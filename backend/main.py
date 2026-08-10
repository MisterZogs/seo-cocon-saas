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
    # Échoue tôt et bruyamment : un backend qui démarre sans secret de
    # signature est un backend dont toutes les URL de run sont publiques.
    # `get_secret` lève une `AuthNotConfigured` qui explique comment en générer un.
    get_secret()

    if not get_repository().enabled:
        logger.warning(
            "DATABASE_URL absent : les comptes agences n'ont nulle part où être "
            "stockés. /auth/* et toutes les routes protégées répondront 503."
        )

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


@app.exception_handler(StorageUnavailable)
async def _storage_unavailable(request: Request, exc: StorageUnavailable) -> JSONResponse:
    """Postgres injoignable sur une route qui ne peut pas s'en passer.

    503 et non 500 : ce n'est pas un bug du code, c'est une dépendance absente,
    et le message dit laquelle.
    """
    logger.error("Stockage indisponible sur %s : %s", request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# ============================================================
# Contrôle d'accès aux runs et aux jobs
# ============================================================


async def _require_run_access(run_id: str, agency: Agency) -> None:
    """404 si le run n'existe pas OU n'appartient pas à l'agence.

    Le même code dans les deux cas est délibéré : distinguer « n'existe pas » de
    « pas à vous » transformerait la route en oracle d'existence des runs.
    """
    owner = await get_repository().get_run_owner(run_id)
    if owner is None or owner != agency.id:
        raise HTTPException(status_code=404, detail="Run introuvable")


def _job_agency_id(job: Job) -> str | None:
    """Propriétaire d'un job RQ, lu dans le formulaire passé en argument.

    On ne repasse pas par Postgres : le formulaire est déjà dans les args du job,
    et cette lecture doit rester possible même sans base (le suivi de job vit
    dans Redis).
    """
    args = job.args or ()
    form = args[0] if args else None
    return form.get("agency_id") if isinstance(form, dict) else None


def _require_job_access(job: Job, agency: Agency) -> None:
    if _job_agency_id(job) != agency.id:
        raise HTTPException(status_code=404, detail="Job introuvable")


# ============================================================
# Routes — authentification
# ============================================================


@app.post("/auth/register", response_model=TokenResponse)
async def register(payload: RegisterRequest) -> TokenResponse:
    """Crée un compte agence et renvoie directement un jeton.

    Pas de vérification d'email au MVP : la cible est une poignée d'agences
    françaises, souvent onboardées à la main. À ajouter avant toute inscription
    ouverte au public.
    """
    validate_password_strength(payload.password)
    email = normalize_email(payload.email)

    try:
        row = await get_agency_repository().create(
            email=email,
            name=payload.name.strip(),
            password_hash=hash_password(payload.password),
        )
    except EmailAlreadyUsed:
        raise HTTPException(status_code=409, detail="Un compte existe déjà pour cet email.")

    agency = Agency(id=str(row["id"]), email=row["email"], name=row["name"])
    token, expires_at = create_access_token(agency)
    logger.info("Compte agence créé : %s (%s)", agency.name, agency.id)
    return TokenResponse(
        access_token=token,
        expires_at=expires_at,
        agency=AgencyPublic(id=agency.id, email=agency.email, name=agency.name),
    )


@app.post("/auth/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    repo = get_agency_repository()
    row = await repo.get_by_email(normalize_email(payload.email))

    # Message identique pour « email inconnu » et « mot de passe faux » : sinon
    # la route énumère les comptes existants.
    invalid = HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")
    if row is None:
        raise invalid

    ok, new_hash = verify_password(row["password_hash"], payload.password)
    if not ok:
        raise invalid

    agency_id = str(row["id"])
    if new_hash:
        await repo.update_password_hash(agency_id, new_hash)
    await repo.touch_last_login(agency_id)

    agency = Agency(id=agency_id, email=row["email"], name=row["name"])
    token, expires_at = create_access_token(agency)
    return TokenResponse(
        access_token=token,
        expires_at=expires_at,
        agency=AgencyPublic(id=agency.id, email=agency.email, name=agency.name),
    )


@app.get("/auth/me", response_model=AgencyPublic)
async def me(agency: Agency = Depends(current_agency)) -> AgencyPublic:
    """Vérifie qu'un jeton stocké côté front est toujours valide.

    Les champs viennent du jeton, pas de la base : c'est un contrôle de session,
    il ne doit pas dépendre de Postgres.
    """
    return AgencyPublic(id=agency.id, email=agency.email, name=agency.name)


# ============================================================
# Routes — pipeline
# ============================================================


@app.get("/health")
async def health() -> dict:
    try:
        pong = app.state.redis.ping()
    except Exception as e:
        return {"status": "degraded", "redis": str(e)}
    return {"status": "ok", "redis": "up" if pong else "down"}


@app.post("/generate")
async def generate(form: ClientForm, agency: Agency = Depends(current_agency)) -> dict:
    """Enqueue une génération de cocons. Retourne le job_id."""
    form_dict = form.model_dump(mode="json")
    # Le propriétaire vient du jeton, jamais du formulaire : `agency_id` reste
    # un champ du ClientForm pour la compatibilité des runs déjà persistés, mais
    # la valeur envoyée par le client est ignorée.
    form_dict["agency_id"] = agency.id
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
async def retry_job(job_id: str, agency: Agency = Depends(current_agency)) -> dict:
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

    _require_job_access(job, agency)

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
async def get_validation(
    run_id: str, agency: Agency = Depends(current_agency)
) -> ValidationSnapshot:
    """Sélection proposée par Claude + pool complet, pour l'écran de validation."""
    await _require_run_access(run_id, agency)
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
async def submit_validation(
    run_id: str,
    decision: ValidationDecision,
    agency: Agency = Depends(current_agency),
) -> dict:
    """Applique la sélection de l'agence et relance le run sur cette base.

    La décision est convertie en `cocon_design` checkpoint : c'est ce qui fait
    que le pipeline relancé saute la porte de validation et reprend directement
    à l'analyse SERP, sans repayer la recherche de mots-clés.
    """
    await _require_run_access(run_id, agency)
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
async def list_runs(limit: int = 50, agency: Agency = Depends(current_agency)) -> dict:
    """Historique des générations de l'agence connectée.

    L'`agency_id` n'est plus un paramètre de requête : il venait du client, donc
    n'importe qui pouvait lister l'historique de n'importe qui.
    """
    repo = get_repository()
    if not repo.enabled:
        return {"enabled": False, "runs": [], "detail": "Base de données non configurée."}
    runs = await repo.list_runs(agency_id=agency.id, limit=min(limit, 200))
    return {"enabled": True, "runs": runs}


@app.get("/form-defaults")
async def form_defaults(agency: Agency = Depends(current_agency)) -> dict:
    """Valeurs de préremplissage du formulaire : la dernière demande de l'agence.

    Le formulaire n'a plus de valeurs en dur. Sans Postgres — ou au tout premier
    usage — la réponse est `{"form": null}` et le formulaire s'ouvre vide, ce qui
    est le comportement attendu et pas une erreur : la route ne renvoie jamais
    503, sinon /new afficherait une alerte pour une base simplement vide.
    """
    repo = get_repository()
    if not repo.enabled:
        return {"enabled": False, "form": None}
    return {"enabled": True, "form": await repo.get_latest_form(agency_id=agency.id)}


@app.get("/runs/{run_id}")
async def get_run(run_id: str, agency: Agency = Depends(current_agency)) -> dict:
    repo = get_repository()
    if not repo.enabled:
        raise HTTPException(status_code=503, detail="Base de données non configurée.")
    await _require_run_access(run_id, agency)
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run introuvable")
    return run


@app.get("/jobs/{job_id}")
async def job_status(job_id: str, agency: Agency = Depends(current_agency)) -> dict:
    try:
        job = Job.fetch(job_id, connection=app.state.redis)
    except Exception:
        raise HTTPException(status_code=404, detail="Job introuvable")

    _require_job_access(job, agency)

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
async def job_stream(
    job_id: str, agency: Agency = Depends(current_agency_from_query)
) -> StreamingResponse:
    """SSE — envoie un event à chaque changement de progression.

    Seule route à accepter le jeton en paramètre d'URL : `EventSource` ne sait
    pas envoyer d'en-tête. Voir `auth.current_agency_from_query`.
    """
    try:
        job = Job.fetch(job_id, connection=app.state.redis)
    except Exception:
        raise HTTPException(status_code=404, detail="Job introuvable")

    _require_job_access(job, agency)

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
