"""Comptes agences dans Postgres — table `agencies` (cf. db/schema.sql).

Partage le pool de `RunRepository` (un seul pool par process) mais **pas sa
politique d'erreur** : ici tout échec SQL remonte via `StorageUnavailable`. Voir
l'en-tête de `auth.py` pour le raisonnement.
"""

from __future__ import annotations

import logging
from typing import Any

from db.postgres import StorageUnavailable, get_repository

logger = logging.getLogger(__name__)

_COLUMNS = "id, email, name, password_hash, created_at, last_login_at"


class EmailAlreadyUsed(Exception):
    """Contrainte d'unicité sur `agencies.email`."""


class AgencyRepository:
    async def _pool(self) -> Any:
        return await get_repository().require_pool()

    async def create(self, *, email: str, name: str, password_hash: str) -> dict[str, Any]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                insert into agencies (email, name, password_hash)
                values ($1, $2, $3)
                on conflict (email) do nothing
                returning {_COLUMNS}
                """,
                email,
                name,
                password_hash,
            )
        # `do nothing` ne renvoie aucune ligne quand l'email existe déjà. C'est
        # préférable à laisser remonter l'UniqueViolation d'asyncpg : le message
        # d'erreur d'une contrainte SQL n'a rien à faire dans une réponse HTTP.
        if row is None:
            raise EmailAlreadyUsed(email)
        return dict(row)

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"select {_COLUMNS} from agencies where email = $1", email
            )
        return dict(row) if row else None

    async def get_by_id(self, agency_id: str) -> dict[str, Any] | None:
        pool = await self._pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"select {_COLUMNS} from agencies where id = $1::uuid", agency_id
                )
        except Exception as e:
            # Un `sub` non-uuid dans un jeton valide : impossible sans notre
            # propre secret, mais on refuse proprement plutôt que de renvoyer 500.
            raise StorageUnavailable(f"Lecture du compte impossible : {e}")
        return dict(row) if row else None

    async def update_password_hash(self, agency_id: str, password_hash: str) -> None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "update agencies set password_hash = $2 where id = $1::uuid",
                agency_id,
                password_hash,
            )

    async def touch_last_login(self, agency_id: str) -> None:
        """Best-effort : une erreur ici ne doit pas refuser une connexion valide."""
        try:
            pool = await self._pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "update agencies set last_login_at = now() where id = $1::uuid",
                    agency_id,
                )
        except Exception as e:
            logger.warning("last_login_at non mis à jour (ignoré) : %s", e)


_agencies: AgencyRepository | None = None


def get_agency_repository() -> AgencyRepository:
    global _agencies
    if _agencies is None:
        _agencies = AgencyRepository()
    return _agencies
