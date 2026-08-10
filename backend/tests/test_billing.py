"""Ledger de cocons — arithmétique du solde, débits, remboursements, report.

⚠️ **Ce test tourne contre un vrai PostgreSQL**, démarré en mémoire par
`pgserver` (binaire embarqué, dépendance de développement uniquement). C'est
délibéré : l'essentiel de la logique de facturation *est* du SQL — verrou
`for update`, consommation FIFO sur plusieurs lots, contraintes d'unicité,
transactions. Une doublure en mémoire testerait la doublure, pas le ledger, et
c'est précisément le genre de code où une erreur se paie en euros.

Si `pgserver` n'est pas installé, le test s'annonce ignoré plutôt que d'échouer :
il ne doit pas bloquer une machine qui n'a que les dépendances de production.

Usage :
    cd backend && .venv/bin/python -m tests.test_billing
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from billing import (
    InsufficientBalance,
    PLANS,
    UNITS_PER_COCOON,
    cocoons_to_units,
    format_cocoons,
    get_plan,
    period_bounds,
    period_key,
)


def _check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f" — {detail}" if not cond else ""))
    return cond


# ============================================================
# 1. Partie pure — pas de base
# ============================================================


def test_pure() -> bool:
    print("\n[1] Unités, formules, périodes")
    ok = True

    ok &= _check("1 cocon = 6 unités", cocoons_to_units(1) == 6)
    ok &= _check("un article = 1 unité", UNITS_PER_COCOON == 6)

    ok &= _check("0 → « 0 cocon »", format_cocoons(0) == "0 cocon")
    ok &= _check("6 → « 1 cocon »", format_cocoons(6) == "1 cocon")
    ok &= _check("12 → « 2 cocons »", format_cocoons(12) == "2 cocons")
    ok &= _check("1 → « 1/6 de cocon »", format_cocoons(1) == "1/6 de cocon")
    ok &= _check("13 → « 2 + 1/6 cocons »", format_cocoons(13) == "2 + 1/6 cocons")

    ok &= _check("plan inconnu → essai", get_plan("n-importe-quoi").key == "trial")
    ok &= _check("plan absent → essai", get_plan(None).key == "trial")
    ok &= _check("Agence = 20 cocons = 120 unités", PLANS["agence"].units_per_month == 120)

    # Le lot d'une période vit DEUX périodes : c'est ce qui implémente le report
    # d'un mois plafonné à 1× l'allocation, sans code de report.
    start, expiry = period_bounds("2026-08")
    ok &= _check("période 2026-08 démarre le 1er août", start.month == 8 and start.day == 1)
    ok &= _check("… et expire le 1er octobre (2 périodes)", expiry.year == 2026 and expiry.month == 10)
    _, expiry = period_bounds("2026-11")
    ok &= _check("passage d'année : 2026-11 expire en 2027-01", expiry.year == 2027 and expiry.month == 1)
    _, expiry = period_bounds("2026-12")
    ok &= _check("2026-12 expire en 2027-02", expiry.year == 2027 and expiry.month == 2)

    ok &= _check("clé de période au format YYYY-MM", len(period_key()) == 7 and period_key()[4] == "-")
    return ok


# ============================================================
# 2. Intégration — vrai Postgres
# ============================================================


async def _run_db_tests(dsn: str) -> bool:
    # Import tardif : ces modules lisent DATABASE_URL au premier usage.
    import db.postgres as pg
    from db.billing import BillingRepository

    pg._repository = pg.RunRepository(dsn)
    repo = pg.RunRepository(dsn)
    pg._repository = repo
    billing = BillingRepository()
    pool = await repo.require_pool()  # applique schema.sql au passage

    ok = True
    print("\n[2] Ledger contre un PostgreSQL réel")

    async def new_agency(email: str, plan: str = "trial") -> str:
        async with pool.acquire() as conn:
            return str(
                await conn.fetchval(
                    "insert into agencies (email, name, password_hash, plan) "
                    "values ($1, $1, 'x', $2) returning id",
                    email,
                    plan,
                )
            )

    async def balance(agency_id: str) -> int:
        return await billing.balance_units(agency_id)

    # -- essai ------------------------------------------------------
    a = await new_agency("essai@x.fr")
    ok &= _check("nouveau compte : solde nul", await balance(a) == 0)

    await billing.grant_trial(a)
    ok &= _check("essai = 3 cocons = 18 unités", await balance(a) == 18, f"{await balance(a)}")
    await billing.grant_trial(a)
    ok &= _check(
        "second appel à grant_trial sans effet (idempotent)",
        await balance(a) == 18,
        f"{await balance(a)} — l'essai a été accordé deux fois",
    )

    # -- débit de génération ----------------------------------------
    run1 = "aaaaaaaa-0000-0000-0000-000000000001"
    debited = await billing.debit_generation(
        agency_id=a, run_id=run1, cocoons=2, plan=get_plan("trial")
    )
    ok &= _check("débit de 2 cocons = 12 unités", debited == 12, f"{debited}")
    ok &= _check("solde restant : 1 cocon", await balance(a) == 6, f"{await balance(a)}")

    # LA règle : une reprise sur checkpoint ne re-débite jamais.
    again = await billing.debit_generation(
        agency_id=a, run_id=run1, cocoons=2, plan=get_plan("trial")
    )
    ok &= _check("reprise sur le même run : aucun débit", again == 0, f"{again} unités débitées")
    ok &= _check("… et solde inchangé", await balance(a) == 6, f"{await balance(a)}")

    # -- remboursement ----------------------------------------------
    refunded = await billing.refund_run(run1)
    ok &= _check("run échoué remboursé de 12 unités", refunded == 12, f"{refunded}")
    ok &= _check("solde revenu à 18", await balance(a) == 18, f"{await balance(a)}")
    ok &= _check("remboursement rejoué : sans effet", await billing.refund_run(run1) == 0)
    ok &= _check("… et solde toujours 18", await balance(a) == 18, f"{await balance(a)}")

    # L'articulation des deux règles : après remboursement, la reprise redébite.
    # Sinon le run serait offert.
    redebited = await billing.debit_generation(
        agency_id=a, run_id=run1, cocoons=2, plan=get_plan("trial")
    )
    ok &= _check("reprise APRÈS remboursement : re-débit", redebited == 12, f"{redebited}")
    ok &= _check("solde de nouveau à 6", await balance(a) == 6, f"{await balance(a)}")

    # -- solde insuffisant ------------------------------------------
    try:
        await billing.debit_generation(
            agency_id=a,
            run_id="aaaaaaaa-0000-0000-0000-000000000002",
            cocoons=4,
            plan=get_plan("trial"),
        )
        ok &= _check("solde insuffisant → exception", False, "aucune exception")
    except InsufficientBalance as e:
        ok &= _check("solde insuffisant → InsufficientBalance", True)
        ok &= _check("… message chiffré en cocons", "cocon" in str(e), str(e))
        ok &= _check(
            "… et rien n'a été consommé (transaction annulée)",
            await balance(a) == 6,
            f"{await balance(a)}",
        )

    # -- régénération : 1/6, et non idempotente ---------------------
    before = await balance(a)
    await billing.debit_regeneration(
        agency_id=a, run_id=run1, articles=1, plan=get_plan("trial")
    )
    ok &= _check("régénération d'1 article = 1 unité", await balance(a) == before - 1)
    await billing.debit_regeneration(
        agency_id=a, run_id=run1, articles=1, plan=get_plan("trial")
    )
    ok &= _check(
        "seconde régénération débitée aussi (non idempotent)",
        await balance(a) == before - 2,
        f"{await balance(a)} au lieu de {before - 2}",
    )
    ok &= _check(
        "une régénération n'est pas remboursée par refund_run",
        await billing.refund_run(run1) == 12 and await balance(a) == before - 2 + 12,
        f"solde {await balance(a)}",
    )

    # -- FIFO : on brûle d'abord ce qui expire le plus tôt ----------
    b = await new_agency("fifo@x.fr")
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into cocoon_lots (agency_id, kind, granted_units, remaining_units, expires_at) "
            "values ($1::uuid, 'purchase', 6, 6, null)",
            b,
        )
        await conn.execute(
            "insert into cocoon_lots (agency_id, kind, granted_units, remaining_units, expires_at) "
            "values ($1::uuid, 'subscription', 6, 6, $2)",
            b,
            now + timedelta(days=5),
        )
    await billing.debit_generation(
        agency_id=b, run_id="bbbbbbbb-0000-0000-0000-000000000001", cocoons=1,
        plan=get_plan("trial"),
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select kind, remaining_units from cocoon_lots where agency_id = $1::uuid", b
        )
    by_kind = {r["kind"]: r["remaining_units"] for r in rows}
    ok &= _check(
        "le lot qui expire est consommé avant l'achat perpétuel",
        by_kind.get("subscription") == 0 and by_kind.get("purchase") == 6,
        f"{by_kind}",
    )

    # -- débit à cheval sur deux lots -------------------------------
    c = await new_agency("cheval@x.fr")
    async with pool.acquire() as conn:
        for days in (5, 40):
            await conn.execute(
                "insert into cocoon_lots (agency_id, kind, granted_units, remaining_units, expires_at) "
                "values ($1::uuid, 'subscription', 6, 6, $2)",
                c,
                now + timedelta(days=days),
            )
    await billing.debit_generation(
        agency_id=c, run_id="cccccccc-0000-0000-0000-000000000001", cocoons=2,
        plan=get_plan("trial"),
    )
    ok &= _check("débit réparti sur 2 lots : solde nul", await balance(c) == 0, f"{await balance(c)}")
    async with pool.acquire() as conn:
        lines = await conn.fetchval(
            "select count(*) from cocoon_ledger where run_id = $1::uuid and kind = 'debit_generation'",
            "cccccccc-0000-0000-0000-000000000001",
        )
    ok &= _check("… et une ligne de journal par lot touché", lines == 2, f"{lines} ligne(s)")
    ok &= _check(
        "remboursement d'un débit réparti : tout revient",
        await billing.refund_run("cccccccc-0000-0000-0000-000000000001") == 12
        and await balance(c) == 12,
        f"solde {await balance(c)}",
    )

    # -- lot expiré : hors solde ------------------------------------
    d = await new_agency("expire@x.fr")
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into cocoon_lots (agency_id, kind, granted_units, remaining_units, expires_at) "
            "values ($1::uuid, 'subscription', 12, 12, $2)",
            d,
            now - timedelta(days=1),
        )
    ok &= _check("un lot expiré ne compte pas dans le solde", await balance(d) == 0, f"{await balance(d)}")

    # -- allocation mensuelle et report ------------------------------
    e = await new_agency("agence@x.fr", plan="agence")
    plan = await billing.get_plan_for(e)
    ok &= _check("formule lue en base", plan.key == "agence", plan.key)

    await billing.ensure_period_grant(e, plan)
    ok &= _check("allocation du mois : 20 cocons = 120 unités", await balance(e) == 120, f"{await balance(e)}")
    await billing.ensure_period_grant(e, plan)
    ok &= _check(
        "second appel dans le même mois : aucun double crédit",
        await balance(e) == 120,
        f"{await balance(e)}",
    )

    # Le report : on simule le mois précédent non consommé. Deux lots vivants
    # au maximum, donc le report est plafonné à 1× l'allocation par construction.
    async with pool.acquire() as conn:
        previous = period_bounds(period_key())[0] - timedelta(days=1)
        prev_key = f"{previous.year:04d}-{previous.month:02d}"
        _, prev_expiry = period_bounds(prev_key)
        await conn.execute(
            "insert into cocoon_lots (agency_id, kind, period_key, granted_units, remaining_units, expires_at) "
            "values ($1::uuid, 'subscription', $2, 120, 120, $3)",
            e,
            prev_key,
            prev_expiry,
        )
    ok &= _check(
        "mois précédent non consommé reporté : 240 unités",
        await balance(e) == 240,
        f"{await balance(e)}",
    )
    ok &= _check(
        "report plafonné à 1× l'allocation (2 lots vivants au plus)",
        await balance(e) == 2 * plan.units_per_month,
    )

    # Un lot d'il y a deux mois est déjà expiré : il ne s'ajoute pas.
    async with pool.acquire() as conn:
        old = period_bounds(prev_key)[0] - timedelta(days=1)
        old_key = f"{old.year:04d}-{old.month:02d}"
        _, old_expiry = period_bounds(old_key)
        await conn.execute(
            "insert into cocoon_lots (agency_id, kind, period_key, granted_units, remaining_units, expires_at) "
            "values ($1::uuid, 'subscription', $2, 120, 120, $3)",
            e,
            old_key,
            old_expiry,
        )
    ok &= _check(
        "l'allocation d'il y a deux mois est expirée, pas cumulée",
        await balance(e) == 240,
        f"{await balance(e)} — le report dépasse le plafond",
    )

    # -- achat à l'unité ---------------------------------------------
    f = await new_agency("carte@x.fr")
    await billing.grant_purchase(f, 1, note="Test")
    ok &= _check("achat à l'unité crédite 6 unités", await balance(f) == 6, f"{await balance(f)}")
    await billing.grant_purchase(f, 1)
    ok &= _check("un second achat s'ajoute (non idempotent)", await balance(f) == 12, f"{await balance(f)}")
    entries = await billing.ledger(f)
    ok &= _check("journal : 2 lignes d'octroi", len(entries) == 2, f"{len(entries)}")

    # -- concurrence : deux débits simultanés ------------------------
    # Sans `for update` dans `_consume`, les deux liraient le même solde et
    # passeraient tous les deux — c'est le bug classique du solde partagé.
    g = await new_agency("course@x.fr")
    await billing.grant_purchase(g, 1)  # exactement 1 cocon
    results = await asyncio.gather(
        billing.debit_generation(
            agency_id=g, run_id="dddddddd-0000-0000-0000-000000000001", cocoons=1,
            plan=get_plan("trial"),
        ),
        billing.debit_generation(
            agency_id=g, run_id="dddddddd-0000-0000-0000-000000000002", cocoons=1,
            plan=get_plan("trial"),
        ),
        return_exceptions=True,
    )
    refused = [r for r in results if isinstance(r, InsufficientBalance)]
    ok &= _check(
        "deux débits concurrents sur 1 cocon : un seul passe",
        len(refused) == 1 and await balance(g) == 0,
        f"résultats={results} solde={await balance(g)}",
    )

    await repo.close()
    return ok


# ============================================================
# 3. L'API : essai à l'inscription, 402 quand le solde manque
# ============================================================


def _run_api_tests(dsn: str) -> bool:
    os.environ.setdefault("JWT_SECRET", "secret-de-test-suffisamment-long-pour-passer")
    os.environ["DATABASE_URL"] = dsn

    import db.postgres as pg
    from fastapi.testclient import TestClient

    pg._repository = pg.RunRepository(dsn)
    import main

    main.get_repository = pg.get_repository

    class FakeJob:
        def __init__(self, job_id: str, args: tuple) -> None:
            self.id, self.args = job_id, args

        def get_status(self, refresh: bool = False) -> str:
            return "queued"

    class FakeQueue:
        def enqueue(self, _fn, *args, job_id: str, **kwargs) -> FakeJob:
            return FakeJob(job_id, args)

    ok = True
    print("\n[3] API — essai, solde, refus 402")

    form = {
        "product": "Plateforme de trading",
        "description": "Une plateforme de copy-trading pour investisseurs particuliers.",
        "seed_keywords": ["copy trading"],
        "audience": "Investisseurs particuliers",
        "niche": "Finance",
    }

    with TestClient(main.app) as client:
        main.app.state.queue = FakeQueue()

        r = client.post(
            "/auth/register",
            json={"email": "api@agence.fr", "password": "motdepasse-solide", "name": "API"},
        )
        ok &= _check("inscription", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
        head = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = client.get("/billing/balance", headers=head)
        body = r.json()
        ok &= _check(
            "l'inscription accorde l'essai : 3 cocons",
            body.get("balance_units") == 18 and body.get("balance_cocoons") == 3.0,
            f"{body}",
        )
        ok &= _check("formule par défaut : essai", body.get("plan") == "trial", body.get("plan"))
        ok &= _check("libellé lisible", body.get("balance_label") == "3 cocons", body.get("balance_label"))

        # 3 cocons demandés sur 3 disponibles : ça passe, et rien n'est débité
        # (règle « débit à la génération, jamais à la soumission »).
        r = client.post("/generate", headers=head, json={**form, "num_cocoons": 3})
        ok &= _check("génération de 3 cocons acceptée", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
        after = client.get("/billing/balance", headers=head).json()["balance_units"]
        ok &= _check(
            "la soumission ne débite RIEN (recherche de mots-clés offerte)",
            after == 18,
            f"solde tombé à {after}",
        )

        # 4 cocons demandés sur 3 disponibles.
        r = client.post("/generate", headers=head, json={**form, "num_cocoons": 4})
        ok &= _check("solde insuffisant → 402", r.status_code == 402, f"statut {r.status_code}")
        detail = r.json()
        ok &= _check(
            "… le 402 chiffre le manque",
            detail.get("required_units") == 24 and detail.get("available_units") == 18,
            f"{detail}",
        )

        r = client.get("/billing/ledger", headers=head)
        ok &= _check("journal accessible", r.status_code == 200, f"statut {r.status_code}")
        r = client.get("/billing/balance")
        ok &= _check("solde non lisible sans jeton → 401", r.status_code == 401, f"statut {r.status_code}")

        # -- second contrôle, à la validation ------------------------
        # Le contrôle de /generate portait sur `num_cocoons` du formulaire ;
        # rien n'empêche l'agence d'ajouter des cocons sur l'écran de
        # validation. Sans ce garde-fou, le dépassement n'exploserait qu'au
        # débit, dans le worker : l'agence verrait un run planté au lieu d'un
        # message sur son solde. Les appels LLM du chemin de validation sont
        # court-circuités — c'est le contrôle de solde qu'on teste, pas eux.
        ok &= _check_validation_gate(client, main, head, form, dsn)
        ok &= _check_stripe(client, main, head)

    return ok


def _check_stripe(client, main, head: dict) -> bool:
    """Webhooks Stripe — la seule voie par laquelle de l'argent entre.

    Stripe livre « au moins une fois » et rejoue ses événements après un timeout.
    Un achat crédité deux fois est un cocon offert : c'est le contrôle central
    de cette section.
    """
    import asyncio as _asyncio

    ok = True
    print("\n[4] Paiement Stripe")

    def balance() -> int:
        return client.get("/billing/balance", headers=head).json()["balance_units"]

    agency_id = client.get("/auth/me", headers=head).json()["id"]

    def purchase_event(event_id: str, cocoons: int = 2) -> dict:
        return {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "mode": "payment",
                    "payment_status": "paid",
                    "customer": "cus_test",
                    "client_reference_id": agency_id,
                    "metadata": {"agency_id": agency_id, "cocoons": str(cocoons)},
                }
            },
        }

    def handle(event: dict) -> dict:
        return _asyncio.run(main._handle_stripe_event(event))

    # -- achat crédité une fois -------------------------------------
    before = balance()
    res = handle(purchase_event("evt_achat_1"))
    ok &= _check("achat Stripe crédité", res.get("status") == "crédité", f"{res}")
    ok &= _check("+ 2 cocons au solde", balance() == before + 12, f"{balance()} au lieu de {before + 12}")

    # LE contrôle : Stripe rejoue, on ne crédite pas deux fois.
    after = balance()
    res = handle(purchase_event("evt_achat_1"))
    ok &= _check("même événement rejoué → déjà traité", res.get("status") == "déjà traité", f"{res}")
    ok &= _check("… et solde INCHANGÉ", balance() == after, f"{balance()} — cocons offerts")

    # Mais deux achats distincts identiques sont légitimes : la déduplication
    # porte sur l'identifiant d'événement, pas sur le contenu.
    res = handle(purchase_event("evt_achat_2"))
    ok &= _check(
        "achat distinct au contenu identique → crédité",
        res.get("status") == "crédité" and balance() == after + 12,
        f"{res} solde={balance()}",
    )

    # -- cas dégradés qui ne doivent rien créditer ------------------
    unpaid = purchase_event("evt_impaye")
    unpaid["data"]["object"]["payment_status"] = "unpaid"
    before = balance()
    res = handle(unpaid)
    ok &= _check("paiement non abouti → ignoré", res.get("status") == "ignoré", f"{res}")
    ok &= _check("… et rien crédité", balance() == before)

    orphan = purchase_event("evt_orphelin")
    orphan["data"]["object"]["metadata"] = {"cocoons": "3"}
    orphan["data"]["object"]["client_reference_id"] = None
    orphan["data"]["object"]["customer"] = "cus_inconnu"
    res = handle(orphan)
    ok &= _check("achat non rattachable → ignoré sans planter", res.get("status") == "ignoré", f"{res}")

    # -- abonnement -------------------------------------------------
    def subscription_event(event_id: str, status: str, plan: str, kind: str) -> dict:
        return {
            "id": event_id,
            "type": kind,
            "data": {
                "object": {
                    "status": status,
                    "customer": "cus_test",
                    "metadata": {"agency_id": agency_id, "plan": plan},
                }
            },
        }

    before = balance()
    res = handle(
        subscription_event("evt_sub_1", "active", "agence", "customer.subscription.created")
    )
    ok &= _check("abonnement actif → formule appliquée", res.get("plan") == "agence", f"{res}")
    ok &= _check(
        "… et l'allocation du mois créditée tout de suite (120 unités)",
        balance() == before + 120,
        f"{balance()} au lieu de {before + 120}",
    )
    ok &= _check(
        "formule visible sur /billing/balance",
        client.get("/billing/balance", headers=head).json()["plan"] == "agence",
    )

    after_sub = balance()
    res = handle(
        subscription_event("evt_sub_1", "active", "agence", "customer.subscription.created")
    )
    ok &= _check("abonnement rejoué → déjà traité", res.get("status") == "déjà traité", f"{res}")
    ok &= _check("… et pas de double allocation", balance() == after_sub, f"{balance()}")

    # Résiliation : la formule retombe, mais les cocons déjà payés restent.
    res = handle(
        subscription_event("evt_sub_del", "canceled", "agence", "customer.subscription.deleted")
    )
    ok &= _check("résiliation traitée", res.get("status") == "abonnement résilié", f"{res}")
    ok &= _check(
        "… formule revenue à l'essai",
        client.get("/billing/balance", headers=head).json()["plan"] == "trial",
    )
    ok &= _check(
        "… mais les cocons déjà payés ne sont PAS repris",
        balance() == after_sub,
        f"{balance()} au lieu de {after_sub}",
    )

    # -- routes -----------------------------------------------------
    r = client.get("/billing/offers", headers=head)
    ok &= _check("offres lisibles", r.status_code == 200, f"statut {r.status_code}")
    ok &= _check(
        "… Stripe annoncé non configuré (aucune clé dans ce test)",
        r.json().get("payments_enabled") is False,
        f"{r.json().get('payments_enabled')}",
    )
    ok &= _check(
        "… 3 formules payantes proposées, l'essai exclu",
        [p["key"] for p in r.json()["plans"]] == ["independant", "agence", "studio"],
        f"{[p['key'] for p in r.json()['plans']]}",
    )

    r = client.post("/billing/checkout", headers=head, json={"plan": "agence"})
    ok &= _check("checkout sans clé Stripe → 503 explicite", r.status_code == 503, f"statut {r.status_code}")
    r = client.post("/billing/checkout", headers=head, json={"plan": "agence", "cocoons": 2})
    ok &= _check("checkout ambigu (formule ET cocons) → 422", r.status_code == 422, f"statut {r.status_code}")
    r = client.post("/billing/checkout", headers=head, json={})
    ok &= _check("checkout vide → 422", r.status_code == 422, f"statut {r.status_code}")

    r = client.post("/billing/webhook", json={"id": "evt_x", "type": "ping"})
    ok &= _check(
        "webhook sans signature vérifiable → refusé (jamais traité)",
        r.status_code in (400, 503),
        f"statut {r.status_code}",
    )
    return ok


class _FakeCocon:
    """Juste ce que la route de validation manipule d'un CoconStructure."""

    def __init__(self, index: int) -> None:
        self.daughters = [object()] * 5
        self._index = index

    def model_dump(self, mode: str = "json") -> dict:
        return {"index": self._index}


def _check_validation_gate(client, main, head: dict, form: dict, dsn: str) -> bool:
    """Vérifie le 402 rendu par POST /runs/{id}/validation."""
    import json as _json

    import asyncpg

    from workers.pipeline_job import VALIDATION_CHECKPOINT

    ok = True
    wanted = 5  # 5 cocons validés alors que l'essai n'en donne que 3

    r = client.post("/generate", headers=head, json={**form, "num_cocoons": 1})
    run_id = r.json()["run_id"]

    async def _seed() -> None:
        # Connexion dédiée, et surtout PAS le pool du repository : celui-ci est
        # attaché à la boucle d'événements du TestClient, s'en servir depuis un
        # `asyncio.run()` échoue — et `RunRepository._execute` avale l'erreur,
        # donc l'écriture disparaîtrait en silence.
        conn = await asyncpg.connect(dsn)
        try:
            for step, payload in (
                (VALIDATION_CHECKPOINT, {"proposals": []}),
                ("keyword_research", {"keywords": [], "proposals": []}),
            ):
                await conn.execute(
                    "insert into run_checkpoints (run_id, step, payload) "
                    "values ($1::uuid, $2, $3::jsonb)",
                    run_id,
                    step,
                    _json.dumps(payload),
                )
        finally:
            await conn.close()

    asyncio.run(_seed())

    saved_decision, saved_builder = main.apply_decision, main.CoconBuilder

    async def _fake_decision(*args, **kwargs):
        return []

    class _FakeBuilder:
        def build(self, _proposals):
            return [_FakeCocon(i) for i in range(wanted)]

    main.apply_decision = _fake_decision
    main.CoconBuilder = _FakeBuilder
    try:
        decision = {
            "cocoons": [
                {
                    "index": i,
                    "mother_keyword": f"mere {i}",
                    "daughter_keywords": [f"fille {i}-{j}" for j in range(3)],
                }
                for i in range(wanted)
            ]
        }
        r = client.post(f"/runs/{run_id}/validation", headers=head, json=decision)
        ok &= _check(
            "validation de 5 cocons avec 3 au solde → 402",
            r.status_code == 402,
            f"statut {r.status_code} {r.text[:150]}",
        )
        ok &= _check(
            "… et le manque est chiffré (30 requis, 18 disponibles)",
            r.json().get("required_units") == 30 and r.json().get("available_units") == 18,
            f"{r.json()}",
        )
        ok &= _check(
            "… et rien n'a été débité",
            client.get("/billing/balance", headers=head).json()["balance_units"] == 18,
        )
    finally:
        main.apply_decision, main.CoconBuilder = saved_decision, saved_builder

    return ok


# ============================================================


def main_() -> int:
    print("=" * 60)
    print("FACTURATION — ledger de cocons")
    print("=" * 60)

    ok = test_pure()

    try:
        import pgserver
    except ImportError:
        print("\n[2] Ledger contre PostgreSQL — IGNORÉ")
        print("    `pgserver` n'est pas installé (dépendance de développement).")
        print("    Installer avec : .venv/bin/pip install pgserver")
        print("    ⚠️ Sans lui, TOUTE la logique SQL du ledger est non testée.")
        print("\n" + "=" * 60)
        print("CONTRÔLES PURS OK — INTÉGRATION NON EXÉCUTÉE" if ok else "ÉCHEC")
        return 0 if ok else 1

    tmp = Path(tempfile.mkdtemp(prefix="cocon-billing-"))
    try:
        server = pgserver.get_server(tmp / "pgdata")
        dsn = server.get_uri()
        os.environ["DATABASE_URL"] = dsn
        ok &= asyncio.run(_run_db_tests(dsn))
        ok &= _run_api_tests(dsn)
    finally:
        try:
            server.cleanup()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print("TOUS LES CONTRÔLES PASSENT" if ok else "ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_())
