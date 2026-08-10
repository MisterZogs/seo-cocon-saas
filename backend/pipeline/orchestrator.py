"""Orchestrateur du pipeline complet — asynchrone, testable.

Émet des `JobProgress` via une callback pour supporter SSE/temps réel.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from clients.anthropic_client import AnthropicClient
from clients.dataforseo_client import DataForSEOClient
from db.checkpoints import CheckpointStore, NullCheckpointStore
from models import (
    ArticleBrief,
    BacklinkReport,
    ClientForm,
    CoconStructure,
    GeneratedArticle,
    GenerationMode,
    JobProgress,
    KeywordWithData,
    PipelineResult,
    PipelineStep,
    RunUsage,
    SerpAnalysis,
    ValidationSnapshot,
)
from pipeline.article_generator import ArticleGenerator
from pipeline.backlink_analyzer import BacklinkAnalyzer
from pipeline.cocon_builder import CoconBuilder
from pipeline.keyword_research import KeywordResearcher
from pipeline.maillage import assemble_maillage
from pipeline.serp_analyzer import SerpAnalyzer
from pipeline.validation import build_snapshot

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[JobProgress], Awaitable[None]] | None

# Appelée une fois par run, avec le nombre de cocons réellement construits, au
# moment précis où le run devient facturable. L'orchestrateur ne sait rien du
# solde ni des lots : il signale l'événement, le worker décide quoi en faire.
BillableCallback = Callable[[int], Awaitable[None]] | None


class AwaitingValidation(Exception):
    """Le run est suspendu : l'agence doit valider la sélection de mots-clés.

    Ce n'est pas une erreur, c'est une pause. Le worker l'attrape et termine le
    job proprement plutôt que de le marquer en échec — un job « failed » serait
    proposé à la reprise automatique, ce qu'on ne veut surtout pas ici.

    Porter le snapshot dans l'exception évite de le recalculer côté API : le
    worker le persiste et l'écran de validation le relit tel quel.
    """

    def __init__(self, snapshot: ValidationSnapshot) -> None:
        super().__init__("Validation de la sélection de mots-clés requise.")
        self.snapshot = snapshot


async def _no_op(_: JobProgress) -> None:
    return None


async def _checkpointed(
    store: CheckpointStore,
    key: str,
    *,
    produce: Callable[[], Awaitable[Any]],
    dump: Callable[[Any], Any],
    load: Callable[[Any], Any],
) -> Any:
    """Exécute une étape, ou la relit si elle a déjà tourné sur ce run.

    Un checkpoint illisible (schéma changé entre deux versions, JSON tronqué)
    n'est jamais fatal : on relance simplement l'étape.
    """
    cached = await store.get(key)
    if cached is not None:
        try:
            value = load(cached)
            logger.info("↻ Étape %s reprise du checkpoint", key)
            return value
        except Exception as e:
            logger.warning("Checkpoint %s illisible (%s) — l'étape est rejouée.", key, e)

    value = await produce()
    await store.set(key, dump(value))
    return value


async def run_pipeline(
    form: ClientForm,
    *,
    anthropic: AnthropicClient | None = None,
    dataforseo: DataForSEOClient | None = None,
    on_progress: ProgressCallback = None,
    on_billable: BillableCallback = None,
    store: CheckpointStore | None = None,
    run_id: str | None = None,
) -> PipelineResult:
    """Exécute le pipeline complet et retourne le PipelineResult final.

    Étapes :
    1. Keyword Research
    2. Cocon Building
    3. SERP Analysis (par article)
    4. Article Generation (Brief ou Full selon form.mode)
    5. Maillage assembly
    6. Backlink Analysis (par cocon)

    Si `store` est fourni, chaque étape est checkpointée : relancer le pipeline
    sur le même run reprend là où il s'était arrêté, sans repayer les appels
    LLM déjà effectués.
    """
    anthropic = anthropic or AnthropicClient()
    dataforseo = dataforseo or DataForSEOClient()
    emit = on_progress or _no_op
    store = store or NullCheckpointStore()

    # ============ 1. Keyword Research ============
    await emit(
        JobProgress(
            step=PipelineStep.KEYWORD_RESEARCH,
            percent=5,
            message="Analyse et expansion des mots-clés cibles...",
        )
    )
    kw_researcher = KeywordResearcher(anthropic, dataforseo)
    keywords, cocoon_proposals = await _checkpointed(
        store,
        "keyword_research",
        produce=lambda: kw_researcher.research(form),
        dump=lambda v: {
            "keywords": [k.model_dump(mode="json") for k in v[0]],
            "proposals": v[1],
        },
        load=lambda d: (
            [KeywordWithData.model_validate(k) for k in d["keywords"]],
            d["proposals"],
        ),
    )
    logger.info("[1/6] Keywords: %d, propositions cocons: %d", len(keywords), len(cocoon_proposals))

    # ============ 1bis. Validation humaine (optionnelle) ============
    # La porte se ferme tant que `cocon_design` n'est pas checkpointé. C'est
    # exactement le bon témoin : la route de validation écrit ce checkpoint à
    # partir des choix de l'agence, donc le run relancé passe tout droit ici
    # sans qu'aucun état supplémentaire n'ait à être suivi.
    if form.validate_keywords and await store.get("cocon_design") is None:
        snapshot = build_snapshot(run_id or "", keywords, cocoon_proposals)
        await emit(
            JobProgress(
                step=PipelineStep.AWAITING_VALIDATION,
                percent=15,
                message=(
                    f"{len(snapshot.proposals)} cocon(s) proposé(s) — "
                    "en attente de votre validation avant génération."
                ),
            )
        )
        logger.info(
            "[1bis] Run suspendu pour validation : %d cocon(s), %d KW dans le pool",
            len(snapshot.proposals),
            len(keywords),
        )
        raise AwaitingValidation(snapshot)

    # ============ 2. Cocon Building ============
    await emit(
        JobProgress(
            step=PipelineStep.COCON_DESIGN,
            percent=15,
            message=f"Construction de {len(cocoon_proposals)} cocons sémantiques...",
        )
    )
    builder = CoconBuilder()

    async def _build_cocoons() -> list[CoconStructure]:
        # La validation est ici, pas après le checkpoint : sinon un résultat
        # vide serait sauvegardé, puis relu à chaque reprise — le run
        # échouerait indéfiniment sans jamais retenter la construction.
        built = builder.build(cocoon_proposals)
        if not built:
            raise RuntimeError("Aucun cocon valide produit par le LLM.")
        return built

    # Checkpointé bien que peu coûteux : `cocon_id` vient d'un uuid4(), donc
    # rejouer cette étape lors d'une reprise réattribuerait de nouveaux ids et
    # les articles déjà générés pointeraient vers des cocons fantômes (tout le
    # maillage basculerait en cross_cocon).
    cocoons = await _checkpointed(
        store,
        "cocon_design",
        produce=_build_cocoons,
        dump=lambda v: [c.model_dump(mode="json") for c in v],
        load=lambda d: [CoconStructure.model_validate(c) for c in d],
    )
    logger.info("[2/6] Cocons construits: %d", len(cocoons))

    # ============ 3. SERP Analysis ============
    all_stubs = [stub for cocon in cocoons for stub in [cocon.mother, *cocon.daughters]]
    await emit(
        JobProgress(
            step=PipelineStep.SERP_ANALYSIS,
            percent=25,
            message=f"Analyse SERP top 10 pour {len(all_stubs)} articles (Surfer-like)...",
        )
    )
    serp_analyzer = SerpAnalyzer(anthropic, dataforseo)
    serp_analyses = await _checkpointed(
        store,
        "serp_analysis",
        produce=lambda: serp_analyzer.analyze_all(all_stubs, form.language),
        dump=lambda v: {slug: a.model_dump(mode="json") for slug, a in v.items()},
        load=lambda d: {
            slug: SerpAnalysis.model_validate(a) for slug, a in d.items()
        },
    )
    logger.info("[3/6] SERP analyses: %d/%d", len(serp_analyses), len(all_stubs))

    # ============ 4. Article Generation ============
    briefs: list[ArticleBrief] = []
    articles: list[GeneratedArticle] = []
    generator = ArticleGenerator(anthropic)

    if form.mode == GenerationMode.BRIEF:
        await emit(
            JobProgress(
                step=PipelineStep.ARTICLE_GENERATION,
                percent=50,
                message=f"Rédaction des briefs éditoriaux ({len(all_stubs)} articles)...",
            )
        )
        # Checkpoint par article (dans le générateur), pas par étape : la boucle
        # est séquentielle, on ne veut pas perdre les briefs déjà produits.
        briefs = await generator.generate_all_briefs(
            form, cocoons, serp_analyses, store=store
        )
        logger.info("[4/6] Briefs générés: %d", len(briefs))
    else:
        await emit(
            JobProgress(
                step=PipelineStep.ARTICLE_GENERATION,
                percent=50,
                message=f"Rédaction complète de {len(all_stubs)} articles...",
            )
        )
        articles = await generator.generate_all_articles(
            form, cocoons, serp_analyses, store=store
        )
        logger.info("[4/6] Articles générés: %d", len(articles))

    # ============ 5. Maillage Assembly ============
    await emit(
        JobProgress(
            step=PipelineStep.MAILLAGE,
            percent=80,
            message="Assemblage et mise en conformité du maillage interne...",
        )
    )
    maillage_map = assemble_maillage(
        briefs=briefs,
        articles=articles,
        cocoons=cocoons,
        policy=form.inter_cocon_policy,
    )
    logger.info(
        "[5/6] Maillage: %d nœuds, %d liens inter-cocons",
        len(maillage_map.links),
        len(maillage_map.inter_cocon_links),
    )

    # ============ 6. Backlink Analysis ============
    await emit(
        JobProgress(
            step=PipelineStep.BACKLINKS,
            percent=90,
            message=f"Analyse concurrentielle backlinks pour {len(cocoons)} cocons...",
        )
    )
    bl_analyzer = BacklinkAnalyzer(anthropic, dataforseo)
    backlink_reports = await _checkpointed(
        store,
        "backlinks",
        produce=lambda: bl_analyzer.analyze_all(form, cocoons),
        dump=lambda v: [r.model_dump(mode="json") for r in v],
        load=lambda d: [BacklinkReport.model_validate(r) for r in d],
    )
    logger.info("[6/6] Rapports backlinks: %d", len(backlink_reports))

    # ============ Result ============
    await emit(
        JobProgress(
            step=PipelineStep.COMPLETE,
            percent=100,
            message="Pipeline terminé.",
        )
    )
    u = anthropic.usage
    usage = RunUsage(
        claude_calls=u.calls,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_creation_tokens=u.cache_creation_tokens,
        cache_read_tokens=u.cache_read_tokens,
        claude_cost_usd=u.cost_usd,
        claude_cost_by_tier=u.by_tier,
        cache_savings_usd=u.cache_savings_usd,
        dataforseo_cost_usd=dataforseo.cost_usd,
    )
    logger.info(
        "Coût de la run : $%.4f (Claude $%.4f sur %d appels, DataForSEO $%.4f) "
        "— caching économisé $%.4f",
        usage.total_cost_usd,
        usage.claude_cost_usd,
        usage.claude_calls,
        usage.dataforseo_cost_usd,
        usage.cache_savings_usd,
    )

    return PipelineResult(
        form=form,
        keywords_researched=keywords,
        cocoons=cocoons,
        briefs=briefs,
        articles=articles,
        maillage_map=maillage_map,
        backlink_reports=backlink_reports,
        usage=usage,
    )
