"""Persistance des runs dans Postgres — optionnelle et non bloquante.

Postgres tourne dans le même Docker Compose que l'app (service `db`), sur le
VPS. Pas de service externe : la base est à côté du backend, dans son propre
volume.

Deux principes :

1. **Dégradation gracieuse.** Sans `DATABASE_URL`, le repository est désactivé
   et toutes ses méthodes sont des no-op. Le pipeline tourne exactement comme
   avant, avec l'historique limité au TTL Redis.
2. **La persistance ne casse jamais un run.** Toute erreur SQL est logguée puis
   avalée : perdre l'historique est ennuyeux, perdre 15 minutes de génération
   payée l'est beaucoup plus.

Le schéma (db/schema.sql) est appliqué au premier accès — il est idempotent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Colonnes de l'historique — `result` et `form` sont volontairement exclus,
# trop lourds pour une liste.
_SUMMARY_COLUMNS = """
    id, job_id, agency_id, project_name, mode, language, status,
    error, cocoons_count, articles_count, created_at, ended_at
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: Any) -> dict[str, Any]:
    """asyncpg.Record → dict JSON-sérialisable (uuid/datetime → str, jsonb → obj)."""
    out: dict[str, Any] = {}
    for key, value in dict(row).items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif key in ("form", "result", "progress", "payload") and isinstance(value, str):
            # asyncpg renvoie le jsonb en texte tant qu'aucun codec n'est posé
            out[key] = json.loads(value) if value else None
        else:
            out[key] = str(value) if key == "id" else value
    return out


class RunRepository:
    """CRUD sur les tables `runs` et `run_checkpoints` (cf. db/schema.sql)."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.getenv("DATABASE_URL") or ""
        self._pool: Any | None = None
        self._ready = False
        self._lock = asyncio.Lock()

        if not self._dsn:
            logger.info(
                "DATABASE_URL absent — persistance des runs désactivée "
                "(l'historique se limite au TTL Redis de 24h)."
            )

    @property
    def enabled(self) -> bool:
        return bool(self._dsn)

    # ------------------------------------------------------------------
    # Connexion
    # ------------------------------------------------------------------

    async def _get_pool(self) -> Any | None:
        """Crée le pool et applique le schéma, une seule fois."""
        if not self._dsn:
            return None
        if self._pool is not None and self._ready:
            return self._pool

        async with self._lock:
            if self._pool is not None and self._ready:
                return self._pool
            try:
                import asyncpg

                self._pool = await asyncpg.create_pool(
                    self._dsn, min_size=1, max_size=5, command_timeout=30
                )
                async with self._pool.acquire() as conn:
                    await conn.execute(SCHEMA_PATH.read_text())
                self._ready = True
                logger.info("Postgres connecté — persistance des runs active.")
                return self._pool
            except Exception as e:
                logger.warning("Postgres injoignable (%s) — persistance désactivée.", e)
                self._pool = None
                return None

    async def _execute(self, label: str, query: str, *args: Any) -> None:
        pool = await self._get_pool()
        if pool is None:
            return
        try:
            async with pool.acquire() as conn:
                await conn.execute(query, *args)
        except Exception as e:
            logger.warning("Postgres %s a échoué (ignoré) : %s", label, e)

    async def _fetch(self, label: str, query: str, *args: Any) -> list[Any]:
        pool = await self._get_pool()
        if pool is None:
            return []
        try:
            async with pool.acquire() as conn:
                return await conn.fetch(query, *args)
        except Exception as e:
            logger.warning("Postgres %s a échoué (ignoré) : %s", label, e)
            return []

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._ready = False

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def create_run(self, *, job_id: str, form: dict[str, Any]) -> str | None:
        """Crée la ligne `runs` au moment de l'enqueue. Retourne le run_id."""
        rows = await self._fetch(
            "create_run",
            """
            insert into runs (job_id, agency_id, project_name, form, mode, language, status)
            values ($1, $2, $3, $4::jsonb, $5, $6, 'queued')
            returning id
            """,
            job_id,
            form.get("agency_id"),
            form.get("client_project_name"),
            json.dumps(form, ensure_ascii=False),
            form.get("mode", "brief"),
            form.get("language", "fr"),
        )
        if rows:
            run_id = str(rows[0]["id"])
            logger.info("Run persisté: %s (job %s)", run_id, job_id)
            return run_id
        return None

    async def mark_running(self, run_id: str) -> None:
        await self._execute(
            "mark_running",
            "update runs set status = 'running', started_at = $2 where id = $1::uuid",
            run_id,
            _now(),
        )

    async def save_progress(self, run_id: str, progress: dict[str, Any]) -> None:
        await self._execute(
            "save_progress",
            "update runs set progress = $2::jsonb where id = $1::uuid",
            run_id,
            json.dumps(progress, ensure_ascii=False),
        )

    async def mark_awaiting_validation(self, run_id: str) -> None:
        """Run suspendu en attente du feu vert de l'agence.

        `ended_at` reste nul : le run n'est pas terminé, il attend. C'est ce qui
        le distingue d'un run complété dans l'historique.
        """
        await self._execute(
            "mark_awaiting_validation",
            "update runs set status = 'awaiting_validation' where id = $1::uuid",
            run_id,
        )

    async def save_result(self, run_id: str, result: dict[str, Any]) -> None:
        await self._execute(
            "save_result",
            """
            update runs set status = 'completed', result = $2::jsonb, ended_at = $3,
                            cocoons_count = $4, articles_count = $5
            where id = $1::uuid
            """,
            run_id,
            json.dumps(result, ensure_ascii=False),
            _now(),
            len(result.get("cocoons") or []),
            len(result.get("articles") or []) + len(result.get("briefs") or []),
        )

    async def save_error(
        self, run_id: str, message: str, traceback_raw: str | None = None
    ) -> None:
        await self._execute(
            "save_error",
            """
            update runs set status = 'failed', error = $2, error_traceback = $3,
                            ended_at = $4
            where id = $1::uuid
            """,
            run_id,
            message,
            traceback_raw,
            _now(),
        )

    async def relink_job(self, run_id: str, job_id: str) -> None:
        """Rattache un run à un nouveau job RQ (cas d'une reprise)."""
        await self._execute(
            "relink_job",
            """
            update runs set job_id = $2, status = 'queued', error = null,
                            error_traceback = null, ended_at = null
            where id = $1::uuid
            """,
            run_id,
            job_id,
        )

    async def list_runs(
        self, *, agency_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Historique — sans les colonnes `form`/`result`, trop lourdes."""
        if agency_id:
            rows = await self._fetch(
                "list_runs",
                f"select {_SUMMARY_COLUMNS} from runs where agency_id = $1 "
                "order by created_at desc limit $2",
                agency_id,
                limit,
            )
        else:
            rows = await self._fetch(
                "list_runs",
                f"select {_SUMMARY_COLUMNS} from runs order by created_at desc limit $1",
                limit,
            )
        return [_row_to_dict(r) for r in rows]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = await self._fetch(
            "get_run", "select * from runs where id = $1::uuid", run_id
        )
        return _row_to_dict(rows[0]) if rows else None

    async def get_run_by_job(self, job_id: str) -> dict[str, Any] | None:
        rows = await self._fetch(
            "get_run_by_job", "select * from runs where job_id = $1", job_id
        )
        return _row_to_dict(rows[0]) if rows else None

    async def get_latest_form(
        self, *, agency_id: str | None = None
    ) -> dict[str, Any] | None:
        """Formulaire de la demande la plus récente — sert de valeurs par défaut.

        Le tri est sur `created_at`, pas sur le statut : ce qui a été *soumis* en
        dernier est ce que l'agence a en tête, qu'il ait abouti, échoué ou qu'il
        attende encore une validation.
        """
        if agency_id:
            rows = await self._fetch(
                "get_latest_form",
                "select form from runs where agency_id = $1 "
                "order by created_at desc limit 1",
                agency_id,
            )
        else:
            rows = await self._fetch(
                "get_latest_form",
                "select form from runs order by created_at desc limit 1",
            )
        if not rows:
            return None
        form = rows[0]["form"]
        return json.loads(form) if isinstance(form, str) else form

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    async def get_checkpoint(self, run_id: str, step: str) -> Any | None:
        rows = await self._fetch(
            "get_checkpoint",
            "select payload from run_checkpoints where run_id = $1::uuid and step = $2",
            run_id,
            step,
        )
        if not rows:
            return None
        payload = rows[0]["payload"]
        return json.loads(payload) if isinstance(payload, str) else payload

    async def save_checkpoint(self, run_id: str, step: str, payload: Any) -> None:
        await self._execute(
            "save_checkpoint",
            """
            insert into run_checkpoints (run_id, step, payload)
            values ($1::uuid, $2, $3::jsonb)
            on conflict (run_id, step) do update set payload = excluded.payload
            """,
            run_id,
            step,
            json.dumps(payload, ensure_ascii=False),
        )


_repository: RunRepository | None = None


def get_repository() -> RunRepository:
    """Singleton — un seul pool de connexions par process."""
    global _repository
    if _repository is None:
        _repository = RunRepository()
    return _repository
