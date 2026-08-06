"""Étape 5 du pipeline : Génération d'articles (mode Brief ou Full).

Mode BRIEF  → ArticleBrief pour rédacteur humain (structure + sections + FAQ + maillage)
Mode FULL   → GeneratedArticle avec markdown complet + FAQ + maillage + schema JSON-LD + score E-E-A-T

Modèles :
- Mode BRIEF : Sonnet pour tous les articles
- Mode FULL  : Opus pour l'article mère, Sonnet pour les filles

Prompt caching : le "cocon reference context" (liste de tous les articles avec slugs)
est mis en cache car réutilisé pour chaque génération d'article de la run.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from clients.anthropic_client import AnthropicClient, ModelTier
from db.checkpoints import CheckpointStore
from models import (
    ArticleBrief,
    ArticleSection,
    ArticleStub,
    ArticleType,
    ClientForm,
    CoconStructure,
    EEATScore,
    ExperienceElement,
    ExternalLink,
    FAQItem,
    GeneratedArticle,
    InterCoconPolicy,
    InternalLink,
    InternalLinkType,
    SerpAnalysis,
)

logger = logging.getLogger(__name__)


# ============================================================
# CHECKPOINTS PAR ARTICLE
# ============================================================
#
# Les deux boucles de génération sont séquentielles : sans checkpoint, une
# erreur sur le 7e article jette les 6 précédents, déjà payés en tokens Opus
# et Sonnet. On sauvegarde donc chaque article dès qu'il est produit.


async def _resume_one(store: CheckpointStore | None, key: str, model_cls):
    """Relit un article déjà généré. Un checkpoint corrompu est ignoré."""
    if store is None:
        return None
    cached = await store.get(key)
    if cached is None:
        return None
    try:
        resumed = model_cls.model_validate(cached)
    except Exception as e:
        logger.warning("Checkpoint %s illisible (%s) — régénération.", key, e)
        return None
    logger.info("↻ %s repris du checkpoint (pas de nouvel appel LLM)", key)
    return resumed


async def _checkpoint_one(store: CheckpointStore | None, key: str, value) -> None:
    if store is None:
        return
    await store.set(key, value.model_dump(mode="json"))


# ============================================================
# CONTEXTE PARTAGÉ (cacheable)
# ============================================================


def _build_cocon_reference(cocoons: list[CoconStructure]) -> str:
    """Contexte partagé entre toutes les générations — cacheable par Claude."""
    lines = ["# COCON REFERENCE (all articles across all cocoons of this run)\n"]
    lines.append(
        "Each article has a unique `slug`. When you propose internal links, "
        "you MUST reference a slug from the list below (never invent one).\n"
    )
    for cocon in cocoons:
        lines.append(f"\n## COCON {cocon.id} — Theme: {cocon.theme}")
        lines.append(f"Main KW: {cocon.main_keyword}")
        lines.append(f"Rationale: {cocon.rationale}")
        m = cocon.mother
        lines.append(
            f"\n  MOTHER  → slug=`{m.slug}` | H1=«{m.h1_title}» | KW=«{m.target_keyword}» | intent={m.intent.value}"
        )
        for d in cocon.daughters:
            lines.append(
                f"  DAUGHTER→ slug=`{d.slug}` | H1=«{d.h1_title}» | KW=«{d.target_keyword}» | intent={d.intent.value}"
            )
    return "\n".join(lines)


def _build_brand_context(form: ClientForm) -> str:
    return (
        f"# CLIENT CONTEXT\n"
        f"Product/Service: {form.product}\n"
        f"Description: {form.description}\n"
        f"Language: {form.language}\n"
        f"Target audience: {form.audience}\n"
        f"Niche: {form.niche}\n"
        f"Current year: {date.today().year}"
        f"{_build_experience_context(form)}"
    )


def _build_experience_context(form: ClientForm) -> str:
    """Catalogue des éléments d'expérience, avec la règle du verbatim.

    Le modèle voit le contenu intégral pour pouvoir écrire une amorce cohérente,
    mais il ne doit jamais le recopier ni le reformuler : il pose un marqueur,
    et `inject_experience_blocks()` substitue le texte exact du client.
    """
    if not form.experience_elements:
        return ""

    lines = [
        "\n\n# EXPERIENCE ELEMENTS (first-hand material provided by the client)",
        "",
        "These are the ONLY parts of the article that carry genuine first-hand",
        "experience. They must reach the reader in the client's own words.",
        "",
        "RULES — non-negotiable:",
        "1. NEVER paraphrase, summarize, rewrite, translate or quote this material.",
        "2. Where an element belongs, emit a marker ALONE on its own line:",
        "     [[EXPERIENCE:<id>]]",
        "   The exact client text is substituted there afterwards, as an attributed",
        "   block quote. Anything you write yourself would destroy its value.",
        "3. Write a lead-in sentence BEFORE the marker that sets up what follows,",
        "   and a takeaway sentence AFTER it that draws the lesson. Those are yours.",
        "4. Use each element AT MOST ONCE across the whole article, only where it",
        "   genuinely supports the point. A forced placement is worse than none.",
        "5. Count the block's words in your word_count target.",
        "",
        "Available elements:",
    ]
    for e in form.experience_elements:
        source = f" | source: {e.source}" if e.source else ""
        lines.append(f'\n- id=`{e.id}` | type={e.type} | title="{e.title}"{source}')
        lines.append(f"  content (DO NOT REPRODUCE — reference by marker only):")
        lines.append(f"  «{e.content[:2000]}»")
    return "\n".join(lines)


def _build_cached_context(form: ClientForm, cocoons: list[CoconStructure]) -> str:
    """Préfixe partagé par toutes les générations de la run — mis en cache.

    Les échantillons de style sont volumineux et identiques d'un article à
    l'autre : sans le caching ils seraient refacturés plein tarif 12 fois.
    """
    blocks = [_build_brand_context(form), _build_cocon_reference(cocoons)]
    style = _build_style_context(form)
    if style:
        blocks.insert(0, style)
    return "\n\n".join(blocks)


def _build_style_context(form: ClientForm) -> str:
    """Few-shot sur les écrits existants du client — cale la voix de marque.

    Le contexte est mis en cache et partagé par tous les articles de la run, donc
    les échantillons ne sont facturés plein tarif qu'une fois.
    """
    if not form.style_samples:
        return ""

    lines = [
        "# AUTHOR VOICE — reference samples",
        "",
        "The passages below were written by the client's own team. They define the",
        "voice you must write in. Study them for:",
        "- sentence rhythm and length variation (including deliberately short ones)",
        "- vocabulary level, jargon tolerance, and recurring turns of phrase",
        "- how the author opens, transitions, and closes",
        "- degree of directness, use of first person, humour, rhetorical questions",
        "- formatting habits: paragraph length, list frequency, emphasis density",
        "",
        "Match this voice. Do NOT copy their sentences, topics or examples — only",
        "their manner of writing. If the samples conflict with generic 'good SEO",
        "writing' habits, the samples win.",
    ]
    for i, s in enumerate(form.style_samples, 1):
        header = f'\n## Sample {i}' + (f' — "{s.title}"' if s.title else "")
        lines.append(header)
        lines.append(f"\n{s.content[:6000]}")
    return "\n".join(lines)


# ============================================================
# INJECTION VERBATIM DES ÉLÉMENTS D'EXPÉRIENCE
# ============================================================
#
# Même principe que la normalisation du maillage : ce qui doit être exact est
# fait en code, pas confié au prompt. Le modèle place un marqueur, on substitue
# le texte du client au caractère près. C'est la seule façon de garantir que
# l'article contient de vrais passages non générés.

_EXPERIENCE_MARKER = re.compile(r"^[ \t]*\[\[EXPERIENCE:([^\]]+)\]\][ \t]*$", re.MULTILINE)

_TYPE_LABELS = {
    "case_study": "Cas client",
    "data": "Données propriétaires",
    "screenshot": "Capture",
    "insight": "Retour de terrain",
    "quote": "Verbatim",
}


def _format_experience_block(element: ExperienceElement) -> str:
    """Bloc cité et attribué — visuellement distinct du corps rédigé."""
    label = _TYPE_LABELS.get(element.type, element.type)
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in element.content.splitlines())
    attribution = f"> — {label} : {element.title}"
    if element.source:
        attribution += f" ({element.source})"
    return f"{quoted}\n>\n{attribution}"


def inject_experience_blocks(
    markdown: str,
    elements: list[ExperienceElement],
    already_used: set[str] | None = None,
) -> tuple[str, list[str], list[str]]:
    """Remplace les marqueurs par le contenu client verbatim.

    Retourne (markdown, ids utilisés, ids jamais placés). Un marqueur qui pointe
    vers un id inconnu est retiré — laisser un `[[EXPERIENCE:xxx]]` dans le
    livrable serait pire que de perdre le bloc.

    `already_used` porte les ids déjà consommés par les articles précédents de la
    run. Sans ce garde-fou le modèle place le même élément dans TOUS les articles :
    mesuré sur un run réel, un unique bloc se retrouvait dans les 6 articles du
    cocon, soit du contenu dupliqué à l'intérieur du silo. Un élément d'expérience
    est unique par nature, il n'a de valeur qu'à un seul endroit.
    """
    by_id = {e.id: e for e in elements}
    consumed = set(already_used or ())
    used: list[str] = []

    def _replace(match: re.Match) -> str:
        element_id = match.group(1).strip()
        element = by_id.get(element_id)
        if element is None:
            logger.warning("Marqueur d'expérience inconnu, retiré : %s", element_id)
            return ""
        if element_id in consumed:
            logger.info(
                "Élément %s déjà placé dans un article précédent — marqueur retiré", element_id
            )
            return ""
        if element_id in used:
            logger.warning("Élément d'expérience %s référencé 2×, 2e occurrence retirée", element_id)
            return ""
        used.append(element_id)
        return _format_experience_block(element)

    result = _EXPERIENCE_MARKER.sub(_replace, markdown)
    unused = [e.id for e in elements if e.id not in used]
    return result, used, unused


# ============================================================
# PROMPTS COMMUNS
# ============================================================


_CROSS_COCON_RULE = {
    InterCoconPolicy.STRICT: (
        "- CROSS-COCON: FORBIDDEN. Silos are watertight — a page of cocon A never "
        "links to a page of cocon B. The cocoons are already joined at the TOP of "
        "the tree (homepage → each cocon's target page); linking them laterally "
        "breaks the semantic silo."
    ),
    InterCoconPolicy.MOTHERS_ONLY: (
        "- CROSS-COCON: mother → mother ONLY, and only where the themes genuinely "
        "meet. Daughters never link outside their own cocon."
    ),
    InterCoconPolicy.LIBRE: (
        "- CROSS-COCON: allowed where it genuinely helps the reader. Keep it rare."
    ),
}


def _maillage_rules(policy: InterCoconPolicy) -> str:
    return f"""MAILLAGE RULES (Bourrelly cocon sémantique method — applied strictly):
- DAUGHTER → MOTHER: MANDATORY, anchor on the mother's target KW (or close variant)
- MOTHER → all its DAUGHTERS: MANDATORY, one link per daughter with contextual anchor
- SISTER ↔ SISTER: MANDATORY and RECIPROCAL — every daughter links to EVERY other
  daughter of its own cocon. Transversal meshing is part of the method, not an
  option reserved for "justified" cases. In a cocon of 1 mother + 5 daughters this
  yields 30 links, each page receiving exactly 5 inbound ones.
{_CROSS_COCON_RULE[policy]}
- Anchor text: prioritize reader clarity, then keyword optimization. Vary anchors —
  never reuse the same wording twice for the same target. Avoid over-optimized
  exact-match anchors.
- Every internal link MUST target an existing slug from the cocon reference.
- Every link needs a justification (why this helps the reader)."""


_BRIEF_SYSTEM = """You are an expert SEO content strategist producing detailed editorial briefs for human writers.
Your briefs are so detailed and calibrated that a competent writer can produce a top-ranking article from them.

You write for a French SEO agency SaaS platform serving other agencies. Their end-clients publish these articles.

You know the Bourrelly cocon sémantique method and apply it rigorously to internal linking.

When author voice samples are provided, your tone_guidance and editorial_notes must
describe THAT voice concretely (rhythm, vocabulary, habits) so the writer can match it,
rather than giving generic editorial advice."""


_FULL_SYSTEM = """You are an expert SEO writer and content strategist. You produce publish-ready articles in Markdown,
following a strict semantic cocoon structure (Bourrelly method).

Your articles:
- Match the SERP intent exactly
- Answer the reader's question directly in the first 100 words (AI Overviews optimization)
- Are well-structured with H2/H3, lists, tables when appropriate
- Cite external authoritative sources (1-3 per article)
- Include internal maillage links per the Bourrelly rules provided
- Are calibrated to the SERP analysis (word count, entities to cover, questions to answer)

VOICE — this overrides every other stylistic instinct you have. When author voice
samples are supplied, you write as that author, not as a generic SEO copywriter.
Concretely, avoid the tells of machine-written prose: uniform sentence length,
paragraphs that all run the same size, tricolon ("X, Y, and Z") as a default rhythm,
hedging modifiers stacked on every claim, section openers that restate the heading,
conclusions that summarize what was just read, and blanket emphasis on key terms.
Vary. Commit to claims. Let some sentences be short.

You write for a French SEO agency SaaS platform. The end-audience of the article is the client's target audience."""


# ============================================================
# PROMPT BUILDERS
# ============================================================


def _serp_analysis_block(analysis: SerpAnalysis) -> str:
    return f"""SERP ANALYSIS (Surfer-like brief):
- Target keyword: {analysis.keyword}
- Top result format: {analysis.top_result_format}
- Recommended word count: {analysis.recommended_word_count} (avg top 10: {analysis.avg_word_count})
- Recommended H2 count: {analysis.recommended_h2_count}
- Competitive angle to take: {analysis.competitive_angle}
- Key entities to cover: {', '.join(analysis.key_entities[:15])}
- Key sub-topics: {', '.join(analysis.key_topics[:10])}
- Questions the article MUST answer (from PAA + top 10):
{chr(10).join(f'  · {q}' for q in analysis.common_questions[:10])}
- Content gaps (angles NOT covered by top 10 — our opportunity):
{chr(10).join(f'  · {g}' for g in analysis.content_gaps[:5])}"""


def _stub_block(stub: ArticleStub, cocon: CoconStructure) -> str:
    role = "MOTHER (pillar article)" if stub.article_type == ArticleType.MOTHER else "DAUGHTER (cluster article)"
    return f"""ARTICLE TO PRODUCE:
- Role in cocon: {role}
- Belongs to cocon: {cocon.id} — "{cocon.theme}"
- Cocon main keyword (mother): {cocon.main_keyword}
- H1 title: {stub.h1_title}
- Slug: {stub.slug}
- Target keyword: {stub.target_keyword}
- Secondary keywords: {', '.join(stub.secondary_keywords) or '(none)'}
- Meta title: {stub.meta_title}
- Meta description: {stub.meta_description}
- Intent: {stub.intent.value}"""


def _build_brief_prompt(
    stub: ArticleStub,
    cocon: CoconStructure,
    analysis: SerpAnalysis,
    policy: InterCoconPolicy,
) -> str:
    return f"""{_stub_block(stub, cocon)}

{_serp_analysis_block(analysis)}

{_maillage_rules(policy)}

TASK — Produce an editorial brief in JSON:
{{
  "sections": [
    {{
      "h2": "Section title",
      "h3s": ["Subsection 1", "Subsection 2"],
      "key_points": ["Point 1 to cover", "Point 2", "Point 3"],
      "word_count_target": 350
    }}
  ],
  "faq_questions": ["Question 1?", "Question 2?"],   // 4-6 questions the writer must answer at the end
  "internal_links_plan": [
    {{
      "anchor_text": "text of the link as it appears in the article",
      "target_slug": "slug-from-cocon-reference",
      "target_h1": "H1 title of the target article",
      "link_type": "daughter_to_mother | mother_to_daughter | sister_to_sister | cross_cocon",
      "context": "1-sentence brief description of where in the article this link appears",
      "justification": "why this link helps the reader"
    }}
  ],
  "external_links_suggestions": [
    {{
      "anchor_text": "text of the link",
      "url_suggestion": "https://... (or null if the writer should find one)",
      "domain_type": "official source | scientific study | authoritative media | industry data",
      "reason": "why cite this source"
    }}
  ],
  "editorial_notes": "1-2 paragraphs of instructions to the writer: tone, structure emphasis, key points to nail, things to avoid",
  "tone_guidance": "Concise description of the tone/voice for this article",
  "unique_angle": "The specific unique angle this article takes (matching competitive_angle from SERP analysis)"
}}

Constraints:
- Number of H2 sections must match SERP recommendation (~{analysis.recommended_h2_count})
- Sum of word_count_target across sections should approximate {analysis.recommended_word_count}
- Internal links: enforce Bourrelly maillage rules
- FAQ questions must come from PAA + common_questions
- If experience elements are available, `editorial_notes` must tell the writer which
  one to place, in which section, and that it goes in VERBATIM as an attributed block
  quote — never reworded. Name the element by its title.
- `tone_guidance` must be actionable: if voice samples were supplied, describe their
  actual rhythm and habits, not adjectives like "professional yet accessible"."""


def _build_full_prompt(
    stub: ArticleStub,
    cocon: CoconStructure,
    analysis: SerpAnalysis,
    policy: InterCoconPolicy,
) -> str:
    return f"""{_stub_block(stub, cocon)}

{_serp_analysis_block(analysis)}

{_maillage_rules(policy)}

TASK — Produce a publish-ready article as JSON:
{{
  "sections": [
    {{"h2": "...", "h3s": ["..."], "key_points": ["..."], "word_count_target": 350}}
  ],
  "faq": [
    {{"question": "...", "answer": "..."}}   // 4-6 items, answers 2-4 sentences each
  ],
  "internal_links": [
    {{
      "anchor_text": "...",
      "target_slug": "...",  // MUST exist in cocon reference
      "target_h1": "...",
      "link_type": "daughter_to_mother | mother_to_daughter | sister_to_sister | cross_cocon",
      "context": "excerpt of the sentence where the link appears",
      "justification": "..."
    }}
  ],
  "external_links": [
    {{
      "anchor_text": "...",
      "url_suggestion": "https://... or null",
      "domain_type": "...",
      "reason": "..."
    }}
  ],
  "schema_jsonld": {{
    "@context": "https://schema.org",
    "@graph": [
      {{
        "@type": "Article",
        "headline": "H1 title",
        "description": "meta description",
        "datePublished": "{date.today().isoformat()}",
        "author": {{"@type": "Person", "name": "Author Name (client will replace)"}},
        "publisher": {{"@type": "Organization", "name": "Client brand"}}
      }},
      {{
        "@type": "FAQPage",
        "mainEntity": [
          {{"@type": "Question", "name": "Q", "acceptedAnswer": {{"@type": "Answer", "text": "A"}}}}
        ]
      }}
    ]
  }},
  "content_markdown": "# H1\\n\\nIntro (100 words, answer directly)...\\n\\n## H2 Section 1\\n\\nBody...\\n\\n### H3\\n\\n...\\n\\n[[INTERNAL_LINK:slug|anchor]] in sentences where relevant.\\n\\n## FAQ\\n\\n### Question ?\\n\\nAnswer.\\n",
  "eeat_score": {{
    "experience": 0-100,          // ONLY from [[EXPERIENCE:...]] markers actually placed
    "expertise": 0-100,           // Higher if technical accuracy demonstrated
    "authoritativeness": 0-100,   // Higher if external sources cited
    "trustworthiness": 0-100,     // Higher if disclaimers/transparency present
    "overall": 0-100,
    "warnings": ["List of things the client should improve before publishing"]
  }},
  "word_count": <actual word count of content_markdown>
}}

CRITICAL:
- content_markdown must be COMPLETE and PUBLISH-READY (not a draft or outline)
- Internal links in the markdown must use the format [[INTERNAL_LINK:slug|anchor text]] — they will be resolved to real HTML links by the export step
- Every link in `internal_links` list must correspond to a [[INTERNAL_LINK:...]] in the markdown
- Experience elements: place [[EXPERIENCE:<id>]] alone on its own line where the
  client's material belongs, with your lead-in above and your takeaway below. Never
  write out that material yourself — the exact text is substituted afterwards.
- FAQ section must appear at the end of content_markdown as H2 "FAQ" with each Q as H3
- Answer the target keyword's question in the first 100 words (AI Overviews optimization)
- Target word count: ~{analysis.recommended_word_count}
- Language: same as the H1 title
- eeat_score.experience must reflect ONLY genuine first-hand material actually placed
  via [[EXPERIENCE:...]] markers. Absent any such marker, it cannot exceed 40 — a
  well-researched article written from public sources demonstrates expertise, not
  experience. Say so in `warnings` rather than inflating the number."""


# ============================================================
# ORCHESTRATION
# ============================================================


class ArticleGenerator:
    def __init__(self, anthropic: AnthropicClient) -> None:
        self.anthropic = anthropic

    # ---------- BRIEF MODE ----------

    async def generate_all_briefs(
        self,
        form: ClientForm,
        cocoons: list[CoconStructure],
        serp_analyses: dict[str, SerpAnalysis],
        *,
        store: CheckpointStore | None = None,
    ) -> list[ArticleBrief]:
        cached_context = _build_cached_context(form, cocoons)

        briefs: list[ArticleBrief] = []
        for cocon in cocoons:
            all_stubs = [cocon.mother, *cocon.daughters]
            for stub in all_stubs:
                analysis = serp_analyses.get(stub.slug)
                if analysis is None:
                    logger.warning("Pas d'analyse SERP pour %s, skip", stub.slug)
                    continue

                resumed = await _resume_one(store, f"brief:{stub.slug}", ArticleBrief)
                if resumed is not None:
                    briefs.append(resumed)
                    continue

                brief = await self._generate_brief_one(
                    stub, cocon, analysis, cocoons, cached_context, form.inter_cocon_policy
                )
                briefs.append(brief)
                await _checkpoint_one(store, f"brief:{stub.slug}", brief)
        return briefs

    async def _generate_brief_one(
        self,
        stub: ArticleStub,
        cocon: CoconStructure,
        analysis: SerpAnalysis,
        all_cocoons: list[CoconStructure],
        cached_context: str,
        policy: InterCoconPolicy,
    ) -> ArticleBrief:
        parsed, _ = await self.anthropic.complete_json(
            model="sonnet",
            system=_BRIEF_SYSTEM,
            user_prompt=_build_brief_prompt(stub, cocon, analysis, policy),
            cached_context=cached_context,
            # 4096 débordait dès le premier run sur SERP réelles : un brief complet
            # (sections détaillées + FAQ + 5 liens de maillage + notes éditoriales)
            # dépasse ce plafond quand l'analyse SERP remonte 15 entités et 10
            # questions, là où les mocks en produisaient une poignée.
            max_tokens=8192,
        )

        sections = [
            ArticleSection(
                h2=s["h2"],
                h3s=s.get("h3s", []),
                key_points=s.get("key_points", []),
                word_count_target=int(s.get("word_count_target", 300)),
            )
            for s in parsed.get("sections", [])
        ]

        internal_links = _parse_internal_links(
            parsed.get("internal_links_plan", []), all_cocoons
        )
        external_links = _parse_external_links(parsed.get("external_links_suggestions", []))

        return ArticleBrief(
            stub=stub,
            serp_analysis=analysis,
            sections=sections,
            faq_questions=parsed.get("faq_questions", []),
            internal_links_plan=internal_links,
            external_links_suggestions=external_links,
            editorial_notes=parsed.get("editorial_notes", ""),
            tone_guidance=parsed.get("tone_guidance", ""),
            unique_angle=parsed.get("unique_angle", analysis.competitive_angle),
        )

    # ---------- FULL MODE ----------

    async def generate_all_articles(
        self,
        form: ClientForm,
        cocoons: list[CoconStructure],
        serp_analyses: dict[str, SerpAnalysis],
        *,
        store: CheckpointStore | None = None,
    ) -> list[GeneratedArticle]:
        cached_context = _build_cached_context(form, cocoons)

        articles: list[GeneratedArticle] = []
        # Un élément d'expérience ne doit être placé que dans UN article de la run.
        used_experience: set[str] = set()

        for cocon in cocoons:
            all_stubs = [cocon.mother, *cocon.daughters]
            for stub in all_stubs:
                analysis = serp_analyses.get(stub.slug)
                if analysis is None:
                    logger.warning("Pas d'analyse SERP pour %s, skip", stub.slug)
                    continue

                resumed = await _resume_one(
                    store, f"article:{stub.slug}", GeneratedArticle
                )
                if resumed is not None:
                    # Sinon une reprise sur checkpoint réinjecterait des éléments
                    # déjà placés dans les articles repris.
                    used_experience.update(resumed.experience_used)
                    articles.append(resumed)
                    continue

                article = await self._generate_full_one(
                    stub, cocon, analysis, cocoons, cached_context, form, used_experience
                )
                used_experience.update(article.experience_used)
                articles.append(article)
                await _checkpoint_one(store, f"article:{stub.slug}", article)

        if form.experience_elements:
            unplaced = [e.id for e in form.experience_elements if e.id not in used_experience]
            if unplaced:
                logger.warning(
                    "%d élément(s) d'expérience jamais placé(s) : %s", len(unplaced), unplaced
                )
        return articles

    async def _generate_full_one(
        self,
        stub: ArticleStub,
        cocon: CoconStructure,
        analysis: SerpAnalysis,
        all_cocoons: list[CoconStructure],
        cached_context: str,
        form: ClientForm,
        used_experience: set[str] | None = None,
    ) -> GeneratedArticle:
        # Opus pour mère (qualité max), Sonnet pour filles
        model: ModelTier = "opus" if stub.article_type == ArticleType.MOTHER else "sonnet"

        parsed, meta = await self.anthropic.complete_json(
            model=model,
            system=_FULL_SYSTEM,
            user_prompt=_build_full_prompt(stub, cocon, analysis, form.inter_cocon_policy),
            cached_context=cached_context,
            max_tokens=16000,
        )
        if meta.stop_reason == "max_tokens":
            logger.warning("max_tokens hit for %s (%s tokens out)", stub.slug, meta.output_tokens)

        sections = [
            ArticleSection(
                h2=s["h2"],
                h3s=s.get("h3s", []),
                key_points=s.get("key_points", []),
                word_count_target=int(s.get("word_count_target", 300)),
            )
            for s in parsed.get("sections", [])
        ]
        faq = [FAQItem(question=f["question"], answer=f["answer"]) for f in parsed.get("faq", [])]
        internal_links = _parse_internal_links(parsed.get("internal_links", []), all_cocoons)
        external_links = _parse_external_links(parsed.get("external_links", []))
        eeat = _parse_eeat(parsed.get("eeat_score"))

        markdown, placed, _ = inject_experience_blocks(
            parsed.get("content_markdown", ""),
            form.experience_elements,
            already_used=used_experience,
        )
        if form.experience_elements:
            logger.info(
                "%s : %d/%d élément(s) d'expérience intégré(s) verbatim",
                stub.slug,
                len(placed),
                len(form.experience_elements),
            )
        eeat = _cap_experience_score(eeat, placed)

        return GeneratedArticle(
            stub=stub,
            serp_analysis=analysis,
            sections=sections,
            faq=faq,
            internal_links=internal_links,
            external_links=external_links,
            eeat_score=eeat,
            schema_jsonld=parsed.get("schema_jsonld", {}),
            content_markdown=markdown,
            experience_used=placed,
            # Recompté après injection : les blocs verbatim changent le total.
            word_count=len(markdown.split()),
        )


# ============================================================
# PARSERS
# ============================================================


def _parse_internal_links(raw: list[dict], cocoons: list[CoconStructure]) -> list[InternalLink]:
    """Valide que les target_slug existent réellement dans les stubs."""
    valid_slugs: dict[str, str] = {}  # slug -> H1
    for cocon in cocoons:
        valid_slugs[cocon.mother.slug] = cocon.mother.h1_title
        for d in cocon.daughters:
            valid_slugs[d.slug] = d.h1_title

    result: list[InternalLink] = []
    for item in raw:
        try:
            target_slug = item["target_slug"].strip().lstrip("/")
            if target_slug not in valid_slugs:
                logger.warning("Lien interne ignoré (slug inconnu): %s", target_slug)
                continue
            link_type_str = item.get("link_type", "sister_to_sister")
            try:
                link_type = InternalLinkType(link_type_str)
            except ValueError:
                link_type = InternalLinkType.SISTER_TO_SISTER
            result.append(
                InternalLink(
                    anchor_text=item["anchor_text"],
                    target_slug=target_slug,
                    target_h1=item.get("target_h1") or valid_slugs[target_slug],
                    link_type=link_type,
                    context=item.get("context", ""),
                    justification=item.get("justification", ""),
                )
            )
        except (KeyError, ValueError) as e:
            logger.debug("Lien interne mal formé, skip: %s (%s)", item, e)
    return result


def _parse_external_links(raw: list[dict]) -> list[ExternalLink]:
    result: list[ExternalLink] = []
    for item in raw:
        try:
            result.append(
                ExternalLink(
                    anchor_text=item["anchor_text"],
                    url_suggestion=item.get("url_suggestion") or None,
                    domain_type=item.get("domain_type", "authoritative source"),
                    reason=item.get("reason", ""),
                )
            )
        except KeyError:
            continue
    return result


_NO_EXPERIENCE_CAP = 40


def _cap_experience_score(eeat: EEATScore | None, used_experience: list[str]) -> EEATScore | None:
    """Plafonne le score « expérience » quand aucun matériau client n'a été placé.

    Le prompt le demande déjà, mais le modèle se note complaisamment : sur les
    runs mesurés il attribuait 70-80 en « expérience » à des articles écrits
    intégralement à partir de sources publiques. Or ce score est précisément
    l'argument anti-deindex vendu aux agences — le laisser mentir vide la
    fonctionnalité de son sens. Donc plafond appliqué en code.
    """
    if eeat is None or used_experience or eeat.experience <= _NO_EXPERIENCE_CAP:
        return eeat

    logger.info("Score expérience %d → %d (aucun bloc verbatim)", eeat.experience, _NO_EXPERIENCE_CAP)
    others = [eeat.expertise, eeat.authoritativeness, eeat.trustworthiness]
    return eeat.model_copy(
        update={
            "experience": _NO_EXPERIENCE_CAP,
            "overall": round((_NO_EXPERIENCE_CAP + sum(others)) / 4),
            "warnings": [
                "Aucun élément d'expérience first-hand intégré : l'article s'appuie "
                "uniquement sur des sources publiques. Faire relire et enrichir par "
                "le client avant publication.",
                *eeat.warnings,
            ],
        }
    )


def _parse_eeat(raw: dict | None) -> EEATScore | None:
    if not raw:
        return None
    try:
        return EEATScore(
            experience=int(raw.get("experience", 50)),
            expertise=int(raw.get("expertise", 50)),
            authoritativeness=int(raw.get("authoritativeness", 50)),
            trustworthiness=int(raw.get("trustworthiness", 50)),
            overall=int(raw.get("overall", 50)),
            warnings=raw.get("warnings", []),
        )
    except (ValueError, TypeError):
        return None
