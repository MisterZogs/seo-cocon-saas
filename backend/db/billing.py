"""Solde de cocons dans Postgres — lots, débits, remboursements.

Comme `db/agencies.py` et **à l'inverse de `db/postgres.py`** : aucune erreur
n'est avalée. Une erreur SQL avalée dans l'historique fait perdre une ligne de
journal ; ici elle offre une génération.

Tout ce qui touche au solde passe par une transaction avec `select ... for
update` sur les lots de l'agence. Sans ce verrou, deux générations lancées
simultanément liraient le même solde et le dépasseraient toutes les deux.

Modèle : le solde est la somme des `remaining_units` des lots **vivants**, pas
un repli sur l'historique du journal. Le journal explique le solde, il ne le
définit pas — un solde calculé par agrégation de tout l'historique devient faux
dès qu'un lot expire sans avoir été consommé.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from billing import (
    InsufficientBalance,
    Plan,
    TRIAL_COCOONS,
    TRIAL_VALIDITY_DAYS,
    cocoons_to_units,
    get_plan,
    period_bounds,
    period_key,
)
from db.postgres import get_repository

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BillingRepository:
    async def _pool(self) -> Any:
        return await get_repository().require_pool()

    # ------------------------------------------------------------------
    # Octrois
    # ------------------------------------------------------------------

    async def grant_trial(self, agency_id: str) -> None:
        """Lot d'essai à l'inscription : 3 cocons, sans carte bancaire.

        `period_key = 'trial'` plutôt que null : l'index unique partiel sur
        (agency_id, period_key) empêche alors un second lot d'essai, y compris
        si la route d'inscription est rejouée.
        """
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                insert into cocoon_lots
                    (agency_id, kind, period_key, granted_units, remaining_units, expires_at)
                values ($1::uuid, 'trial', 'trial', $2, $2, $3)
                on conflict (agency_id, period_key) where period_key is not null
                do nothing
                """,
                agency_id,
                cocoons_to_units(TRIAL_COCOONS),
                _now() + timedelta(days=TRIAL_VALIDITY_DAYS),
            )

    async def ensure_period_grant(self, agency_id: str, plan: Plan) -> None:
        """Crée le lot d'abonnement de la période courante s'il manque.

        L'octroi est **paresseux** : déclenché à la lecture du solde plutôt que
        par une tâche planifiée. Une tâche cron aurait besoin d'un ordonnanceur
        qui n'existe pas encore dans l'infra, et se rattraperait mal après une
        panne ; ici, la première consultation du mois suffit et l'index unique
        sur (agency_id, period_key) rend l'opération rejouable sans risque.
        """
        if plan.units_per_month <= 0:
            return  # l'essai n'a pas d'allocation récurrente

        key = period_key()
        _, expires_at = period_bounds(key)
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                insert into cocoon_lots
                    (agency_id, kind, period_key, granted_units, remaining_units, expires_at)
                values ($1::uuid, 'subscription', $2, $3, $3, $4)
                on conflict (agency_id, period_key) where period_key is not null
                do nothing
                """,
                agency_id,
                key,
                plan.units_per_month,
                expires_at,
            )

    async def grant_purchase(self, agency_id: str, cocoons: int, note: str | None = None) -> None:
        """Achat à l'unité (20 € le cocon). N'expire pas : il est payé.

        Pas d'idempotence ici — chaque achat est un événement distinct. Le jour
        où un prestataire de paiement enverra des webhooks, c'est son
        identifiant d'événement qui devra porter l'idempotence, pas cette table.
        """
        pool = await self._pool()
        units = cocoons_to_units(cocoons)
        async with pool.acquire() as conn:
            async with conn.transaction():
                lot_id = await conn.fetchval(
                    """
                    insert into cocoon_lots
                        (agency_id, kind, granted_units, remaining_units, expires_at)
                    values ($1::uuid, 'purchase', $2, $2, null)
                    returning id
                    """,
                    agency_id,
                    units,
                )
                await conn.execute(
                    """
                    insert into cocoon_ledger (agency_id, lot_id, kind, delta_units, note)
                    values ($1::uuid, $2, 'grant', $3, $4)
                    """,
                    agency_id,
                    lot_id,
                    units,
                    note or f"Achat à l'unité : {cocoons} cocon(s)",
                )

    # ------------------------------------------------------------------
    # Solde
    # ------------------------------------------------------------------

    async def balance_units(self, agency_id: str, plan: Plan | None = None) -> int:
        """Solde disponible, en unités. Crée au passage l'allocation du mois."""
        if plan is not None:
            await self.ensure_period_grant(agency_id, plan)
        pool = await self._pool()
        async with pool.acquire() as conn:
            total = await conn.fetchval(
                """
                select coalesce(sum(remaining_units), 0) from cocoon_lots
                where agency_id = $1::uuid
                  and remaining_units > 0
                  and (expires_at is null or expires_at > now())
                """,
                agency_id,
            )
        return int(total or 0)

    async def lots(self, agency_id: str) -> list[dict[str, Any]]:
        """Lots vivants, dans l'ordre où ils seront consommés."""
        pool = await self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select id, kind, period_key, granted_units, remaining_units,
                       granted_at, expires_at
                from cocoon_lots
                where agency_id = $1::uuid
                  and remaining_units > 0
                  and (expires_at is null or expires_at > now())
                order by expires_at asc nulls last, granted_at asc
                """,
                agency_id,
            )
        return [_serialize(r) for r in rows]

    async def ledger(self, agency_id: str, limit: int = 50) -> list[dict[str, Any]]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select id, lot_id, run_id, kind, delta_units, reversed_at, note, created_at
                from cocoon_ledger
                where agency_id = $1::uuid
                order by created_at desc
                limit $2
                """,
                agency_id,
                limit,
            )
        return [_serialize(r) for r in rows]

    # ------------------------------------------------------------------
    # Débits
    # ------------------------------------------------------------------

    async def debit_generation(
        self, *, agency_id: str, run_id: str, cocoons: int, plan: Plan
    ) -> int:
        """Débite `cocoons` pour un run. Idempotent tant que le débit tient.

        « Idempotent tant que le débit tient » et non « une fois pour toutes » :
        une reprise sur checkpoint ne re-débite pas (il existe déjà un débit non
        annulé pour ce run), mais une reprise **après remboursement** re-débite,
        puisque le remboursement a annulé le précédent. Le total reste d'un
        débit par génération effectivement livrée, et les deux règles produit
        — « la reprise ne re-débite jamais » et « un run échoué est remboursé »
        — cessent de se contredire.

        Retourne le nombre d'unités effectivement débitées (0 si déjà débité).
        """
        units = cocoons_to_units(cocoons)
        await self.ensure_period_grant(agency_id, plan)

        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                already = await conn.fetchval(
                    """
                    select 1 from cocoon_ledger
                    where run_id = $1::uuid
                      and kind = 'debit_generation'
                      and reversed_at is null
                    limit 1
                    """,
                    run_id,
                )
                if already:
                    logger.info("Run %s déjà débité — reprise, pas de nouveau débit.", run_id)
                    return 0

                await self._consume(
                    conn,
                    agency_id=agency_id,
                    units=units,
                    run_id=run_id,
                    kind="debit_generation",
                    note=f"Génération de {cocoons} cocon(s)",
                )
        logger.info("Run %s débité de %d unité(s) (%d cocon(s))", run_id, units, cocoons)
        return units

    async def debit_regeneration(
        self, *, agency_id: str, run_id: str, articles: int, plan: Plan, note: str | None = None
    ) -> int:
        """Débite une régénération d'article : 1/6 de cocon par article.

        Volontairement **non idempotent** : chaque régénération demandée est un
        travail distinct, y compris deux fois le même article. C'est l'inverse
        du débit de génération, et c'est voulu.
        """
        await self.ensure_period_grant(agency_id, plan)
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await self._consume(
                    conn,
                    agency_id=agency_id,
                    units=articles,
                    run_id=run_id,
                    kind="debit_regeneration",
                    note=note or f"Régénération de {articles} article(s)",
                )
        return articles

    async def _consume(
        self,
        conn: Any,
        *,
        agency_id: str,
        units: int,
        run_id: str | None,
        kind: str,
        note: str,
    ) -> None:
        """Consomme `units` sur les lots vivants, du plus proche de l'expiration.

        FIFO par expiration (les lots sans expiration en dernier) : on brûle
        d'abord ce qui allait être perdu. Le `for update` verrouille les lots de
        l'agence pour toute la transaction — sans lui, deux générations
        simultanées liraient le même solde et le dépasseraient toutes les deux.
        """
        rows = await conn.fetch(
            """
            select id, remaining_units from cocoon_lots
            where agency_id = $1::uuid
              and remaining_units > 0
              and (expires_at is null or expires_at > now())
            order by expires_at asc nulls last, granted_at asc
            for update
            """,
            agency_id,
        )
        available = sum(r["remaining_units"] for r in rows)
        if available < units:
            raise InsufficientBalance(required_units=units, available_units=available)

        left = units
        for row in rows:
            if left <= 0:
                break
            take = min(left, row["remaining_units"])
            await conn.execute(
                "update cocoon_lots set remaining_units = remaining_units - $2 where id = $1",
                row["id"],
                take,
            )
            await conn.execute(
                """
                insert into cocoon_ledger (agency_id, lot_id, run_id, kind, delta_units, note)
                values ($1::uuid, $2, $3::uuid, $4, $5, $6)
                """,
                agency_id,
                row["id"],
                run_id,
                kind,
                -take,
                note,
            )
            left -= take

    # ------------------------------------------------------------------
    # Remboursement
    # ------------------------------------------------------------------

    async def refund_run(self, run_id: str, note: str | None = None) -> int:
        """Rembourse les débits de génération d'un run échoué. Idempotent.

        Les unités retournent **dans les lots d'origine**, pas dans un lot neuf :
        sinon un run raté en fin de mois rallongerait la durée de vie de
        l'allocation, et il suffirait de faire échouer des runs pour ne jamais
        rien perdre. Contrepartie assumée : si le lot d'origine a expiré entre
        le débit et l'échec, le remboursement est comptable mais sans valeur.
        Le cas est étroit — un run dure moins d'une heure — et le corriger
        rouvrirait la faille ci-dessus.

        Les régénérations ne sont pas remboursées : le travail a été commandé.

        Retourne le nombre d'unités recréditées.
        """
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    select id, agency_id, lot_id, delta_units from cocoon_ledger
                    where run_id = $1::uuid
                      and kind = 'debit_generation'
                      and reversed_at is null
                    for update
                    """,
                    run_id,
                )
                if not rows:
                    return 0

                total = 0
                for row in rows:
                    units = -row["delta_units"]  # le débit est négatif
                    total += units
                    if row["lot_id"] is not None:
                        await conn.execute(
                            """
                            update cocoon_lots
                            set remaining_units = least(remaining_units + $2, granted_units)
                            where id = $1
                            """,
                            row["lot_id"],
                            units,
                        )
                    await conn.execute(
                        "update cocoon_ledger set reversed_at = now() where id = $1",
                        row["id"],
                    )
                    await conn.execute(
                        """
                        insert into cocoon_ledger
                            (agency_id, lot_id, run_id, kind, delta_units, note)
                        values ($1, $2, $3::uuid, 'refund', $4, $5)
                        """,
                        row["agency_id"],
                        row["lot_id"],
                        run_id,
                        units,
                        note or "Remboursement automatique — run en échec",
                    )
        logger.info("Run %s remboursé de %d unité(s)", run_id, total)
        return total

    # ------------------------------------------------------------------
    # Formule
    # ------------------------------------------------------------------

    async def get_plan_for(self, agency_id: str) -> Plan:
        pool = await self._pool()
        async with pool.acquire() as conn:
            key = await conn.fetchval(
                "select plan from agencies where id = $1::uuid", agency_id
            )
        return get_plan(key)

    async def set_plan(self, agency_id: str, plan_key: str) -> Plan:
        """Change la formule. Appelé à la main tant qu'il n'y a pas de paiement.

        Le changement ne rétro-alloue rien : le lot du mois en cours a déjà été
        octroyé au tarif précédent. Un passage à un palier supérieur en milieu
        de mois ne donne donc son allocation qu'au mois suivant — à revoir avec
        la facturation réelle, où le prorata devra suivre la logique du
        prestataire de paiement plutôt que la nôtre.
        """
        plan = get_plan(plan_key)
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "update agencies set plan = $2, plan_started_at = now() where id = $1::uuid",
                agency_id,
                plan.key,
            )
        return plan


def _serialize(row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in dict(row).items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif key in ("id", "lot_id", "run_id") and value is not None:
            out[key] = str(value)
        else:
            out[key] = value
    return out


_billing: BillingRepository | None = None


def get_billing_repository() -> BillingRepository:
    global _billing
    if _billing is None:
        _billing = BillingRepository()
    return _billing
