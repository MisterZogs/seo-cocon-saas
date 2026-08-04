"""FastAPI backend — expose le pipeline via REST + SSE.

Routes :
- GET  /health                 — santé du service
- POST /generate               — enqueue un job pipeline, retourne job_id
- GET  /jobs/{job_id}          — statut + progression + résultat si terminé
- GET  /jobs/{job_id}/stream   — SSE stream de la progression en temps réel

Run local :
    cd backend
    .venv/bin/uvicorn main:app --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from redis import Redis
from rq import Queue
from rq.job import Job

load_dotenv(Path(__file__).parent / ".env")

from models import ClientForm  # noqa: E402
from workers.pipeline_job import run_pipeline_job  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
JOB_TIMEOUT_SECONDS = 1800  # 30 min max par run


# Causes fréquentes → message actionnable en français. Le pattern est cherché
# dans la traceback complète, du plus spécifique au plus générique.
_ERROR_HINTS: list[tuple[str, str]] = [
    (
        "credit balance is too low",
        "Crédit Anthropic épuisé. Rechargez le compte sur console.anthropic.com "
        "(Plans & Billing) puis relancez la génération.",
    ),
    (
        "invalid x-api-key",
        "Clé API Anthropic invalide. Vérifiez ANTHROPIC_API_KEY dans le .env du serveur.",
    ),
    (
        "authentication_error",
        "Authentification Anthropic refusée. Vérifiez ANTHROPIC_API_KEY dans le .env du serveur.",
    ),
    (
        "rate_limit_error",
        "Limite de débit Anthropic atteinte malgré les retries. Réessayez dans quelques minutes.",
    ),
    (
        "overloaded_error",
        "API Anthropic surchargée. Réessayez dans quelques minutes.",
    ),
    (
        "DataForSEO",
        "Erreur DataForSEO. Vérifiez les credentials (ou laissez-les vides pour le mode mock).",
    ),
    (
        "HorseTimeoutException",
        "Le job a dépassé la limite de 30 minutes et a été interrompu.",
    ),
]


def _friendly_error(exc_info: str | None) -> tuple[str, str | None]:
    """Traduit une traceback RQ en (message lisible, traceback brute).

    Le front affiche le message ; la traceback reste disponible en repli.
    """
    if not exc_info:
        return "Erreur inconnue", None

    raw = str(exc_info)
    for needle, hint in _ERROR_HINTS:
        if needle.lower() in raw.lower():
            return hint, raw

    # Sinon : dernière ligne non vide de la traceback, c'est l'exception réelle
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    return (lines[-1] if lines else "Erreur inconnue"), raw


QUEUE_NAME = "pipeline"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = Redis.from_url(REDIS_URL)
    app.state.queue = Queue(QUEUE_NAME, connection=app.state.redis)
    logger.info("FastAPI démarré, Redis: %s", REDIS_URL)
    yield
    app.state.redis.close()


app = FastAPI(
    title="Cocon Sémantique SaaS API",
    version="0.1.0",
    lifespan=lifespan,
)

_allowed_origins = ["http://localhost:3000", "http://localhost:5173"]
_domain = os.getenv("DOMAIN")
if _domain:
    _allowed_origins.append(f"https://{_domain}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Routes
# ============================================================


@app.get("/health")
async def health() -> dict:
    try:
        pong = app.state.redis.ping()
    except Exception as e:
        return {"status": "degraded", "redis": str(e)}
    return {"status": "ok", "redis": "up" if pong else "down"}


@app.post("/generate")
async def generate(form: ClientForm) -> dict:
    """Enqueue une génération de cocons. Retourne le job_id."""
    job = app.state.queue.enqueue(
        run_pipeline_job,
        form.model_dump(mode="json"),
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=86400,   # résultat conservé 24h après exécution
        failure_ttl=86400,
    )
    logger.info("Job enqueued: %s", job.id)
    return {"job_id": job.id, "status": job.get_status()}


@app.get("/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    try:
        job = Job.fetch(job_id, connection=app.state.redis)
    except Exception:
        raise HTTPException(status_code=404, detail="Job introuvable")

    payload = {
        "job_id": job.id,
        "status": job.get_status(refresh=True),
        "progress": job.meta.get("progress"),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
    }

    if job.is_finished:
        payload["result"] = job.result
    elif job.is_failed:
        message, traceback_raw = _friendly_error(job.exc_info)
        payload["error"] = message
        payload["error_traceback"] = traceback_raw

    return payload


@app.get("/jobs/{job_id}/stream")
async def job_stream(job_id: str) -> StreamingResponse:
    """SSE — envoie un event à chaque changement de progression."""
    try:
        Job.fetch(job_id, connection=app.state.redis)
    except Exception:
        raise HTTPException(status_code=404, detail="Job introuvable")

    async def event_source():
        last_progress = None
        last_ping = asyncio.get_event_loop().time()
        while True:
            job = Job.fetch(job_id, connection=app.state.redis)
            status = job.get_status(refresh=True)
            progress = job.meta.get("progress")

            if progress != last_progress:
                last_progress = progress
                last_ping = asyncio.get_event_loop().time()
                yield f"event: progress\ndata: {json.dumps(progress)}\n\n"

            if status == "finished":
                yield f"event: done\ndata: {json.dumps({'result': job.result})}\n\n"
                break
            if status == "failed":
                message, traceback_raw = _friendly_error(job.exc_info)
                payload = {"error": message, "error_traceback": traceback_raw}
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                break

            # Keepalive toutes les 20s pour éviter le proxy_read_timeout nginx (60s)
            now = asyncio.get_event_loop().time()
            if now - last_ping > 20:
                yield ": keepalive\n\n"
                last_ping = now

            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
