"""RQ worker : régénère UN article d'un run terminé (chantier 16b).

Job séparé de `run_pipeline_job` à dessein — il ne partage ni le cycle de vie du
run, ni sa règle de facturation :

  · `run_pipeline_job` **ne re-débite pas** une reprise sur checkpoint : elle
    répare un échec technique dont l'agence n'est pas responsable.
  · ici on **débite systématiquement** 1 unité (1/6 de cocon, soit un article) :
    l'agence a lu la sortie, changé d'avis, et commande un nouveau travail.

Les deux règles coexistent sans se contredire parce que ce sont deux jobs.
Les mélanger dans une seule fonction aurait demandé un drapeau « est-ce que ça
compte ? », c'est-à-dire l'endroit exact où l'on finit par offrir des cocons.
"""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
from typing import Any

from rq import get_current_job

from db.billing import get_billing_repository
from db.checkpoints import make_checkpoint_store
from db.postgres import get_repository
from models import GenerationMode, JobProgress, PipelineResult, PipelineStep
from pipeline.regeneration import ArticleNotFound, regenerate_article

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Un article = 1/6 de cocon. Le ledger compte en unités entières précisément
# pour que ce « 1/6 » s'écrive « 1 » et non 0,1667 (cf. db/billing.py).
UNITS_PER_ARTICLE = 1


def regenerate_article_job(
    run_id: str, slug: str, directives: str | None, agency_id: str | None = None
) -> dict:
    """Point d'entrée RQ (sync) qui wrappe la régénération async."""
    return asyncio.run(_regenerate_async(run_id, slug, directives, agency_id))


async def _regenerate_async(
    run_id: str, slug: str, directives: str | None, agency_id: str | None
) -> dict:
    job = get_current_job()
    repo = get_repository()
    billing = get_billing_repository()

    async def _emit(percent: int, message: str) -> None:
        payload = JobProgress(
            step=PipelineStep.ARTICLE_GENERATION, percent=percent, message=message
        ).model_dump(mode="json")
        if job is not None:
            job.meta["progress"] = payload
            job.save_meta()
        await repo.save_progress(run_id, payload)

    run: dict[str, Any] | None = await repo.get_run(run_id)
    if not run or not run.get("result"):
        raise RuntimeError(
            f"Run {run_id} introuvable ou sans résultat — rien à régénérer."
        )

    result = PipelineResult.model_validate(run["result"])

    await _emit(5, f"Régénération de l'article « {slug} »...")

    # ---- Débit AVANT le travail ----
    # Volontairement non idempotent : deux régénérations du même article sont
    # deux commandes distinctes (cf. `debit_regeneration`). Débiter avant plutôt
    # qu'après, comme le pipeline le fait à l'étape 2bis, pour ne jamais livrer
    # un travail que le solde ne couvrait pas.
    entry_ids: list[str] = []
    if agency_id:
        entry_ids = await billing.debit_regeneration(
            agency_id=agency_id,
            run_id=run_id,
            articles=UNITS_PER_ARTICLE,
            plan=await billing.get_plan_for(agency_id),
            note=f"Régénération de l'article {slug}",
        )
    else:
        logger.warning("Régénération non facturée (run=%s, agency_id absent)", run_id)

    try:
        await _emit(30, "Rédaction en cours...")
        outcome = await regenerate_article(
            result, slug=slug, directives=directives
        )
    except ArticleNotFound:
        await _refund(billing, run_id, entry_ids, reason="article introuvable")
        raise
    except Exception as e:
        # Même règle que pour un run en échec : l'agence ne paie pas un travail
        # qu'elle n'a pas reçu. Le remboursement retourne dans les lots d'origine.
        await _refund(billing, run_id, entry_ids, reason=str(e))
        await repo.save_progress(
            run_id,
            JobProgress(
                step=PipelineStep.ARTICLE_GENERATION,
                percent=100,
                message=f"Échec de la régénération : {e}",
            ).model_dump(mode="json"),
        )
        logger.error(
            "Régénération de %s (run %s) en échec : %s\n%s",
            slug,
            run_id,
            e,
            traceback.format_exc(),
        )
        raise

    await _emit(90, "Maillage remis en conformité...")
    await repo.save_result(run_id, outcome.result.model_dump(mode="json"))

    # Le checkpoint de l'article est réaligné sur la nouvelle version. Sans ça,
    # une reprise ultérieure du run relirait l'ancienne rédaction et écraserait
    # en silence le travail que l'agence vient de payer.
    await _sync_checkpoint(run_id, slug, outcome)

    await _emit(100, f"Article « {slug} » régénéré.")
    logger.info(
        "Régénération terminée — run=%s slug=%s mode=%s facturé=%s maillage_intact=%s",
        run_id,
        slug,
        outcome.mode.value,
        bool(entry_ids),
        outcome.maillage_intact,
    )
    return {
        # Témoin de forme : le résultat d'un job de régénération n'est PAS un
        # `PipelineResult`. Sans ce drapeau, l'écran de résultat tenterait de le
        # rendre comme un livrable complet et n'afficherait rien.
        "regeneration": True,
        "run_id": run_id,
        "slug": slug,
        "mode": outcome.mode.value,
        "billed_units": UNITS_PER_ARTICLE if entry_ids else 0,
        "maillage_intact": outcome.maillage_intact,
        "missing_links": outcome.missing_links,
    }


async def _refund(billing, run_id: str, entry_ids: list[str], *, reason: str) -> None:
    """Annule le débit de CETTE régénération, et lui seul.

    Surtout pas `refund_run` : il ne cible que les `debit_generation`, donc il
    rembourserait le cocon d'origine du run — un cocon offert à chaque
    régénération ratée.
    """
    if not entry_ids:
        return
    try:
        await billing.reverse_entries(
            entry_ids, note="Remboursement — régénération d'article en échec"
        )
        logger.info("Régénération du run %s remboursée (%s)", run_id, reason)
    except Exception as refund_error:
        # Ne jamais masquer l'erreur d'origine : c'est elle que l'agence doit
        # voir. Un remboursement manqué se rattrape à la main depuis le journal.
        logger.error(
            "Remboursement de la régénération du run %s impossible (%s) — "
            "à reprendre à la main.",
            run_id,
            refund_error,
        )


async def _sync_checkpoint(run_id: str, slug: str, outcome) -> None:
    key = "brief" if outcome.mode is GenerationMode.BRIEF else "article"
    items = outcome.result.briefs if key == "brief" else outcome.result.articles
    fresh = next((i for i in items if i.stub.slug == slug), None)
    if fresh is None:
        return
    try:
        store = make_checkpoint_store(
            run_id, redis_url=REDIS_URL, repository=get_repository()
        )
        await store.set(f"{key}:{slug}", fresh.model_dump(mode="json"))
    except Exception as e:
        # Non fatal : le résultat persisté fait foi, le checkpoint n'est qu'un
        # cache de reprise. Mais on veut le savoir.
        logger.warning("Checkpoint %s:%s non réaligné (%s)", key, slug, e)
