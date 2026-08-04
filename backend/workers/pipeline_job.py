"""RQ worker : exécute le pipeline en background et publie la progression via job.meta.

Utilisation depuis FastAPI :
    from rq import Queue
    from redis import Redis
    from workers.pipeline_job import run_pipeline_job

    queue = Queue("pipeline", connection=Redis.from_url(REDIS_URL))
    job = queue.enqueue(run_pipeline_job, form_dict, run_id, job_timeout=1800)

Le worker lui-même se lance avec :
    cd backend && .venv/bin/rq worker pipeline --url redis://localhost:6379/0

`run_id` (optionnel) rattache l'exécution à une ligne `runs` en base et active
les checkpoints : relancer le même run_id reprend là où ça s'était arrêté.
"""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
from typing import Any

from rq import get_current_job

from db.checkpoints import make_checkpoint_store
from db.postgres import get_repository
from models import ClientForm, JobProgress
from pipeline.orchestrator import run_pipeline

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def run_pipeline_job(
    form_dict: dict[str, Any], run_id: str | None = None
) -> dict:
    """Point d'entrée RQ (sync) qui wrappe le pipeline async.

    Args:
        form_dict: le ClientForm sérialisé (JSON-compatible)
        run_id: identifiant du run persisté, si la persistance est active

    Returns:
        Le PipelineResult sérialisé (dict JSON-compatible)
    """
    form = ClientForm.model_validate(form_dict)
    result = asyncio.run(_run_async(form, run_id))
    return result.model_dump(mode="json")


async def _run_async(form: ClientForm, run_id: str | None):
    job = get_current_job()
    repo = get_repository()
    store = make_checkpoint_store(run_id, redis_url=REDIS_URL, repository=repo)

    async def _emit(progress: JobProgress) -> None:
        payload = progress.model_dump(mode="json")
        if job is not None:
            job.meta["progress"] = payload
            job.save_meta()
            logger.info(
                "job=%s step=%s pct=%d msg=%s",
                job.id,
                progress.step.value,
                progress.percent,
                progress.message,
            )
        if run_id:
            await repo.save_progress(run_id, payload)

    if run_id:
        await repo.mark_running(run_id)

    try:
        result = await run_pipeline(form, on_progress=_emit, store=store)
    except Exception as e:
        # On persiste l'échec avant de relancer : RQ garde la traceback 24h,
        # la base la garde indéfiniment (et les checkpoints restent réutilisables).
        if run_id:
            await repo.save_error(run_id, str(e), traceback.format_exc())
        raise

    if run_id:
        await repo.save_result(run_id, result.model_dump(mode="json"))
    return result
