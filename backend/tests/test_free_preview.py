"""Générateur public de structure de cocon — chantier 12.

C'est la seule route du produit accessible sans compte, et la seule où un
inconnu peut nous coûter de l'argent. Deux choses méritent donc d'être prouvées.

**1. La démonstration est vraie.** L'argument de vente est « chaque page reçoit
exactement 5 liens entrants, comptez-les ». Si la page publique affichait ces
chiffres sans qu'ils sortent du vrai code de maillage, on ferait précisément ce
qu'on reproche au marché : une promesse invérifiable. Le test vérifie que le
maillage déduit de la seule structure est arithmétiquement exact.

**2. La dépense est bornée.** Trois plafonds superposés, et un échec **fermé**
si Redis ne répond pas — sans compteur, plus rien ne limite la facture.

Aucun appel réseau.

Usage :
    cd backend && .venv/bin/python -m tests.test_free_preview
"""

from __future__ import annotations

import asyncio
import collections
import sys
from typing import Any

from clients.anthropic_client import MODELS, CompletionResult, UsageTotals
from models import InterCoconPolicy
from pipeline.cocon_builder import CoconBuilder
from pipeline.free_preview import generate_preview
from pipeline.maillage import build_maillage_from_structure
from ratelimit import RateLimitExceeded, Quota, client_ip, enforce_public_quota


def _check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f" — {detail}" if not cond else ""))
    return bool(cond)


PROPOSAL = {
    "theme": "Tout sur le compostage domestique",
    "main_keyword": "composteur de jardin",
    "rationale": "Découpage par usage et par contrainte.",
    "mother": {
        "target_keyword": "composteur de jardin",
        "h1_title": "Composteur de jardin : le guide complet",
        "meta_title": "Composteur de jardin",
        "meta_description": "Tout choisir et réussir son compost.",
        "slug": "composteur-de-jardin",
        "intent": "informational",
    },
    "daughters": [
        {
            "target_keyword": f"compost sujet {i}",
            "h1_title": f"Compost : sujet {i}",
            "meta_title": f"Compost {i}",
            "meta_description": "Description.",
            "slug": f"compost-sujet-{i}",
            "intent": "informational",
        }
        for i in range(1, 6)
    ],
}


class FakeAnthropic:
    def __init__(self, payload: Any = None) -> None:
        self.usage = UsageTotals()
        self.payload = PROPOSAL if payload is None else payload
        self.calls: list[str] = []
        self.models: list[str] = []

    async def complete_json(
        self, *, model: str, system: str, user_prompt: str, max_tokens: int = 4096,
        cached_context: str | None = None,
    ) -> tuple[Any, Any]:
        self.calls.append(user_prompt)
        self.models.append(model)
        result = CompletionResult(
            text="{}",
            model=MODELS[model],
            input_tokens=400,
            output_tokens=800,
            cache_read_tokens=0,
            stop_reason="end_turn",
        )
        self.usage.add(model, result)
        return self.payload, result


class FakeRedis:
    """Compteurs en mémoire — même interface que celle utilisée par le limiteur."""

    def __init__(self, broken: bool = False) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.broken = broken

    def incr(self, key: str) -> int:
        if self.broken:
            raise ConnectionError("redis down")
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds

    def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)


class FakeRequest:
    def __init__(self, headers: dict[str, str], peer: str | None = "10.0.0.1") -> None:
        self.headers = headers

        class _C:
            host = peer

        self.client = _C() if peer else None


# ============================================================
# 1. Le maillage déduit de la seule structure
# ============================================================


def test_maillage_structurel() -> bool:
    print("\n[1] Maillage déduit de la structure, sans aucun contenu")
    ok = True

    cocon = CoconBuilder().build([PROPOSAL])[0]
    maillage = build_maillage_from_structure([cocon], InterCoconPolicy.STRICT)

    outbound = {s: len(v) for s, v in maillage.links.items()}
    inbound: collections.Counter = collections.Counter()
    total = 0
    for links in maillage.links.values():
        for link in links:
            inbound[link.target_slug] += 1
            total += 1

    ok &= _check("6 pages", len(maillage.links) == 6, str(len(maillage.links)))
    ok &= _check("30 liens — n × (n−1)", total == 30, str(total))
    ok &= _check("5 sortants partout", all(n == 5 for n in outbound.values()), str(outbound))
    ok &= _check("5 entrants partout", all(inbound[s] == 5 for s in maillage.links), str(dict(inbound)))
    ok &= _check("aucun lien inter-cocon", not maillage.inter_cocon_links)

    reciproque = all(
        any(x.target_slug == a for x in maillage.links[l.target_slug])
        for a, links in maillage.links.items()
        for l in links
    )
    ok &= _check("tous les liens sont réciproques", reciproque)

    types = collections.Counter(
        l.link_type.value for links in maillage.links.values() for l in links
    )
    ok &= _check(
        "5 mère→fille, 5 fille→mère, 20 sœur↔sœur",
        types["mother_to_daughter"] == 5
        and types["daughter_to_mother"] == 5
        and types["sister_to_sister"] == 20,
        str(dict(types)),
    )

    ancres = [l.anchor_text for links in maillage.links.values() for l in links]
    ok &= _check("toutes les ancres sont renseignées", all(a.strip() for a in ancres))
    return ok


# ============================================================
# 2. Le générateur
# ============================================================


async def test_generateur() -> bool:
    print("\n[2] Génération de l'aperçu")
    ok = True

    fake = FakeAnthropic()
    preview = await generate_preview(
        "composteur de jardin", context="Vente de matériel de jardinage", anthropic=fake
    )

    ok &= _check("un seul appel au modèle", len(fake.calls) == 1, str(len(fake.calls)))
    ok &= _check("… sur Haiku, le tier le moins cher", fake.models == ["haiku"], str(fake.models))
    ok &= _check("le mot-clé est dans le prompt", "composteur de jardin" in fake.calls[0])
    ok &= _check("le contexte fourni est transmis", "matériel de jardinage" in fake.calls[0])
    ok &= _check("30 liens comptés sur la map", preview.total_links == 30, str(preview.total_links))
    ok &= _check("aucune page orpheline", not preview.orphans, str(preview.orphans))
    ok &= _check(
        "les compteurs sont mesurés page par page",
        all(v == 5 for v in preview.inbound_per_page.values())
        and all(v == 5 for v in preview.outbound_per_page.values()),
    )
    ok &= _check("le coût est instrumenté", preview.cost_usd > 0, str(preview.cost_usd))
    ok &= _check(
        "… et reste sous un centime",
        preview.cost_usd < 0.01,
        f"${preview.cost_usd:.5f}",
    )

    # Le contexte est optionnel.
    fake2 = FakeAnthropic()
    await generate_preview("composteur de jardin", context=None, anthropic=fake2)
    ok &= _check("le contexte est optionnel", "Contexte" not in fake2.calls[0])

    # Une proposition inexploitable doit produire un message, pas une page vide.
    # `CoconBuilder` avale silencieusement ce qui est invalide.
    for mauvais, libelle in [
        ({"theme": "x"}, "proposition tronquée"),
        ({**PROPOSAL, "daughters": PROPOSAL["daughters"][:2]}, "moins de 3 filles"),
        ("pas un dict", "réponse non JSON-objet"),
    ]:
        try:
            await generate_preview("x", anthropic=FakeAnthropic(mauvais))
            ok &= _check(f"{libelle} → erreur explicite", False, "aucune exception")
        except ValueError as e:
            ok &= _check(f"{libelle} → erreur explicite", bool(str(e)))
    return ok


# ============================================================
# 3. Les plafonds
# ============================================================


def test_quotas() -> bool:
    print("\n[3] Limitation de débit")
    ok = True

    import os

    os.environ["PUBLIC_HOURLY_LIMIT"] = "3"
    os.environ["PUBLIC_DAILY_LIMIT"] = "5"
    os.environ["PUBLIC_GLOBAL_DAILY_LIMIT"] = "8"

    redis = FakeRedis()
    req = FakeRequest({"x-real-ip": "1.2.3.4"})

    for i in range(3):
        enforce_public_quota(req, redis)
    ok &= _check("3 requêtes passent", True)

    try:
        enforce_public_quota(req, redis)
        ok &= _check("la 4e est refusée", False, "acceptée")
    except RateLimitExceeded as e:
        ok &= _check("la 4e est refusée (plafond horaire)", True)
        ok &= _check("… avec un Retry-After exploitable", e.retry_after > 0, str(e.retry_after))
        ok &= _check("… et un message qui oriente vers l'essai", "compte" in str(e).lower(), str(e))

    # Une autre IP n'est pas affectée par le plafond de la première.
    autre = FakeRequest({"x-real-ip": "5.6.7.8"})
    enforce_public_quota(autre, redis)
    ok &= _check("une autre IP n'est pas pénalisée", True)

    # Le disjoncteur global finit par tomber, quelles que soient les IP.
    redis2 = FakeRedis()
    refuse_global = False
    for i in range(20):
        try:
            enforce_public_quota(FakeRequest({"x-real-ip": f"9.9.9.{i}"}), redis2)
        except RateLimitExceeded as e:
            refuse_global = "quota du jour" in str(e)
            break
    ok &= _check("le disjoncteur global finit par couper", refuse_global)

    # Redis muet → refus, pas laisser-passer.
    try:
        enforce_public_quota(FakeRequest({"x-real-ip": "1.1.1.1"}), FakeRedis(broken=True))
        ok &= _check("Redis en panne → échec FERMÉ", False, "requête acceptée sans compteur")
    except RateLimitExceeded:
        ok &= _check("Redis en panne → échec FERMÉ", True)

    return ok


def test_ip() -> bool:
    print("\n[4] Identification de l'appelant")
    ok = True

    ok &= _check(
        "X-Real-IP est prioritaire (posé par notre nginx, non usurpable)",
        client_ip(FakeRequest({"x-real-ip": "1.2.3.4", "x-forwarded-for": "6.6.6.6"}))
        == "1.2.3.4",
    )
    # nginx AJOUTE à X-Forwarded-For : un client peut en préfixer de fausses,
    # seule la dernière entrée est vraie.
    ok &= _check(
        "sans X-Real-IP, on lit X-Forwarded-For par la DROITE",
        client_ip(FakeRequest({"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3"}))
        == "3.3.3.3",
    )
    ok &= _check(
        "sans en-tête, on retombe sur le pair TCP",
        client_ip(FakeRequest({}, peer="10.0.0.9")) == "10.0.0.9",
    )
    ok &= _check(
        "sans rien du tout, une valeur neutre",
        client_ip(FakeRequest({}, peer=None)) == "inconnu",
    )
    return ok


# ============================================================


def main_() -> int:
    print("=" * 60)
    print("GÉNÉRATEUR PUBLIC DE COCON (chantier 12)")
    print("=" * 60)

    ok = test_maillage_structurel()
    ok &= asyncio.run(test_generateur())
    ok &= test_quotas()
    ok &= test_ip()

    print("\n" + "=" * 60)
    print("TOUS LES CONTRÔLES PASSENT" if ok else "ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_())
