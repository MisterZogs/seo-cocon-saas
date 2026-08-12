"""Régénération d'UN article d'un run déjà terminé (chantier 16b).

Second sens du « Mode Brief bidirectionnel » : l'agence lit ce qui est sorti,
donne de nouvelles consignes sur un article, et redemande la rédaction de
celui-là seul.

Trois décisions structurent ce module.

**1. On travaille à partir du `PipelineResult` stocké, pas des checkpoints.**
`ArticleBrief` et `GeneratedArticle` portent chacun leur `stub` et leur
`serp_analysis` : tout ce qu'il faut pour rejouer une génération est déjà dans
le résultat. Les checkpoints, eux, expirent (7 jours en Redis) — un run du mois
dernier doit rester régénérable.

**2. Le stub est réutilisé tel quel, donc le graphe de maillage ne bouge pas.**
Même slug, même mot-clé cible, même rôle : les liens *entrants* venant des sœurs
et de la mère restent valides sans être touchés. Seuls les liens *sortants* du
nouvel article sont neufs, et ils passent par la même normalisation que le reste.
C'est ce qui rend l'opération sûre : régénérer un article ne peut pas créer de
page orpheline, parce que rien de ce qui pointe vers elle n'est réécrit.

**3. Le maillage est renormalisé sur l'ENSEMBLE, pas seulement sur l'article.**
`assemble_maillage` est idempotent sur des liens déjà conformes (tous les liens
attendus sont présents, donc il n'ajoute rien et ne réécrit aucun marqueur), et
le rejouer en entier garantit l'invariant global plutôt qu'un raisonnement local
sur ce qui « devrait » suffire. Le coût est nul : aucun appel LLM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from clients.anthropic_client import AnthropicClient
from models import (
    ArticleBrief,
    ArticleStub,
    CoconStructure,
    GeneratedArticle,
    GenerationMode,
    PipelineResult,
    RunUsage,
)
from pipeline.article_generator import (
    ArticleGenerator,
    _build_cached_context,
    assign_experience_elements,
)
from pipeline.geo_score import score_articles_geo
from pipeline.maillage import assemble_maillage, audit_maillage

logger = logging.getLogger(__name__)


class ArticleNotFound(LookupError):
    """Le slug demandé n'appartient pas à ce run."""


@dataclass
class RegenerationOutcome:
    """Ce que la régénération a produit, pour la route et les logs."""

    result: PipelineResult
    slug: str
    mode: GenerationMode
    previous_directives: str | None
    new_directives: str | None
    maillage_intact: bool
    missing_links: list[str]


def regenerable_slugs(result: PipelineResult) -> list[str]:
    """Les articles de ce run qu'on peut régénérer, dans l'ordre du livrable."""
    return [item.stub.slug for item in [*result.briefs, *result.articles]]


def _locate_cocon(cocoons: list[CoconStructure], slug: str) -> CoconStructure:
    for cocon in cocoons:
        if slug in [cocon.mother.slug, *(d.slug for d in cocon.daughters)]:
            return cocon
    raise ArticleNotFound(f"Aucun cocon ne contient l'article « {slug} ».")


def _stubs_of(cocon: CoconStructure) -> list[ArticleStub]:
    return [cocon.mother, *cocon.daughters]


def _merge_usage(previous: RunUsage | None, anthropic: AnthropicClient) -> RunUsage:
    """Ajoute le coût de la régénération à celui du run.

    Le total cesse ainsi de décrire « une génération » pour décrire « ce que ce
    run a réellement coûté ». C'est le chiffre utile : les marges se mesurent sur
    l'argent dépensé, et une régénération non comptée les surestime. Le nombre
    d'appels (`claude_calls`) reste le témoin qu'il y a eu plus d'un passage.
    """
    u = anthropic.usage
    base = previous or RunUsage()
    by_tier = dict(base.claude_cost_by_tier)
    for tier, cost in u.by_tier.items():
        by_tier[tier] = by_tier.get(tier, 0.0) + cost

    return RunUsage(
        claude_calls=base.claude_calls + u.calls,
        input_tokens=base.input_tokens + u.input_tokens,
        output_tokens=base.output_tokens + u.output_tokens,
        cache_creation_tokens=base.cache_creation_tokens + u.cache_creation_tokens,
        cache_read_tokens=base.cache_read_tokens + u.cache_read_tokens,
        claude_cost_usd=base.claude_cost_usd + u.cost_usd,
        claude_cost_by_tier=by_tier,
        cache_savings_usd=base.cache_savings_usd + u.cache_savings_usd,
        # La régénération ne rappelle pas DataForSEO : l'analyse SERP est relue
        # depuis le brief/article existant, elle n'est pas repayée.
        dataforseo_cost_usd=base.dataforseo_cost_usd,
    )


async def regenerate_article(
    result: PipelineResult,
    *,
    slug: str,
    directives: str | None,
    anthropic: AnthropicClient | None = None,
) -> RegenerationOutcome:
    """Régénère l'article `slug` et rend le résultat complet, maillage remis d'aplomb.

    `result` est modifié sur place (comme le fait déjà `assemble_maillage`) et
    retourné, pour que l'appelant n'ait qu'un objet à persister.
    """
    anthropic = anthropic or AnthropicClient()

    briefs_by_slug = {b.stub.slug: b for b in result.briefs}
    articles_by_slug = {a.stub.slug: a for a in result.articles}
    if slug not in briefs_by_slug and slug not in articles_by_slug:
        raise ArticleNotFound(
            f"L'article « {slug} » n'existe pas dans ce run. "
            f"Articles disponibles : {', '.join(regenerable_slugs(result)) or 'aucun'}."
        )

    is_brief = slug in briefs_by_slug
    previous = briefs_by_slug.get(slug) or articles_by_slug[slug]
    previous_directives = previous.stub.directives

    cocon = _locate_cocon(result.cocoons, slug)

    # La consigne est posée sur le stub porté par `result.cocoons` ET sur celui
    # que l'on passe au générateur : après désérialisation ce sont deux objets
    # distincts, et c'est `result.cocoons` qui sera relu à la prochaine
    # régénération. Les laisser diverger ferait « oublier » la consigne au
    # deuxième passage.
    stub = next(s for s in _stubs_of(cocon) if s.slug == slug)
    stub.directives = directives
    previous.stub.directives = directives

    generator = ArticleGenerator(anthropic)
    cached_context = _build_cached_context(result.form, result.cocoons)
    assigned = assign_experience_elements(result.form, result.cocoons)

    if is_brief:
        fresh = await generator._generate_brief_one(
            stub,
            cocon,
            previous.serp_analysis,
            result.cocoons,
            cached_context,
            result.form.inter_cocon_policy,
            assigned.get(slug, []),
        )
        result.briefs = [fresh if b.stub.slug == slug else b for b in result.briefs]
        mode = GenerationMode.BRIEF
    else:
        # Les éléments d'expérience placés dans les AUTRES articles restent
        # verrouillés — l'unicité sur la run est ce qui empêche le même bloc
        # verbatim d'apparaître six fois dans le silo. Ceux de l'article qu'on
        # réécrit sont au contraire relâchés : ils lui appartenaient.
        used_elsewhere = {
            eid
            for a in result.articles
            if a.stub.slug != slug
            for eid in a.experience_used
        }
        fresh = await generator._generate_full_one(
            stub,
            cocon,
            previous.serp_analysis,
            result.cocoons,
            cached_context,
            result.form,
            used_elsewhere,
            assigned.get(slug, []),
        )
        result.articles = [fresh if a.stub.slug == slug else a for a in result.articles]
        mode = GenerationMode.FULL

    # Remise en conformité du maillage sur l'ensemble. Les liens sortants du
    # nouvel article sortent du LLM et ne respectent rien ; ceux des autres
    # pages sont déjà conformes et traversent la normalisation sans changer.
    result.maillage_map = assemble_maillage(
        briefs=result.briefs,
        articles=result.articles,
        cocoons=result.cocoons,
        policy=result.form.inter_cocon_policy,
    )

    # Comme pour le maillage, on renote TOUS les articles et pas seulement celui
    # qu'on vient de réécrire : la normalisation a pu toucher le markdown des
    # sœurs, et le calcul ne coûte rien.
    score_articles_geo(result.articles)

    audit = audit_maillage(result.maillage_map, result.cocoons)
    missing = list(audit["missing_required"])
    orphans = list(audit["orphans"])
    intact = not missing and not orphans
    if not intact:
        # Ne doit jamais arriver : la normalisation complète justement ce qui
        # manque. Si ça arrive, c'est un défaut du code de maillage, pas du LLM —
        # et il faut le voir dans les logs plutôt que de livrer un cocon percé.
        logger.error(
            "Maillage incomplet APRÈS régénération de %s — liens manquants: %s, "
            "orphelins: %s",
            slug,
            missing,
            orphans,
        )

    result.usage = _merge_usage(result.usage, anthropic)

    logger.info(
        "Article %s régénéré (%s) — consignes: %s → %s | maillage: %d liens, intact=%s",
        slug,
        mode.value,
        "aucune" if not previous_directives else f"{len(previous_directives)} car.",
        "aucune" if not directives else f"{len(directives)} car.",
        sum(len(v) for v in result.maillage_map.links.values()),
        intact,
    )

    return RegenerationOutcome(
        result=result,
        slug=slug,
        mode=mode,
        previous_directives=previous_directives,
        new_directives=directives,
        maillage_intact=intact,
        missing_links=missing + [f"orphelin: {o}" for o in orphans],
    )
