"""RQ worker : audit du maillage d'un site existant (chantier 14).

Job séparé du pipeline, pour deux raisons de fond :

· **Il ne coûte rien.** Aucun appel LLM, aucun appel DataForSEO — seulement de
  la bande passante et du temps de worker. Il n'est donc pas débité, et il n'a
  rien à faire du ledger.
· **Il n'a pas de run.** Il ne produit ni cocon, ni article, ni maillage à
  normaliser : rien à persister dans `runs`, rien à reprendre sur checkpoint.

⚠️ Le worker RQ est **unique** et traite un job à la fois : un crawl occupe donc
la file pendant toute sa durée. C'est la vraie raison du plafond `max_pages`,
bien plus que la politesse envers le site audité.
"""

from __future__ import annotations

import asyncio
import logging
import traceback

from rq import get_current_job

from models import SiteAuditRequest
from pipeline.site_audit import audit_site

logger = logging.getLogger(__name__)


def run_site_audit_job(payload: dict) -> dict:
    """Point d'entrée RQ (sync) qui enveloppe l'audit async.

    `payload` porte l'`agency_id` **à l'intérieur du dict** : c'est ce qui permet
    à `_job_agency_id` de retrouver le propriétaire sans signature dédiée, et à
    `_job_run_id` de rendre None — un audit ne se rattache à aucun run.
    """
    return asyncio.run(_audit_async(payload))


async def _audit_async(payload: dict) -> dict:
    job = get_current_job()
    data = {k: v for k, v in payload.items() if k != "agency_id"}
    request = SiteAuditRequest.model_validate(data)

    def _emit(done: int, total: int) -> None:
        if job is None:
            return
        # Le total bouge pendant un crawl sans sitemap (la file s'allonge à
        # mesure qu'on découvre des liens). On borne donc le pourcentage plutôt
        # que d'afficher une progression qui recule.
        pct = min(95, round(done * 100 / total)) if total else 5
        job.meta["progress"] = {
            "step": "site_audit",
            "percent": pct,
            "message": f"{done} page(s) analysée(s) sur {total} connue(s)…",
        }
        job.save_meta()

    async def on_progress(done: int, total: int) -> None:
        _emit(done, total)

    try:
        report = await audit_site(request, on_progress=on_progress)
    except ValueError as e:
        # URL invalide : faute de saisie, pas panne. Le message doit remonter
        # tel quel à l'agence plutôt qu'en trace Python.
        logger.warning("Audit refusé : %s", e)
        raise
    except Exception:
        logger.error("Audit en échec :\n%s", traceback.format_exc())
        raise

    if job is not None:
        job.meta["progress"] = {
            "step": "site_audit",
            "percent": 100,
            "message": f"Audit terminé — {report.pages_crawled} page(s) analysée(s).",
        }
        job.save_meta()

    logger.info(
        "Audit terminé %s — %d pages, %d liens, %d orphelines, réciprocité %.0f%%",
        report.start_url,
        report.pages_crawled,
        report.total_internal_links,
        len(report.orphans),
        report.reciprocity_rate * 100,
    )

    # Le drapeau `site_audit` distingue ce résultat d'un livrable de cocon dans
    # `/jobs/{id}`, exactement comme `regeneration` le fait pour le chantier 16b.
    return {"site_audit": True, "report": report.model_dump(mode="json")}
