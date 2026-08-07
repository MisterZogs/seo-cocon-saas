"""Étape 4 du pipeline : Analyse SERP par article (Surfer-like).

Pour chaque article (mère + filles), pour son mot-clé cible :
1. Fetch le top 10 SERP via DataForSEO
2. Scrape le contenu de chaque page (httpx + BeautifulSoup)
3. Analyse statistique : word count moyen, nb H2/H3
4. Analyse Claude : entités clés, questions PAA, angles non couverts, angle unique
→ Produit un SerpAnalysis (brief calibré) par article
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from statistics import mean
from typing import Any

import httpx
from bs4 import BeautifulSoup

from clients.anthropic_client import AnthropicClient
from clients.dataforseo_client import DataForSEOClient
from models import ArticleStub, ScrapedPage, SerpAnalysis

logger = logging.getLogger(__name__)


SCRAPE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)

# Un User-Agent seul ne suffit pas : les filtres anti-bot regardent aussi l'absence
# d'Accept / Accept-Language, signature classique d'un client scripté. Ça ne passera
# pas Cloudflare en mode challenge, mais ça récupère les rejets les plus simples.
SCRAPE_HEADERS = {
    "User-Agent": SCRAPE_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

SCRAPE_TIMEOUT = 12.0
SCRAPE_MAX_CONCURRENT = 5
SCRAPE_MAX_BYTES = 500_000  # 500KB par page suffit pour extraire la structure

# `depth` est compté par DataForSEO sur TOUS les types d'items de la SERP
# (organic, paa, video, local_pack, related_searches…), pas sur les seuls organic.
# Mesuré sur 6 requêtes crypto FR avec depth=10 : 8 à 9 URLs organiques seulement,
# jamais 10. On demande donc large et on tronque nous-mêmes côté organic.
SERP_DEPTH = 30

# Une page qui répond 200 mais ne rend son contenu qu'en JS (binance.com, youtube,
# reddit, etoro…) renvoie un word_count de 0 à 20. Elle passait auparavant pour une
# page de référence valide : comptée dans scraped_pages_count, moyennée dans les
# H2/H3 et envoyée à Claude comme exemple à imiter. Mesuré sur « copytrade binance » :
# 8 pages « scrapées » dont 5 vides, soit un brief calibré sur 3 pages réelles alors
# qu'il en annonçait 8.
MIN_REFERENCE_WORDS = 250

# En dessous de ce nombre de pages de référence, le brief reste produit mais il est
# marqué `low_sample` : la calibration (longueur cible, nb de H2, entités) repose sur
# un échantillon trop mince pour être opposée telle quelle à un client.
LOW_SAMPLE_THRESHOLD = 4


def _clamp_h2(recommended: int, avg_h2: int) -> int:
    """Borne le nombre de H2 recommandé sur ce que font réellement les concurrents.

    Le nombre de titres est une donnée observable, pas une appréciation — donc
    il se borne en code, comme le maillage et le plafond E-E-A-T. Claude
    renvoyait 9 ou 10 quel que soit le SERP : mesuré sur le run frelons, il
    recommandait 9 H2 aussi bien face à des concurrents qui en font 3 que face
    à ceux qui en font 9. La recommandation ne suivait donc plus rien.

    On garde une marge au-dessus de la moyenne (couvrir plus que le top 10 est
    la promesse de l'outil), mais proportionnée au SERP observé.
    """
    ceiling = avg_h2 + max(2, avg_h2 // 2)
    return max(avg_h2, min(recommended, ceiling))


# ============================================================
# PROMPTS
# ============================================================


_ANALYSIS_SYSTEM = """You are an expert SEO content strategist. You analyze SERP top 10 results to produce \
a Surfer-like content brief: what entities to cover, what questions to answer, what unique angle to take.

Your analysis must be actionable and specific — not generic SEO advice."""


def _build_analysis_prompt(
    keyword: str,
    language: str,
    scraped: list[ScrapedPage],
    paa_questions: list[str],
    serp_features: dict[str, Any],
) -> str:
    # Résumé compact des pages scrapées
    pages_summary = []
    for i, page in enumerate(scraped, 1):
        pages_summary.append(
            f"### PAGE #{i} — {page.url}\n"
            f"TITLE: {page.title}\n"
            f"WORD_COUNT: {page.word_count}\n"
            f"H1: {page.h1 or '(missing)'}\n"
            f"H2s ({len(page.h2s)}): {' | '.join(page.h2s[:15])}\n"
            f"H3s ({len(page.h3s)}): {' | '.join(page.h3s[:15])}"
        )
    pages_block = "\n\n".join(pages_summary) if pages_summary else "(no pages scraped)"

    paa_block = (
        "\n".join(f"- {q}" for q in paa_questions) if paa_questions else "(none)"
    )
    features_block = ", ".join(k for k, v in serp_features.items() if v) or "(none)"

    return f"""Analyze the SERP top 10 for the keyword: "{keyword}"
Target language of the article to produce: {language}

SERP FEATURES PRESENT: {features_block}

PEOPLE ALSO ASK QUESTIONS:
{paa_block}

TOP RANKING PAGES (scraped structure):
{pages_block}

TASK:
Produce a Surfer-like content brief for an article targeting this keyword.
The article must be BETTER than what already ranks by:
- Covering all key entities/topics competitors cover
- Answering all common questions
- Adding unique angles that competitors miss

Return JSON with these fields (respond in the article's target language {language} for text fields like entities/topics/questions/angles):
{{
  "key_entities": ["entity 1", "entity 2", ...],   // 8-15 named entities/concepts to mention
  "key_topics": ["subtopic 1", ...],               // 5-10 sub-themes competitors cover
  "common_questions": ["question 1?", ...],        // 5-10 questions the article MUST answer (from PAA + top 10 patterns)
  "content_gaps": ["gap 1", ...],                  // 3-5 angles NOT covered by top 10 = our opportunity
  "recommended_word_count": 2500,                  // integer, calibrated on top 10 (aim slightly above average)
  "recommended_h2_count": 8,                       // integer, calibrated
  "competitive_angle": "One clear sentence describing the unique angle our article should take to beat the top 10",
  "top_result_format": "guide long | listicle | comparatif | tuto | actualité"
}}"""


# ============================================================
# ORCHESTRATION
# ============================================================


class SerpAnalyzer:
    def __init__(
        self,
        anthropic: AnthropicClient,
        dataforseo: DataForSEOClient,
        max_pages_to_scrape: int = 10,
    ) -> None:
        self.anthropic = anthropic
        self.dataforseo = dataforseo
        self.max_pages_to_scrape = max_pages_to_scrape
        self._semaphore = asyncio.Semaphore(SCRAPE_MAX_CONCURRENT)

    async def analyze_all(
        self, stubs: list[ArticleStub], language: str
    ) -> dict[str, SerpAnalysis]:
        """Analyse SERP pour tous les articles, en parallèle. Retourne dict[slug -> SerpAnalysis]."""
        results = await asyncio.gather(
            *(self.analyze_one(s, language) for s in stubs),
            return_exceptions=True,
        )
        analyses: dict[str, SerpAnalysis] = {}
        for stub, res in zip(stubs, results):
            if isinstance(res, Exception):
                logger.error("SERP analysis failed for %s: %s", stub.slug, res)
                continue
            analyses[stub.slug] = res
        return analyses

    async def analyze_one(self, stub: ArticleStub, language: str) -> SerpAnalysis:
        keyword = stub.target_keyword

        # 1. Récupère SERP
        serp = await self.dataforseo.get_serp(keyword, depth=SERP_DEPTH)
        organic_results = serp.get("organic_results", [])
        paa = serp.get("paa", [])
        features = serp.get("features", {})

        # 2. Scrape les pages (avec sémaphore + gestion erreurs douces)
        urls = [r["url"] for r in organic_results if r.get("url")][
            : self.max_pages_to_scrape
        ]
        scraped_pages, rejected = await self._scrape_pages(urls)

        # 3. Analyse statistique — uniquement sur les pages de référence retenues.
        #    `mean()` lève StatisticsError sur une séquence vide, elle ne renvoie pas
        #    de valeur fausse : le `or 1500` d'origine ne pouvait donc jamais servir de
        #    repli, il plantait. L'exception remontait dans analyze_all qui abandonnait
        #    silencieusement le brief de l'article.
        if scraped_pages:
            avg_wc = int(mean(p.word_count for p in scraped_pages))
            avg_h2 = int(mean(len(p.h2s) for p in scraped_pages)) or 6
            avg_h3 = int(mean(len(p.h3s) for p in scraped_pages)) or 4
        else:
            avg_wc, avg_h2, avg_h3 = 1500, 6, 4

        # 4. Analyse Claude (si on a au moins 1 page)
        if scraped_pages:
            parsed, _ = await self.anthropic.complete_json(
                model="sonnet",
                system=_ANALYSIS_SYSTEM,
                user_prompt=_build_analysis_prompt(
                    keyword=keyword,
                    language=language,
                    scraped=scraped_pages,
                    paa_questions=paa,
                    serp_features=features,
                ),
                max_tokens=2048,
            )
        else:
            # Fallback si aucune page scrapée : Claude analyse sans pages
            parsed = {
                "key_entities": [],
                "key_topics": [],
                "common_questions": paa,
                "content_gaps": [],
                "recommended_word_count": 2000,
                "recommended_h2_count": 6,
                "competitive_angle": f"Guide complet et à jour sur '{keyword}'",
                "top_result_format": "guide long",
            }

        analysis = SerpAnalysis(
            keyword=keyword,
            scraped_pages_count=len(scraped_pages),
            serp_urls_count=len(urls),
            rejected_pages=rejected,
            low_sample=len(scraped_pages) < LOW_SAMPLE_THRESHOLD,
            avg_word_count=avg_wc,
            recommended_word_count=int(parsed.get("recommended_word_count", avg_wc + 300)),
            avg_h2_count=avg_h2,
            recommended_h2_count=_clamp_h2(int(parsed.get("recommended_h2_count", avg_h2)), avg_h2),
            avg_h3_count=avg_h3,
            key_entities=parsed.get("key_entities", []),
            key_topics=parsed.get("key_topics", []),
            common_questions=parsed.get("common_questions", []) or paa,
            content_gaps=parsed.get("content_gaps", []),
            competitive_angle=parsed.get(
                "competitive_angle", f"Guide de référence sur '{keyword}'"
            ),
            top_result_format=parsed.get("top_result_format", "guide long"),
        )
        log = logger.warning if analysis.low_sample else logger.info
        log(
            "SERP analysé: %s (référence=%d/%d URLs, rejets=%s, wc_avg=%d, wc_reco=%d)%s",
            keyword,
            len(scraped_pages),
            len(urls),
            dict(rejected) or "aucun",
            avg_wc,
            analysis.recommended_word_count,
            " — ÉCHANTILLON FAIBLE" if analysis.low_sample else "",
        )
        return analysis

    # ------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------

    async def _scrape_pages(
        self, urls: list[str]
    ) -> tuple[list[ScrapedPage], dict[str, int]]:
        """Retourne (pages de référence retenues, motif de rejet -> nb d'URLs)."""
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=SCRAPE_TIMEOUT,
            headers=SCRAPE_HEADERS,
        ) as client:
            results = await asyncio.gather(
                *(self._scrape_one(client, url) for url in urls),
                return_exceptions=True,
            )

        pages: list[ScrapedPage] = []
        rejected: Counter[str] = Counter()
        for url, r in zip(urls, results):
            if isinstance(r, Exception):
                rejected[f"exception {type(r).__name__}"] += 1
                logger.info("Scrape rejeté %s: %s", url, r)
            elif isinstance(r, ScrapedPage):
                pages.append(r)
            else:  # (None, motif)
                _, reason = r
                rejected[reason] += 1
                logger.info("Scrape rejeté %s: %s", url, reason)
        return pages, dict(rejected)

    async def _scrape_one(
        self, client: httpx.AsyncClient, url: str
    ) -> ScrapedPage | tuple[None, str]:
        async with self._semaphore:
            try:
                response = await client.get(url)
                if response.status_code >= 400:
                    return None, f"HTTP {response.status_code}"
                # Limite bytes pour éviter les pages énormes
                content = response.text[:SCRAPE_MAX_BYTES]
            except httpx.TimeoutException:
                return None, "timeout"
            except httpx.HTTPError as e:
                return None, f"réseau {type(e).__name__}"

        try:
            soup = BeautifulSoup(content, "lxml")
        except Exception as e:
            return None, f"parse {type(e).__name__}"

        # Retire scripts/styles/nav/footer pour word count propre
        for tag in soup(["script", "style", "nav", "footer", "aside", "noscript"]):
            tag.decompose()

        title = (soup.title.string.strip() if soup.title and soup.title.string else "")
        h1_tag = soup.find("h1")
        h1 = h1_tag.get_text(strip=True) if h1_tag else None
        h2s = [h.get_text(strip=True) for h in soup.find_all("h2") if h.get_text(strip=True)]
        h3s = [h.get_text(strip=True) for h in soup.find_all("h3") if h.get_text(strip=True)]

        # Word count sur le body (approximation raisonnable)
        text = soup.get_text(separator=" ", strip=True)
        word_count = len(text.split())

        # Page rendue côté client : 200 OK mais aucun contenu dans le HTML servi.
        # Inutilisable comme référence — et surtout trompeuse si on la compte.
        if word_count < MIN_REFERENCE_WORDS:
            return None, "contenu vide ou JS-only"

        meta_desc_tag = soup.find("meta", attrs={"name": "description"})
        meta_desc = (
            meta_desc_tag.get("content", "").strip() if meta_desc_tag else None
        )

        return ScrapedPage(
            url=url,
            title=title[:300],
            word_count=word_count,
            h1=h1,
            h2s=h2s[:30],
            h3s=h3s[:30],
            meta_description=meta_desc[:500] if meta_desc else None,
        )
