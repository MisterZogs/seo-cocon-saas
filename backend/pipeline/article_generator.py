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
from datetime import date

from clients.anthropic_client import AnthropicClient, ModelTier
from models import (
    ArticleBrief,
    ArticleSection,
    ArticleStub,
    ArticleType,
    ClientForm,
    CoconStructure,
    EEATScore,
    ExternalLink,
    FAQItem,
    GeneratedArticle,
    InternalLink,
    InternalLinkType,
    SerpAnalysis,
)

logger = logging.getLogger(__name__)


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
    exp_block = ""
    if form.experience_elements:
        lines = ["\nEXPERIENCE ELEMENTS UPLOADED BY CLIENT (integrate these when generating):"]
        for e in form.experience_elements:
            lines.append(f"- [{e.type}] {e.title}: {e.content[:400]}")
        exp_block = "\n".join(lines)
    return (
        f"# CLIENT CONTEXT\n"
        f"Product/Service: {form.product}\n"
        f"Description: {form.description}\n"
        f"Language: {form.language}\n"
        f"Target audience: {form.audience}\n"
        f"Niche: {form.niche}\n"
        f"Current year: {date.today().year}"
        f"{exp_block}"
    )


# ============================================================
# PROMPTS COMMUNS
# ============================================================


_MAILLAGE_RULES = """MAILLAGE RULES (Bourrelly hybrid method, 2026):
- DAUGHTER → MOTHER: MANDATORY, anchor on the mother's target KW (or close variant)
- MOTHER → all its DAUGHTERS: MANDATORY, one link per daughter with contextual anchor
- DAUGHTER ↔ SISTER: only if it genuinely helps the reader continue their journey
- CROSS-COCON: 1 link per article to a related article in another cocon IS RECOMMENDED (signal of site depth). No strict siloing.
- Anchor text: prioritize reader clarity, then keyword optimization. Avoid over-optimized exact-match anchors.
- Every internal link MUST target an existing slug from the cocon reference.
- Every link needs a justification (why this helps the reader)."""


_BRIEF_SYSTEM = """You are an expert SEO content strategist producing detailed editorial briefs for human writers.
Your briefs are so detailed and calibrated that a competent writer can produce a top-ranking article from them.

You write for a French SEO agency SaaS platform serving other agencies. Their end-clients publish these articles.

You know the Bourrelly cocon sémantique method and apply it rigorously to internal linking."""


_FULL_SYSTEM = """You are an expert SEO writer and content strategist. You produce publish-ready articles in Markdown,
following a strict semantic cocoon structure (Bourrelly method, 2026 hybrid version).

Your articles:
- Match the SERP intent exactly
- Answer the reader's question directly in the first 100 words (AI Overviews optimization)
- Are well-structured with H2/H3, lists, tables when appropriate
- Cite external authoritative sources (1-3 per article)
- Include internal maillage links per the Bourrelly rules provided
- Are calibrated to the SERP analysis (word count, entities to cover, questions to answer)
- Integrate the client's uploaded experience elements naturally when present

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
    stub: ArticleStub, cocon: CoconStructure, analysis: SerpAnalysis
) -> str:
    return f"""{_stub_block(stub, cocon)}

{_serp_analysis_block(analysis)}

{_MAILLAGE_RULES}

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
- FAQ questions must come from PAA + common_questions"""


def _build_full_prompt(
    stub: ArticleStub, cocon: CoconStructure, analysis: SerpAnalysis
) -> str:
    return f"""{_stub_block(stub, cocon)}

{_serp_analysis_block(analysis)}

{_MAILLAGE_RULES}

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
    "experience": 0-100,          // Higher if experience_elements integrated
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
- FAQ section must appear at the end of content_markdown as H2 "FAQ" with each Q as H3
- Answer the target keyword's question in the first 100 words (AI Overviews optimization)
- Target word count: ~{analysis.recommended_word_count}
- Language: same as the H1 title"""


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
        cocon_ref = _build_cocon_reference(cocoons)
        brand = _build_brand_context(form)
        cached_context = f"{brand}\n\n{cocon_ref}"

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
                    stub, cocon, analysis, cocoons, cached_context
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
    ) -> ArticleBrief:
        parsed, _ = await self.anthropic.complete_json(
            model="sonnet",
            system=_BRIEF_SYSTEM,
            user_prompt=_build_brief_prompt(stub, cocon, analysis),
            cached_context=cached_context,
            max_tokens=4096,
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
    ) -> list[GeneratedArticle]:
        cocon_ref = _build_cocon_reference(cocoons)
        brand = _build_brand_context(form)
        cached_context = f"{brand}\n\n{cocon_ref}"

        articles: list[GeneratedArticle] = []
        for cocon in cocoons:
            all_stubs = [cocon.mother, *cocon.daughters]
            for stub in all_stubs:
                analysis = serp_analyses.get(stub.slug)
                if analysis is None:
                    logger.warning("Pas d'analyse SERP pour %s, skip", stub.slug)
                    continue
                article = await self._generate_full_one(
                    stub, cocon, analysis, cocoons, cached_context
                )
                articles.append(article)
        return articles

    async def _generate_full_one(
        self,
        stub: ArticleStub,
        cocon: CoconStructure,
        analysis: SerpAnalysis,
        all_cocoons: list[CoconStructure],
        cached_context: str,
    ) -> GeneratedArticle:
        # Opus pour mère (qualité max), Sonnet pour filles
        model: ModelTier = "opus" if stub.article_type == ArticleType.MOTHER else "sonnet"

        parsed, meta = await self.anthropic.complete_json(
            model=model,
            system=_FULL_SYSTEM,
            user_prompt=_build_full_prompt(stub, cocon, analysis),
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

        return GeneratedArticle(
            stub=stub,
            serp_analysis=analysis,
            sections=sections,
            faq=faq,
            internal_links=internal_links,
            external_links=external_links,
            eeat_score=eeat,
            schema_jsonld=parsed.get("schema_jsonld", {}),
            content_markdown=parsed.get("content_markdown", ""),
            word_count=int(parsed.get("word_count", 0))
            or len(parsed.get("content_markdown", "").split()),
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
