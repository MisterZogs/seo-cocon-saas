"""Persistance des runs dans Supabase — optionnelle et non bloquante.

Deux principes :

1. **Dégradation gracieuse.** Si `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`
   sont absents, le repository est désactivé et toutes ses méthodes sont des
   no-op. Le pipeline tourne exactement comme avant.
2. **La persistance ne casse jamais un run.** Toute erreur Supabase est logguée
   puis avalée : perdre l'historique est ennuyeux, perdre 15 minutes de
   génération payée l'est beaucoup plus.

Le SDK `supabase` est synchrone ; les appels partent donc dans un thread pour
ne pas bloquer la boucle asyncio du worker ou de FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRepository:
    """CRUD sur les tables `runs` et `run_checkpoints` (cf. db/schema.sql)."""

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        self._url = url or os.getenv("SUPABASE_URL") or ""
        # service_role : le backend écrit pour toutes les agences, il bypasse RLS
        self._key = (
            key
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_ANON_KEY")
            or ""
        )
        self._client: Any | None = None

        if not self._url or not self._key:
            logger.info(
                "Supabase non configuré — persistance des runs désactivée "
                "(l'historique se limite au TTL Redis de 24h)."
            )
            return

        try:
            from supabase import create_client

            self._client = create_client(self._url, self._key)
            logger.info("Supabase connecté — persistance des runs active.")
        except Exception as e:  # pragma: no cover - dépend de l'env
            logger.warning("Supabase injoignable (%s) — persistance désactivée.", e)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _run(self, label: str, fn) -> Any | None:
        """Exécute un appel Supabase dans un thread, en avalant les erreurs."""
        if self._client is None:
            return None
        try:
            return await asyncio.to_thread(fn)
        except Exception as e:
            logger.warning("Supabase %s a échoué (ignoré) : %s", label, e)
            return None

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def create_run(self, *, job_id: str, form: dict[str, Any]) -> str | None:
        """Crée la ligne `runs` au moment de l'enqueue. Retourne le run_id."""
        row = {
            "job_id": job_id,
            "agency_id": form.get("agency_id"),
            "project_name": form.get("client_project_name"),
            "form": form,
            "mode": form.get("mode", "brief"),
            "language": form.get("language", "fr"),
            "status": "queued",
        }
        res = await self._run(
            "create_run",
            lambda: self._client.table("runs").insert(row).execute(),
        )
        if res and res.data:
            run_id = res.data[0]["id"]
            logger.info("Run persisté: %s (job %s)", run_id, job_id)
            return run_id
        return None

    async def mark_running(self, run_id: str) -> None:
        await self._run(
            "mark_running",
            lambda: self._client.table("runs")
            .update({"status": "running", "started_at": _now()})
            .eq("id", run_id)
            .execute(),
        )

    async def save_progress(self, run_id: str, progress: dict[str, Any]) -> None:
        await self._run(
            "save_progress",
            lambda: self._client.table("runs")
            .update({"progress": progress})
            .eq("id", run_id)
            .execute(),
        )

    async def save_result(self, run_id: str, result: dict[str, Any]) -> None:
        payload = {
            "status": "completed",
            "result": result,
            "ended_at": _now(),
            "cocoons_count": len(result.get("cocoons") or []),
            "articles_count": len(result.get("articles") or [])
            + len(result.get("briefs") or []),
        }
        await self._run(
            "save_result",
            lambda: self._client.table("runs")
            .update(payload)
            .eq("id", run_id)
            .execute(),
        )

    async def save_error(
        self, run_id: str, message: str, traceback_raw: str | None = None
    ) -> None:
        await self._run(
            "save_error",
            lambda: self._client.table("runs")
            .update(
                {
                    "status": "failed",
                    "error": message,
                    "error_traceback": traceback_raw,
                    "ended_at": _now(),
                }
            )
            .eq("id", run_id)
            .execute(),
        )

    async def relink_job(self, run_id: str, job_id: str) -> None:
        """Rattache un run à un nouveau job RQ (cas d'une reprise)."""
        await self._run(
            "relink_job",
            lambda: self._client.table("runs")
            .update(
                {
                    "job_id": job_id,
                    "status": "queued",
                    "error": None,
                    "error_traceback": None,
                    "ended_at": None,
                }
            )
            .eq("id", run_id)
            .execute(),
        )

    async def list_runs(
        self, *, agency_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Historique — sans la colonne `result`, trop lourde pour une liste."""

        def _query():
            q = self._client.table("runs").select(
                "id, job_id, agency_id, project_name, mode, language, status, "
                "error, cocoons_count, articles_count, created_at, ended_at"
            )
            if agency_id:
                q = q.eq("agency_id", agency_id)
            return q.order("created_at", desc=True).limit(limit).execute()

        res = await self._run("list_runs", _query)
        return res.data if res and res.data else []

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        res = await self._run(
            "get_run",
            lambda: self._client.table("runs")
            .select("*")
            .eq("id", run_id)
            .limit(1)
            .execute(),
        )
        return res.data[0] if res and res.data else None

    async def get_run_by_job(self, job_id: str) -> dict[str, Any] | None:
        res = await self._run(
            "get_run_by_job",
            lambda: self._client.table("runs")
            .select("*")
            .eq("job_id", job_id)
            .limit(1)
            .execute(),
        )
        return res.data[0] if res and res.data else None

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    async def get_checkpoint(self, run_id: str, step: str) -> Any | None:
        res = await self._run(
            "get_checkpoint",
            lambda: self._client.table("run_checkpoints")
            .select("payload")
            .eq("run_id", run_id)
            .eq("step", step)
            .limit(1)
            .execute(),
        )
        if res and res.data:
            return res.data[0]["payload"]
        return None

    async def save_checkpoint(self, run_id: str, step: str, payload: Any) -> None:
        await self._run(
            "save_checkpoint",
            lambda: self._client.table("run_checkpoints")
            .upsert(
                {"run_id": run_id, "step": step, "payload": payload},
                on_conflict="run_id,step",
            )
            .execute(),
        )


_repository: RunRepository | None = None


def get_repository() -> RunRepository:
    """Singleton — évite de recréer un client HTTP par appel."""
    global _repository
    if _repository is None:
        _repository = RunRepository()
    return _repository
