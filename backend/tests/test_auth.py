"""Auth JWT maison — hash, jetons, et cloisonnement des runs entre agences.

Deux niveaux :

1. **Unitaire** — Argon2, signature/expiration des jetons, et les gardes
   `get_secret` qui empêchent un démarrage sans secret exploitable.
2. **Bout en bout via `TestClient`** — c'est le seul niveau qui prouve ce qui
   compte vraiment : qu'une agence ne voit pas les runs d'une autre. La règle
   vit dans des dépendances FastAPI, pas dans une fonction qu'on peut appeler à
   la main, donc la tester autrement reviendrait à tester une reformulation.

Postgres, Redis et RQ sont remplacés par des doublures : aucun réseau.

Usage :
    cd backend && .venv/bin/python -m tests.test_auth
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# Doit être posé AVANT d'importer `main` : le lifespan appelle `get_secret()` et
# refuse de démarrer sans. C'est le comportement testé plus bas.
_TEST_SECRET = "secret-de-test-suffisamment-long-pour-passer-le-plancher"
os.environ["JWT_SECRET"] = _TEST_SECRET

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
import main  # noqa: E402
from auth import (  # noqa: E402
    Agency,
    AuthNotConfigured,
    create_access_token,
    get_secret,
    hash_password,
    normalize_email,
    verify_password,
)
from db.agencies import EmailAlreadyUsed  # noqa: E402


def _check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f" — {detail}" if not cond else ""))
    return cond


# ============================================================
# Doublures
# ============================================================


class FakeAgencyRepository:
    """Table `agencies` en mémoire, mêmes contrats que la vraie."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self._next = 0

    async def create(self, *, email: str, name: str, password_hash: str) -> dict[str, Any]:
        if email in self.rows:
            raise EmailAlreadyUsed(email)
        self._next += 1
        row = {
            "id": f"00000000-0000-0000-0000-00000000000{self._next}",
            "email": email,
            "name": name,
            "password_hash": password_hash,
        }
        self.rows[email] = row
        return row

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        return self.rows.get(email)

    async def update_password_hash(self, agency_id: str, password_hash: str) -> None:
        for row in self.rows.values():
            if row["id"] == agency_id:
                row["password_hash"] = password_hash

    async def touch_last_login(self, agency_id: str) -> None:
        return None


class FakeRunRepository:
    """Table `runs` en mémoire, réduite à ce que les routes protégées lisent."""

    enabled = True

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self._next = 0

    async def create_run(self, *, job_id: str, form: dict[str, Any]) -> str:
        self._next += 1
        run_id = f"11111111-0000-0000-0000-00000000000{self._next}"
        self.runs[run_id] = {
            "id": run_id,
            "job_id": job_id,
            "agency_id": form.get("agency_id"),
            "form": form,
        }
        return run_id

    async def get_run_owner(self, run_id: str) -> str | None:
        run = self.runs.get(run_id)
        return run["agency_id"] if run else None

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    async def list_runs(self, *, agency_id: str | None = None, limit: int = 50) -> list:
        return [r for r in self.runs.values() if r["agency_id"] == agency_id]

    async def get_latest_form(self, *, agency_id: str | None = None) -> dict | None:
        for run in reversed(list(self.runs.values())):
            if run["agency_id"] == agency_id:
                return run["form"]
        return None

    async def relink_job(self, run_id: str, job_id: str) -> None:
        if run_id in self.runs:
            self.runs[run_id]["job_id"] = job_id


class FakeBillingRepository:
    """Solde toujours suffisant.

    Ce test porte sur le cloisonnement entre agences, pas sur la facturation :
    l'arithmétique du ledger est vérifiée contre un vrai PostgreSQL dans
    `tests/test_billing.py`. Ici on veut seulement que `/generate` ne se fasse
    pas refuser pour une raison hors sujet.
    """

    def __init__(self) -> None:
        self.trials: list[str] = []

    async def grant_trial(self, agency_id: str) -> None:
        self.trials.append(agency_id)

    async def get_plan_for(self, agency_id: str):
        from billing import get_plan

        return get_plan("agence")

    async def balance_units(self, agency_id: str, plan=None) -> int:
        return 10_000

    async def lots(self, agency_id: str) -> list:
        return []

    async def ledger(self, agency_id: str, limit: int = 50) -> list:
        return []


class FakeJob:
    def __init__(self, job_id: str, args: tuple) -> None:
        self.id = job_id
        self.args = args

    def get_status(self, refresh: bool = False) -> str:
        return "queued"


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: dict[str, FakeJob] = {}

    def enqueue(self, _fn, *args, job_id: str, **kwargs) -> FakeJob:
        job = FakeJob(job_id, args)
        self.jobs[job_id] = job
        return job


_FORM = {
    "product": "Plateforme de trading",
    "description": "Une plateforme de copy-trading pour investisseurs particuliers.",
    "seed_keywords": ["copy trading"],
    "audience": "Investisseurs particuliers",
    "niche": "Finance",
    "num_cocoons": 1,
}


# ============================================================
# 1. Unitaire — mots de passe et jetons
# ============================================================


def test_passwords() -> bool:
    print("\n[1] Mots de passe (Argon2id)")
    ok = True

    h = hash_password("motdepassecorrect")
    ok &= _check("le hash n'est pas le mot de passe en clair", "motdepassecorrect" not in h)
    ok &= _check("format argon2id", h.startswith("$argon2id$"))
    ok &= _check("vérification du bon mot de passe", verify_password(h, "motdepassecorrect")[0])
    ok &= _check("rejet d'un mauvais mot de passe", not verify_password(h, "autre-chose")[0])
    ok &= _check("rejet d'un hash corrompu", not verify_password("pas-un-hash", "x")[0])
    ok &= _check(
        "deux hashs du même mot de passe diffèrent (sel aléatoire)",
        hash_password("identique") != hash_password("identique"),
    )

    ok &= _check("email normalisé en minuscules", normalize_email("  Gaetan@Example.COM ") == "gaetan@example.com")
    return ok


def test_secret_guard() -> bool:
    print("\n[2] Garde sur JWT_SECRET")
    ok = True
    saved = os.environ.get("JWT_SECRET")

    del os.environ["JWT_SECRET"]
    try:
        get_secret()
        ok &= _check("secret absent → AuthNotConfigured", False, "aucune exception levée")
    except AuthNotConfigured as e:
        ok &= _check("secret absent → AuthNotConfigured", "python -c" in str(e), "message sans la commande de génération")

    os.environ["JWT_SECRET"] = "trop-court"
    try:
        get_secret()
        ok &= _check("secret trop court → AuthNotConfigured", False, "aucune exception levée")
    except AuthNotConfigured:
        ok &= _check("secret trop court → AuthNotConfigured", True)

    os.environ["JWT_SECRET"] = saved or _TEST_SECRET
    ok &= _check("secret valide accepté", get_secret() == _TEST_SECRET)
    return ok


def test_tokens() -> bool:
    print("\n[3] Jetons JWT")
    ok = True
    agency = Agency(id="agence-1", email="a@b.fr", name="Agence Un")

    token, expires_at = create_access_token(agency)
    decoded = auth.decode_access_token(token)
    ok &= _check("aller-retour : id conservé", decoded.id == agency.id)
    ok &= _check("aller-retour : nom conservé", decoded.name == agency.name)
    ok &= _check("expiration dans le futur", expires_at > datetime.now(timezone.utc))

    def _rejects(label: str, bad_token: str) -> bool:
        try:
            auth.decode_access_token(bad_token)
            return _check(label, False, "jeton accepté")
        except Exception as e:
            return _check(label, getattr(e, "status_code", None) == 401, f"statut {getattr(e, 'status_code', '?')}")

    ok &= _rejects("signature falsifiée rejetée", token[:-3] + "aaa")
    ok &= _rejects(
        "jeton signé avec un autre secret rejeté",
        jwt.encode({"sub": "agence-1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "un-autre-secret-tout-aussi-long-que-le-vrai", algorithm="HS256"),
    )
    ok &= _rejects(
        "jeton expiré rejeté",
        jwt.encode({"sub": "agence-1", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}, _TEST_SECRET, algorithm="HS256"),
    )
    ok &= _rejects(
        "jeton sans `sub` rejeté",
        jwt.encode({"exp": datetime.now(timezone.utc) + timedelta(hours=1)}, _TEST_SECRET, algorithm="HS256"),
    )
    # `alg: none` est l'attaque classique sur les implémentations JWT naïves.
    ok &= _rejects(
        "jeton non signé (alg=none) rejeté",
        jwt.encode({"sub": "agence-1"}, key="", algorithm="none"),
    )
    return ok


# ============================================================
# 2. Bout en bout — cloisonnement entre agences
# ============================================================


def test_isolation() -> bool:
    print("\n[4] Cloisonnement des runs entre agences (TestClient)")
    ok = True

    agencies = FakeAgencyRepository()
    runs = FakeRunRepository()
    main.get_agency_repository = lambda: agencies
    main.get_repository = lambda: runs

    with TestClient(main.app) as client:
        main.app.state.queue = FakeQueue()

        # -- inscription / connexion ----------------------------------
        r = client.post("/auth/register", json={"email": "A@Agence-Un.fr", "password": "motdepasse-solide", "name": "Agence Un"})
        ok &= _check("inscription acceptée", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
        token_a = r.json().get("access_token", "")
        ok &= _check("email stocké en minuscules", "a@agence-un.fr" in agencies.rows)

        r = client.post("/auth/register", json={"email": "a@agence-un.fr", "password": "motdepasse-solide", "name": "Doublon"})
        ok &= _check("email déjà pris → 409", r.status_code == 409, f"statut {r.status_code}")

        r = client.post("/auth/register", json={"email": "b@agence-deux.fr", "password": "court", "name": "Agence Deux"})
        ok &= _check("mot de passe trop court → 422", r.status_code == 422, f"statut {r.status_code}")

        r = client.post("/auth/register", json={"email": "b@agence-deux.fr", "password": "motdepasse-solide", "name": "Agence Deux"})
        token_b = r.json().get("access_token", "")

        r = client.post("/auth/login", json={"email": "a@agence-un.fr", "password": "mauvais-mot-de-passe"})
        ok &= _check("mauvais mot de passe → 401", r.status_code == 401, f"statut {r.status_code}")
        r = client.post("/auth/login", json={"email": "inconnu@nulle-part.fr", "password": "motdepasse-solide"})
        ok &= _check(
            "email inconnu → même message que mauvais mot de passe (pas d'énumération)",
            r.status_code == 401 and r.json()["detail"] == "Email ou mot de passe incorrect.",
        )
        r = client.post("/auth/login", json={"email": "A@AGENCE-UN.FR", "password": "motdepasse-solide"})
        ok &= _check("connexion insensible à la casse de l'email", r.status_code == 200, f"statut {r.status_code}")

        head_a = {"Authorization": f"Bearer {token_a}"}
        head_b = {"Authorization": f"Bearer {token_b}"}

        # -- routes protégées sans jeton ------------------------------
        for method, path in [
            ("get", "/runs"), ("get", "/form-defaults"), ("get", "/auth/me"),
            ("post", "/generate"), ("get", "/jobs/x"), ("get", "/jobs/x/stream"),
            ("post", "/jobs/x/retry"), ("get", "/runs/x"), ("get", "/runs/x/validation"),
        ]:
            kwargs = {"json": {}} if method == "post" else {}
            r = getattr(client, method)(path, **kwargs)
            ok &= _check(f"{method.upper()} {path} sans jeton → 401", r.status_code == 401, f"statut {r.status_code}")

        r = client.get("/health")
        ok &= _check("/health reste public", r.status_code == 200, f"statut {r.status_code}")

        # -- l'agency_id du client est ignoré -------------------------
        r = client.post("/generate", headers=head_a, json={**_FORM, "agency_id": "je-suis-quelquun-dautre"})
        ok &= _check("génération acceptée", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
        run_a = r.json()["run_id"]
        job_a = r.json()["job_id"]
        ok &= _check(
            "l'agency_id envoyé par le client est écrasé par celui du jeton",
            runs.runs[run_a]["agency_id"] == agencies.rows["a@agence-un.fr"]["id"],
            f"propriétaire enregistré : {runs.runs[run_a]['agency_id']}",
        )

        # -- l'agence B ne voit rien de l'agence A --------------------
        ok &= _check("A voit son run", client.get(f"/runs/{run_a}", headers=head_a).status_code == 200)
        r = client.get(f"/runs/{run_a}", headers=head_b)
        ok &= _check("B ne voit pas le run de A → 404 (pas 403)", r.status_code == 404, f"statut {r.status_code}")
        r = client.get(f"/runs/{run_a}/validation", headers=head_b)
        ok &= _check("B ne lit pas la validation de A → 404", r.status_code == 404, f"statut {r.status_code}")
        # Corps volontairement VALIDE : avec un corps invalide, FastAPI répondrait
        # 422 avant même d'exécuter la route, et le test passerait sans rien
        # prouver du contrôle de propriété.
        valid_decision = {
            "cocoons": [
                {
                    "index": 0,
                    "mother_keyword": "copy trading",
                    "daughter_keywords": ["copy trading avis", "copy trading debutant", "copy trading risques"],
                }
            ]
        }
        r = client.post(f"/runs/{run_a}/validation", headers=head_b, json=valid_decision)
        ok &= _check("B ne soumet pas la validation de A → 404", r.status_code == 404, f"statut {r.status_code}")
        r = client.get(f"/jobs/{job_a}", headers=head_b)
        ok &= _check("B ne suit pas le job de A → 404", r.status_code == 404, f"statut {r.status_code}")
        r = client.post(f"/jobs/{job_a}/retry", headers=head_b)
        ok &= _check("B ne relance pas le job de A → 404", r.status_code == 404, f"statut {r.status_code}")

        ok &= _check("historique de A : 1 run", len(client.get("/runs", headers=head_a).json()["runs"]) == 1)
        ok &= _check("historique de B : vide", client.get("/runs", headers=head_b).json()["runs"] == [])
        ok &= _check("préremplissage de B : vide", client.get("/form-defaults", headers=head_b).json()["form"] is None)
        ok &= _check("préremplissage de A : son formulaire", client.get("/form-defaults", headers=head_a).json()["form"] is not None)

        # -- `agency_id` n'est plus un paramètre de requête ------------
        r = client.get("/runs?agency_id=" + agencies.rows["a@agence-un.fr"]["id"], headers=head_b)
        ok &= _check(
            "?agency_id= ne permet plus de lire l'historique d'autrui",
            r.json()["runs"] == [],
            f"{len(r.json()['runs'])} run(s) fuité(s)",
        )

        # -- SSE : jeton en query, seule route à l'accepter ------------
        r = client.get(f"/jobs/{job_a}/stream?token={token_b}")
        ok &= _check("SSE : jeton de B sur le job de A → 404", r.status_code == 404, f"statut {r.status_code}")
        r = client.get(f"/jobs/{job_a}/stream?token=jeton-bidon")
        ok &= _check("SSE : jeton invalide → 401", r.status_code == 401, f"statut {r.status_code}")
        r = client.get("/runs", params={"token": token_a})
        ok &= _check("le jeton en query ne marche PAS hors SSE", r.status_code == 401, f"statut {r.status_code}")

    return ok


def main_() -> int:
    print("=" * 60)
    print("AUTH — hash, jetons, cloisonnement")
    print("=" * 60)
    ok = test_passwords()
    ok &= test_secret_guard()
    ok &= test_tokens()
    ok &= test_isolation()
    print("\n" + "=" * 60)
    print("TOUS LES CONTRÔLES PASSENT" if ok else "ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_())
