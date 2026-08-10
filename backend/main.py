"""FastAPI backend — expose le pipeline via REST + SSE.

Routes publiques :
- GET  /health                 — santé du service
- POST /auth/register          — crée un compte agence
- POST /auth/login             — échange email + mot de passe contre un JWT

Routes authentifiées (en-tête `Authorization: Bearer <jeton>`) :
- GET  /auth/me                — l'agence connectée
- POST /generate               — enqueue un job pipeline, retourne job_id
- GET  /runs                   — historique de l'agence
- GET  /jobs/{job_id}          — statut + progression + résultat si terminé
- GET  /jobs/{job_id}/stream   — SSE de la progression (jeton en query, cf. auth.py)

Cloisonnement : chaque run porte l'`agency_id` de son créateur, posé **côté
serveur** depuis le jeton — jamais depuis le formulaire. Toutes les lectures
d'un run vérifient ce propriétaire et répondent 404 (pas 403) quand il ne
correspond pas, pour ne pas révéler l'existence du run.

Run local :
    cd backend
    .venv/bin/uvicorn main:app --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from redis import Redis
from rq import Queue
from rq.job import Job

load_dotenv(Path(__file__).parent / ".env")

from auth import (  # noqa: E402
    Agency,
    create_access_token,
    current_agency,
    current_agency_from_query,
    get_secret,
    hash_password,
    normalize_email,
    validate_password_strength,
    verify_password,
)
from clients.anthropic_client import AnthropicClient  # noqa: E402
from db.agencies import EmailAlreadyUsed, get_agency_repository  # noqa: E402
from db.checkpoints import make_checkpoint_store  # noqa: E402
from db.postgres import StorageUnavailable, get_repository  # noqa: E402
from models import (  # noqa: E402
    AgencyPublic,
    ClientForm,
    KeywordWithData,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    ValidationDecision,
    ValidationSnapshot,
)
from pipeline.cocon_builder import CoconBuilder  # noqa: E402
from pipeline.validation import apply_decision  # noqa: E402
from workers.pipeline_job import VALIDATION_CHECKPOINT, run_pipeline_job  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# 1h : un run FULL de 12 articles tourne à ~2-4 min par article (Opus sur la
# mère, 16k tokens de sortie), soit 30-45 min. L'ancienne limite de 30 min
# coupait ces runs en plein milieu. Un dépassement reste rattrapable — les
# checkpoints permettent de reprendre — mais autant ne pas le provoquer.
JOB_TIMEOUT_SECONDS = 3600


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
        "Le job a dépassé la durée maximale autorisée et a été interrompu. "
        "La reprise repart des étapes déjà terminées.",
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
    form_dict = form.model_dump(mode="json")
    job_id = str(uuid.uuid4())

    # Le run_id est indépendant du job RQ : il survit au TTL de 24h et sert de
    # clé aux checkpoints. Postgres le fournit quand il est configuré ; sinon on
    # en génère un localement pour que la reprise fonctionne quand même.
    repo = get_repository()
    run_id = await repo.create_run(job_id=job_id, form=form_dict) or str(uuid.uuid4())

    job = app.state.queue.enqueue(
        run_pipeline_job,
        form_dict,
        run_id,
        job_id=job_id,
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=86400,   # résultat conservé 24h après exécution
        failure_ttl=86400,
    )
    logger.info("Job enqueued: %s (run %s)", job.id, run_id)
    return {"job_id": job.id, "run_id": run_id, "status": job.get_status()}


@app.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str) -> dict:
    """Relance un job échoué en réutilisant ses checkpoints.

    Les étapes déjà passées (et payées) sont relues au lieu d'être rejouées :
    en pratique, une reprise après un crash à l'étape 4 repart directement à
    l'article qui a échoué.
    """
    try:
        job = Job.fetch(job_id, connection=app.state.redis)
    except Exception:
        raise HTTPException(
            status_code=404,
            detail="Job introuvable — au-delà de 24h, relancez depuis l'historique.",
        )

    if not job.is_failed:
        raise HTTPException(
            status_code=409,
            detail=f"Ce job n'est pas en échec (statut : {job.get_status()}).",
        )

    if not job.args:
        raise HTTPException(status_code=422, detail="Job sans formulaire réutilisable.")

    form_dict = job.args[0]
    run_id = job.args[1] if len(job.args) > 1 else str(uuid.uuid4())
    new_job_id = str(uuid.uuid4())

    await get_repository().relink_job(run_id, new_job_id)

    new_job = app.state.queue.enqueue(
        run_pipeline_job,
        form_dict,
        run_id,
        job_id=new_job_id,
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=86400,
        failure_ttl=86400,
    )
    logger.info("Job %s relancé en %s (run %s)", job_id, new_job.id, run_id)
    return {"job_id": new_job.id, "run_id": run_id, "status": new_job.get_status()}


# ============================================================
# Validation humaine de la sélection de mots-clés
# ============================================================


def _store_for(run_id: str):
    return make_checkpoint_store(
        run_id, redis_url=REDIS_URL, repository=get_repository()
    )


@app.get("/runs/{run_id}/validation")
async def get_validation(run_id: str) -> ValidationSnapshot:
    """Sélection proposée par Claude + pool complet, pour l'écran de validation."""
    snapshot = await _store_for(run_id).get(VALIDATION_CHECKPOINT)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Aucune sélection en attente pour ce run. Elle a peut-être déjà "
                "été validée, ou le run a été lancé sans étape de validation."
            ),
        )
    return ValidationSnapshot.model_validate(snapshot)


@app.post("/runs/{run_id}/validation")
async def submit_validation(run_id: str, decision: ValidationDecision) -> dict:
    """Applique la sélection de l'agence et relance le run sur cette base.

    La décision est convertie en `cocon_design` checkpoint : c'est ce qui fait
    que le pipeline relancé saute la porte de validation et reprend directement
    à l'analyse SERP, sans repayer la recherche de mots-clés.
    """
    store = _store_for(run_id)

    snapshot_raw = await store.get(VALIDATION_CHECKPOINT)
    research = await store.get("keyword_research")
    if snapshot_raw is None or research is None:
        raise HTTPException(
            status_code=404,
            detail="Run introuvable ou sélection déjà validée.",
        )

    repo = get_repository()
    run = await repo.get_run(run_id) if repo.enabled else None
    form_dict = (run or {}).get("form")
    if form_dict is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Formulaire du run introuvable — la validation nécessite la "
                "persistance Postgres."
            ),
        )
    form = ClientForm.model_validate(form_dict)

    proposals = research.get("proposals") or []
    keywords = [KeywordWithData.model_validate(k) for k in research.get("keywords", [])]

    try:
        rebuilt = await apply_decision(
            decision, proposals, keywords, form, AnthropicClient()
        )
        cocoons = CoconBuilder().build(rebuilt)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if len(cocoons) != len(decision.cocoons):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(decision.cocoons)} cocon(s) soumis, {len(cocoons)} valide(s). "
                "Vérifiez qu'ils comptent au moins 1 mère et 3 filles."
            ),
        )

    await store.set("cocon_design", [c.model_dump(mode="json") for c in cocoons])

    new_job_id = str(uuid.uuid4())
    await repo.relink_job(run_id, new_job_id)
    job = app.state.queue.enqueue(
        run_pipeline_job,
        form_dict,
        run_id,
        job_id=new_job_id,
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=86400,
        failure_ttl=86400,
    )
    logger.info(
        "Validation acceptée pour le run %s : %d cocons, %d articles — job %s",
        run_id,
        len(cocoons),
        sum(1 + len(c.daughters) for c in cocoons),
        job.id,
    )
    return {
        "job_id": job.id,
        "run_id": run_id,
        "status": job.get_status(),
        "cocoons": len(cocoons),
        "articles": sum(1 + len(c.daughters) for c in cocoons),
    }


@app.get("/runs")
async def list_runs(agency_id: str | None = None, limit: int = 50) -> dict:
    """Historique des générations — nécessite Postgres configuré."""
    repo = get_repository()
    if not repo.enabled:
        return {"enabled": False, "runs": [], "detail": "Base de données non configurée."}
    runs = await repo.list_runs(agency_id=agency_id, limit=min(limit, 200))
    return {"enabled": True, "runs": runs}


@app.get("/form-defaults")
async def form_defaults(agency_id: str | None = None) -> dict:
    """Valeurs de préremplissage du formulaire : la dernière demande soumise.

    Le formulaire n'a plus de valeurs en dur. Sans Postgres — ou au tout premier
    usage — la réponse est `{"form": null}` et le formulaire s'ouvre vide, ce qui
    est le comportement attendu et pas une erreur : la route ne renvoie jamais
    503, sinon /new afficherait une alerte pour une base simplement vide.
    """
    repo = get_repository()
    if not repo.enabled:
        return {"enabled": False, "form": None}
    return {"enabled": True, "form": await repo.get_latest_form(agency_id=agency_id)}


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    repo = get_repository()
    if not repo.enabled:
        raise HTTPException(status_code=503, detail="Base de données non configurée.")
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run introuvable")
    return run


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

    # Le run_id est stable d'un job à l'autre (une reprise ou une validation
    # crée un nouveau job sur le même run) : c'est lui qui adresse la validation.
    payload["run_id"] = job.args[1] if len(job.args or ()) > 1 else None

    if job.is_finished:
        result = job.result
        # Un run suspendu « finit » côté RQ sans être terminé côté métier.
        if isinstance(result, dict) and result.get("awaiting_validation"):
            payload["status"] = "awaiting_validation"
            payload["run_id"] = result.get("run_id") or payload["run_id"]
        else:
            payload["result"] = result
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
                result = job.result
                if isinstance(result, dict) and result.get("awaiting_validation"):
                    payload = {"run_id": result.get("run_id")}
                    yield f"event: awaiting_validation\ndata: {json.dumps(payload)}\n\n"
                else:
                    yield f"event: done\ndata: {json.dumps({'result': result})}\n\n"
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
