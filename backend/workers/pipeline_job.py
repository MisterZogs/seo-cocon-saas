"""RQ worker : exécute le pipeline en background et publie la progression via job.meta.

Utilisation depuis FastAPI :
    from rq import Queue
    from redis import Redis
    from workers.pipeline_job import run_pipeline_job

    queue = Queue("pipeline", connection=Redis.from_url(REDIS_URL))
    job = queue.enqueue(run_pipeline_job, form_dict, job_timeout=1800)

Le worker lui-même se lance avec :
    cd backend && .venv/bin/rq worker pipeline --url redis://localhost:6379/0
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rq import get_current_job

from models import ClientForm, JobProgress
from pipeline.orchestrator import run_pipeline

logger = logging.getLogger(__name__)


def run_pipeline_job(form_dict: dict[str, Any]) -> dict:
    """Point d'entrée RQ (sync) qui wrappe le pipeline async.

    Args:
        form_dict: le ClientForm sérialisé (JSON-compatible)

    Returns:
        Le PipelineResult sérialisé (dict JSON-compatible)
    """
    form = ClientForm.model_validate(form_dict)
    result = asyncio.run(_run_async(form))
    return result.model_dump(mode="json")


async def _run_async(form: ClientForm):
    job = get_current_job()

    async def _emit(progress: JobProgress) -> None:
        if job is None:
            return
        job.meta["progress"] = progress.model_dump(mode="json")
        job.save_meta()
        logger.info(
            "job=%s step=%s pct=%d msg=%s",
            job.id,
            progress.step.value,
            progress.percent,
            progress.message,
        )

    return await run_pipeline(form, on_progress=_emit)
