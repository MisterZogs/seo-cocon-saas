"""Étape 7 du pipeline : Rapport backlinks stratégique par cocon.

Pour chaque cocon :
1. Récupère top 5-10 URLs concurrents (sur le KW de la mère)
2. Fetch backlinks summary + referring domains pour chaque concurrent (DataForSEO)
3. Claude analyse patterns + produit opportunités, ratio d'ancres, templates outreach
→ 1 BacklinkReport par cocon
"""

from __future__ import annotations

import asyncio
import logging

from clients.anthropic_client import AnthropicClient
from clients.dataforseo_client import DataForSEOClient
from models import (
    AnchorRatio,
    BacklinkOpportunity,
    BacklinkReport,
    ClientForm,
    CompetitorBacklinkSummary,
    CoconStructure,
)

logger = logging.getLogger(__name__)

TOP_COMPETITORS_PER_COCON = 5


_ANALYSIS_SYSTEM = """You are an SEO backlink strategist. Given competitor backlink data, you produce a white-hat \
link-building strategy: what types of sites to target, what anchor ratio to maintain, and outreach templates.

You NEVER recommend buying links, PBNs, or link exchanges. You focus on:
- Guest posts on niche-relevant authoritative sites
- Niche edits (adding links in existing relevant content)
- Resource pages that list tools/services in the client's category
- Digital PR opportunities (studies, data, expert interviews)"""


def _build_analysis_prompt(
    form: ClientForm,
    cocon: CoconStructure,
    competitors_data: list[dict],
) -> str:
    comp_lines = []
    for c in competitors_data:
        summary = c["summary"]
        domains = c["referring_domains"][:15]
        comp_lines.append(
            f"### {summary.get('target')}\n"
            f"- Total backlinks: {summary.get('total_backlinks')}\n"
            f"- Referring domains: {summary.get('referring_domains')}\n"
            f"- Domain rating: {summary.get('domain_rating')}\n"
            f"- Top referring domains (sample):\n"
            + "\n".join(
                f"  · {d.get('domain')} (DR {d.get('domain_rating')})" for d in domains
            )
        )
    comp_block = "\n\n".join(comp_lines) if comp_lines else "(no competitor data)"

    return f"""CLIENT CONTEXT
- Product: {form.product}
- Niche: {form.niche}
- Language: {form.language}

COCON TARGETED FOR LINK BUILDING
- Theme: {cocon.theme}
- Main KW: {cocon.main_keyword}
- Mother article H1: {cocon.mother.h1_title}
- Mother slug: /{cocon.mother.slug}

COMPETITOR BACKLINK DATA (top-ranking pages on the main KW):
{comp_block}

TASK:
Analyze the competitor backlink patterns and produce a white-hat link-building strategy for this cocon.

Return JSON:
{{
  "opportunities": [
    {{
      "referring_domain": "example.com",
      "domain_rating": 65,
      "reason": "This site already links to 3 competitors and covers our niche",
      "suggested_anchor": "anchor text to propose",
      "outreach_template_type": "guest_post | niche_edit | resource_page"
    }}
  ],   // 8-15 real opportunities, ideally sites that link to multiple competitors
  "recommended_anchor_ratio": {{
    "exact": 0.10,       // 10% exact match anchors — keep low to avoid over-optimization
    "partial": 0.30,     // 30% partial match
    "branded": 0.30,     // 30% brand name
    "naked_url": 0.15,   // 15% raw URLs
    "generic": 0.15      // 15% generic ("click here", "learn more")
  }},
  "outreach_templates": {{
    "guest_post": "Full email template for guest post pitch (in {form.language}), personalizable with {{{{recipient_name}}}} and {{{{recipient_site}}}} placeholders",
    "niche_edit": "Full email template for niche edit request (in {form.language})",
    "resource_page": "Full email template for resource page inclusion (in {form.language})"
  }}
}}"""


class BacklinkAnalyzer:
    def __init__(
        self,
        anthropic: AnthropicClient,
        dataforseo: DataForSEOClient,
        top_competitors: int = TOP_COMPETITORS_PER_COCON,
    ) -> None:
        self.anthropic = anthropic
        self.dataforseo = dataforseo
        self.top_competitors = top_competitors

    async def analyze_all(
        self, form: ClientForm, cocoons: list[CoconStructure]
    ) -> list[BacklinkReport]:
        results = await asyncio.gather(
            *(self.analyze_cocon(form, c) for c in cocoons),
            return_exceptions=True,
        )
        reports: list[BacklinkReport] = []
        for cocon, r in zip(cocoons, results):
            if isinstance(r, Exception):
                logger.error("Backlink report failed for %s: %s", cocon.id, r)
                continue
            reports.append(r)
        return reports

    async def analyze_cocon(
        self, form: ClientForm, cocon: CoconStructure
    ) -> BacklinkReport:
        # 1. Récupère les top compétiteurs sur le KW de la mère.
        #    depth=10 puis déduplication : plusieurs résultats du top 10 peuvent
        #    appartenir au même domaine, et interroger `depth=top_competitors`
        #    ne laissait que 3 concurrents distincts sur les 5 attendus.
        serp = await self.dataforseo.get_serp(cocon.main_keyword, depth=10)
        competitor_domains = _unique_domains(
            [r["url"] for r in serp.get("organic_results", []) if r.get("url")],
            limit=self.top_competitors,
        )
        logger.info(
            "Cocon %s : %d domaines concurrents retenus — %s",
            cocon.id, len(competitor_domains), ", ".join(competitor_domains),
        )

        # 2. Fetch backlink summary + referring domains pour chaque concurrent (parallèle)
        competitors_data = await asyncio.gather(
            *(self._fetch_competitor(d) for d in competitor_domains),
            return_exceptions=True,
        )
        competitors_data = [c for c in competitors_data if not isinstance(c, Exception)]

        # 3. Prépare summaries structurés
        summaries: list[CompetitorBacklinkSummary] = []
        for c in competitors_data:
            s = c["summary"]
            if not s:
                continue
            summaries.append(
                CompetitorBacklinkSummary(
                    competitor_url=s.get("target", ""),
                    total_backlinks=int(s.get("total_backlinks") or 0),
                    referring_domains=int(s.get("referring_domains") or 0),
                    domain_rating=s.get("domain_rating"),
                    top_referring_types=[],
                )
            )

        # 4. Claude analyse et propose stratégie
        parsed, _ = await self.anthropic.complete_json(
            model="sonnet",
            system=_ANALYSIS_SYSTEM,
            user_prompt=_build_analysis_prompt(form, cocon, competitors_data),
            max_tokens=4096,
        )

        opportunities = _parse_opportunities(parsed.get("opportunities", []))
        ratio = _parse_ratio(parsed.get("recommended_anchor_ratio"))
        templates = parsed.get("outreach_templates", {}) or {}

        report = BacklinkReport(
            cocon_id=cocon.id,
            competitor_analysis=summaries,
            opportunities=opportunities,
            recommended_anchor_ratio=ratio,
            outreach_templates=templates,
        )
        logger.info(
            "Backlink report cocon %s: %d compétiteurs analysés, %d opportunités",
            cocon.id,
            len(summaries),
            len(opportunities),
        )
        return report

    async def _fetch_competitor(self, url: str) -> dict:
        try:
            summary, refs = await asyncio.gather(
                self.dataforseo.get_backlinks_summary(url),
                self.dataforseo.get_referring_domains(url, limit=30),
            )
            return {"summary": summary, "referring_domains": refs}
        except Exception as e:
            logger.warning("Backlink fetch failed for %s: %s", url, e)
            return {"summary": {}, "referring_domains": []}


def _parse_opportunities(raw: list[dict]) -> list[BacklinkOpportunity]:
    result: list[BacklinkOpportunity] = []
    for item in raw:
        try:
            template_type = item.get("outreach_template_type", "guest_post")
            if template_type not in {"guest_post", "niche_edit", "resource_page"}:
                template_type = "guest_post"
            result.append(
                BacklinkOpportunity(
                    referring_domain=item["referring_domain"],
                    domain_rating=item.get("domain_rating"),
                    reason=item.get("reason", ""),
                    suggested_anchor=item.get("suggested_anchor", ""),
                    outreach_template_type=template_type,  # type: ignore[arg-type]
                )
            )
        except KeyError:
            continue
    return result


def _parse_ratio(raw: dict | None) -> AnchorRatio:
    if not raw:
        return AnchorRatio(exact=0.10, partial=0.30, branded=0.30, naked_url=0.15, generic=0.15)
    return AnchorRatio(
        exact=float(raw.get("exact", 0.10)),
        partial=float(raw.get("partial", 0.30)),
        branded=float(raw.get("branded", 0.30)),
        naked_url=float(raw.get("naked_url", 0.15)),
        generic=float(raw.get("generic", 0.15)),
    )
