"""Schémas Pydantic pour le pipeline de génération de cocons sémantiques."""

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


# ============================================================
# INPUT — Formulaire client
# ============================================================


class GenerationMode(str, Enum):
    BRIEF = "brief"
    FULL = "full"


class InterCoconPolicy(str, Enum):
    """Gouverne les liens entre cocons différents.

    L'étanchéité entre silos est le principe central de la méthode : une page
    du cocon A ne lie pas vers une page du cocon B, sous peine de rupture
    sémantique. Les cocons sont déjà reliés par le haut de l'arbre (accueil →
    page cible de chaque cocon), les relier latéralement est redondant.

    STRICT       — aucun lien inter-cocon (défaut, conforme à la méthode)
    MOTHERS_ONLY — mère ↔ mère uniquement, jamais les filles
    LIBRE        — pas de contrainte, on garde ce que le LLM a proposé
    """

    STRICT = "strict"
    MOTHERS_ONLY = "mothers_only"
    LIBRE = "libre"


class ExperienceElement(BaseModel):
    """Élément d'expérience uploadé par le client (obligatoire en mode FULL).

    `content` est repris **verbatim** dans l'article, en bloc cité et attribué —
    jamais paraphrasé par le modèle. C'est ce qui produit de vrais passages non
    générés (signal E-E-A-T réel, et seuls segments qu'un détecteur peut créditer
    comme humains). L'injection est faite en code, voir article_generator.py.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    type: Literal["case_study", "data", "screenshot", "insight", "quote"]
    title: str
    content: str
    source: str | None = None


class StyleSample(BaseModel):
    """Échantillon d'écriture existant du client, injecté en few-shot.

    Sert à caler la voix de marque au moment de la génération. Effet secondaire
    mesuré (Epoch AI, 2026) : conditionner sur ~5 passages d'auteur fait passer
    le taux de faux négatifs des détecteurs de ≤1 % à ~10 % (~25 % en rédaction
    technique) — un ordre de grandeur de plus que n'importe quel humanizer
    post-hoc, sans dégrader le texte.
    """

    title: str | None = None
    content: str = Field(..., min_length=200)
    source: str | None = None


# Le nombre de filles est décidé par le modèle au design du cocon (3 à 8, voir
# Cocon.daughters), donc le total exact n'est pas connu au moment du formulaire.
# 1 mère + 5 filles est le cas nominal : sert d'estimation pour prévenir l'agence.
TYPICAL_ARTICLES_PER_COCON = 6


class ExperienceCoverage(BaseModel):
    """Estimation de la couverture first-hand d'une run, calculée avant génération."""

    expected_articles: int
    elements_provided: int
    articles_capped: int = Field(
        ..., description="Articles qui n'auront aucun bloc verbatim → E-E-A-T plafonné à 40"
    )

    @property
    def is_short(self) -> bool:
        return self.articles_capped > 0


class ClientForm(BaseModel):
    product: str = Field(..., description="Nom du produit ou service du client final")
    description: str = Field(..., description="Description détaillée du produit/service")
    language: str = Field(default="fr", description="Langue cible ISO (fr, en, es...)")
    seed_keywords: list[str] = Field(..., min_length=1, max_length=20)
    audience: str = Field(..., description="Description de l'audience cible")
    niche: str = Field(..., description="Secteur / niche du client")
    num_cocoons: int = Field(default=2, ge=1, le=4)
    mode: GenerationMode = Field(default=GenerationMode.BRIEF)
    validate_keywords: bool = Field(
        default=True,
        description=(
            "Arrête le run après la recherche de mots-clés et attend une validation "
            "humaine avant de générer. Activé par défaut : une génération complète "
            "coûte des dizaines de minutes et quelques dollars, autant trancher la "
            "sélection avant de la payer."
        ),
    )
    inter_cocon_policy: InterCoconPolicy = Field(
        default=InterCoconPolicy.STRICT,
        description="Étanchéité entre cocons — strict par défaut (méthode Bourrelly)",
    )
    experience_elements: list[ExperienceElement] = Field(default_factory=list)
    style_samples: list[StyleSample] = Field(
        default_factory=list,
        max_length=5,
        description="Articles existants du client — few-shot pour caler la voix",
    )
    agency_id: str | None = None
    client_project_name: str | None = None

    def experience_coverage(self) -> "ExperienceCoverage":
        """Combien d'articles auront un vrai bloc first-hand, et combien seront plafonnés.

        Un `experience_element` n'est placé que dans **un seul** article de la run
        (unicité appliquée dans article_generator). Fournir 1 élément pour un cocon
        de 6 donne donc 1 article crédité et 5 plafonnés à 40 — comportement correct,
        mais qu'il faut annoncer AVANT la génération et pas laisser découvrir dans les
        scores, une fois les tokens dépensés.
        """
        expected = self.num_cocoons * TYPICAL_ARTICLES_PER_COCON
        available = len(self.experience_elements)
        return ExperienceCoverage(
            expected_articles=expected,
            elements_provided=available,
            articles_capped=max(0, expected - available),
        )


# ============================================================
# KEYWORD RESEARCH
# ============================================================


class SearchIntent(str, Enum):
    INFORMATIONAL = "informational"
    COMMERCIAL = "commercial"
    TRANSACTIONAL = "transactional"
    NAVIGATIONAL = "navigational"


class DifficultyBucket(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class KeywordCandidate(BaseModel):
    """KW proposé par Claude en expansion des seeds (sans données volume)."""

    keyword: str
    intent: SearchIntent
    cluster: str = Field(..., description="Nom du cluster thématique proposé par Claude")
    relative_volume: Literal["high", "medium", "low"]
    difficulty_estimate: DifficultyBucket


class SerpFeatures(BaseModel):
    """Features SERP présentes pour un mot clé."""

    featured_snippet: bool = False
    people_also_ask: list[str] = Field(default_factory=list)
    video_carousel: bool = False
    image_pack: bool = False
    local_pack: bool = False
    knowledge_panel: bool = False
    ads_top: int = 0
    ads_bottom: int = 0
    top_result_types: list[str] = Field(
        default_factory=list, description="Ex: ['guide', 'listicle', 'video']"
    )


class KeywordWithData(BaseModel):
    """KW enrichi avec données DataForSEO + analyse SERP."""

    keyword: str
    intent: SearchIntent
    cluster: str
    monthly_volume: int | None = None
    cpc: float | None = None
    competition_score: float | None = Field(default=None, ge=0, le=1)
    difficulty: int | None = Field(default=None, ge=0, le=100)
    serp_features: SerpFeatures | None = None


# ============================================================
# COCON STRUCTURE
# ============================================================


class ArticleType(str, Enum):
    MOTHER = "mother"
    DAUGHTER = "daughter"


class ArticleStub(BaseModel):
    """Métadonnées minimales d'un article — générées à l'étape design cocon."""

    cocon_id: str
    article_type: ArticleType
    target_keyword: str
    secondary_keywords: list[str] = Field(default_factory=list)
    h1_title: str
    meta_title: str = Field(..., max_length=70)
    meta_description: str = Field(..., max_length=160)
    slug: str
    intent: SearchIntent


class CoconStructure(BaseModel):
    """Un cocon = 1 mère + N filles (généralement 5)."""

    id: str
    theme: str = Field(..., description="Thème central du cocon en 1 phrase")
    main_keyword: str
    mother: ArticleStub
    daughters: list[ArticleStub] = Field(..., min_length=3, max_length=8)
    rationale: str = Field(..., description="Pourquoi ce cocon a été formé (SEO logic)")


# ============================================================
# SERP ANALYSIS (Surfer-like)
# ============================================================


class ScrapedPage(BaseModel):
    """Contenu d'une page top 10 scrapée."""

    url: str
    title: str
    word_count: int
    h1: str | None = None
    h2s: list[str] = Field(default_factory=list)
    h3s: list[str] = Field(default_factory=list)
    meta_description: str | None = None


class SerpAnalysis(BaseModel):
    """Brief calibré pour un article, basé sur analyse top 10 SERP."""

    keyword: str
    scraped_pages_count: int = Field(
        ..., description="Pages retenues comme référence (HTTP 200 ET contenu substantiel)"
    )
    serp_urls_count: int = Field(
        default=0, description="URLs organiques renvoyées par la SERP"
    )
    rejected_pages: dict[str, int] = Field(
        default_factory=dict,
        description="Motif de rejet -> nb d'URLs (ex: {'HTTP 403': 5, 'contenu vide (JS)': 2})",
    )
    low_sample: bool = Field(
        default=False,
        description="True si le brief est calibré sur trop peu de pages pour être fiable",
    )
    avg_word_count: int
    recommended_word_count: int
    avg_h2_count: int
    recommended_h2_count: int
    avg_h3_count: int
    key_entities: list[str] = Field(..., description="Entités nommées à couvrir")
    key_topics: list[str] = Field(..., description="Sous-thèmes récurrents dans top 10")
    common_questions: list[str] = Field(
        ..., description="Questions PAA + questions répondues par top 10"
    )
    content_gaps: list[str] = Field(
        default_factory=list, description="Angles NON couverts par top 10 — opportunité"
    )
    competitive_angle: str = Field(..., description="Angle unique à prendre pour se différencier")
    top_result_format: str = Field(..., description="Ex: guide long, listicle, comparatif, tuto")


# ============================================================
# ARTICLE GENERATION
# ============================================================


class ArticleSection(BaseModel):
    """Section H2 avec ses sous-sections H3 et points clés."""

    h2: str
    h3s: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    word_count_target: int = 300


class InternalLinkType(str, Enum):
    DAUGHTER_TO_MOTHER = "daughter_to_mother"
    MOTHER_TO_DAUGHTER = "mother_to_daughter"
    SISTER_TO_SISTER = "sister_to_sister"
    CROSS_COCON = "cross_cocon"


class InternalLink(BaseModel):
    """Lien interne — élément clé du maillage Bourrelly."""

    anchor_text: str
    target_slug: str
    target_h1: str
    link_type: InternalLinkType
    context: str = Field(..., description="Court extrait où le lien apparaît")
    justification: str = Field(..., description="Pourquoi ce lien est pertinent pour le lecteur")


class ExternalLink(BaseModel):
    """Suggestion de lien externe vers source autoritaire."""

    anchor_text: str
    url_suggestion: str | None = None
    domain_type: str = Field(..., description="Ex: étude scientifique, source officielle, média")
    reason: str


class FAQItem(BaseModel):
    question: str
    answer: str


class EEATScore(BaseModel):
    """Score E-E-A-T par article — critique pour éviter deindex."""

    experience: int = Field(..., ge=0, le=100, description="Expérience terrain démontrée")
    expertise: int = Field(..., ge=0, le=100, description="Expertise technique")
    authoritativeness: int = Field(..., ge=0, le=100, description="Sources et références")
    trustworthiness: int = Field(..., ge=0, le=100, description="Transparence, disclaimers")
    overall: int = Field(..., ge=0, le=100)
    warnings: list[str] = Field(default_factory=list, description="Points à améliorer avant publication")


class GeneratedArticle(BaseModel):
    """Article complet généré — sortie du mode FULL."""

    stub: ArticleStub
    serp_analysis: SerpAnalysis
    sections: list[ArticleSection]
    faq: list[FAQItem]
    internal_links: list[InternalLink]
    external_links: list[ExternalLink]
    eeat_score: EEATScore | None = None
    schema_jsonld: dict = Field(..., description="Schema.org JSON-LD Article + FAQ")
    content_markdown: str = Field(..., description="Article complet en Markdown")
    experience_used: list[str] = Field(
        default_factory=list,
        description="IDs des ExperienceElement repris verbatim dans cet article",
    )
    word_count: int


class ArticleBrief(BaseModel):
    """Brief éditorial — sortie du mode BRIEF (pas d'article rédigé)."""

    stub: ArticleStub
    serp_analysis: SerpAnalysis
    sections: list[ArticleSection]
    faq_questions: list[str]
    internal_links_plan: list[InternalLink]
    external_links_suggestions: list[ExternalLink]
    editorial_notes: str = Field(..., description="Instructions rédactionnelles pour le rédacteur")
    tone_guidance: str
    unique_angle: str


# ============================================================
# BACKLINK REPORT
# ============================================================


class AnchorRatio(BaseModel):
    """Ratio recommandé d'ancres pour un article."""

    exact: float = Field(..., description="% ancre exacte du KW cible")
    partial: float = Field(..., description="% ancre partielle")
    branded: float = Field(..., description="% ancre marque")
    naked_url: float = Field(..., description="% URL nue")
    generic: float = Field(..., description="% ancre générique (clic ici, en savoir plus)")


class CompetitorBacklinkSummary(BaseModel):
    competitor_url: str
    total_backlinks: int
    referring_domains: int
    domain_rating: int | None = None
    top_referring_types: list[str] = Field(default_factory=list)


class BacklinkOpportunity(BaseModel):
    referring_domain: str
    domain_rating: int | None = None
    reason: str = Field(..., description="Pourquoi ce site pourrait linker")
    suggested_anchor: str
    contact_email: str | None = None
    outreach_template_type: Literal["guest_post", "niche_edit", "resource_page"] = "guest_post"


class BacklinkReport(BaseModel):
    cocon_id: str
    competitor_analysis: list[CompetitorBacklinkSummary]
    opportunities: list[BacklinkOpportunity]
    recommended_anchor_ratio: AnchorRatio
    outreach_templates: dict[str, str] = Field(
        default_factory=dict, description="Templates prêts par type d'outreach"
    )


# ============================================================
# INTER-COCON MAILLAGE
# ============================================================


class InterCoconLink(BaseModel):
    from_cocon_id: str
    from_slug: str
    to_cocon_id: str
    to_slug: str
    anchor_text: str
    justification: str


class MaillageMap(BaseModel):
    """Map complète du maillage — clé = slug source, valeur = liens sortants."""

    links: dict[str, list[InternalLink]] = Field(default_factory=dict)
    inter_cocon_links: list[InterCoconLink] = Field(default_factory=list)


# ============================================================
# PIPELINE RESULT (sortie complète)
# ============================================================


class RunUsage(BaseModel):
    """Coût réel d'une run, mesuré et non estimé.

    Reprend le cumul de `AnthropicClient.usage` (défini côté client pour ne pas
    faire dépendre models.py du SDK) et y ajoute le coût DataForSEO.
    """

    claude_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    claude_cost_usd: float = 0.0
    claude_cost_by_tier: dict[str, float] = Field(default_factory=dict)
    cache_savings_usd: float = Field(
        default=0.0, description="Économie des lectures de cache vs plein tarif d'entrée"
    )
    dataforseo_cost_usd: float = 0.0

    @property
    def total_cost_usd(self) -> float:
        return round(self.claude_cost_usd + self.dataforseo_cost_usd, 4)


class PipelineResult(BaseModel):
    form: ClientForm
    keywords_researched: list[KeywordWithData]
    cocoons: list[CoconStructure]
    briefs: list[ArticleBrief] = Field(
        default_factory=list, description="Rempli en mode BRIEF"
    )
    articles: list[GeneratedArticle] = Field(
        default_factory=list, description="Rempli en mode FULL"
    )
    maillage_map: MaillageMap
    backlink_reports: list[BacklinkReport]
    usage: RunUsage | None = Field(
        default=None, description="Consommation et coût réels de la run"
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# JOB (pour RQ workers)
# ============================================================


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineStep(str, Enum):
    KEYWORD_RESEARCH = "keyword_research"
    COCON_DESIGN = "cocon_design"
    SERP_ANALYSIS = "serp_analysis"
    ARTICLE_GENERATION = "article_generation"
    MAILLAGE = "maillage"
    BACKLINKS = "backlinks"
    COMPLETE = "complete"


class JobProgress(BaseModel):
    step: PipelineStep
    percent: int = Field(..., ge=0, le=100)
    message: str
    current_item: str | None = None


class PipelineJob(BaseModel):
    id: str
    agency_id: str
    status: JobStatus
    progress: JobProgress | None = None
    result: PipelineResult | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
