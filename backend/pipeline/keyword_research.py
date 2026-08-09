"""Étape 2 du pipeline : Keyword Research complet.

Flux :
1. Claude (Haiku) étend les seeds en 30 KW candidats + intent + cluster
2. DataForSEO enrichit avec volume, CPC, concurrence
3. DataForSEO retourne SERP features pour les KW les plus intéressants
4. Claude (Sonnet) sélectionne et regroupe en propositions de cocons
"""

from __future__ import annotations

import asyncio
import logging

from clients.anthropic_client import AnthropicClient
from clients.dataforseo_client import DataForSEOClient
from models import (
    ClientForm,
    KeywordCandidate,
    KeywordWithData,
    SearchIntent,
    SerpFeatures,
)

logger = logging.getLogger(__name__)


# ============================================================
# PROMPTS
# ============================================================


_EXPANSION_SYSTEM = """You are an expert SEO strategist specialized in the semantic cocoon method (Laurent Bourrelly). \
You work for a French SEO agency SaaS platform. Your job is to generate high-quality keyword ideas \
based on a client's business context. Focus on informational and commercial-intent keywords that are \
suitable for topic cluster content, not just brand or navigational queries.

Rules:
- Generate keywords in the target language specified by the user.
- Prioritize keywords with clear informational or commercial intent.
- Avoid pure brand-name keywords or transactional-only queries.
- Group keywords into thematic clusters (2-5 clusters).
- Each cluster should contain 4-8 related keywords covering the same topic in depth."""


def _build_expansion_prompt(form: ClientForm) -> str:
    return f"""Analyze this business and generate a comprehensive keyword research.

BUSINESS CONTEXT:
- Product/Service: {form.product}
- Description: {form.description}
- Target Language: {form.language}
- Target Audience: {form.audience}
- Niche/Sector: {form.niche}
- Number of cocoons desired: {form.num_cocoons}
- Seed keywords provided by the client (starting point): {', '.join(form.seed_keywords)}

TASK:
Generate 30 relevant keyword ideas that:
1. Expand from the seeds provided
2. Include related long-tail keywords
3. Cover different angles of the topic (breadth)
4. Focus on informational or commercial intent
5. Are realistic to rank on (avoid ultra-competitive head terms)

For each keyword, provide:
- keyword: the exact search query (in {form.language})
- intent: "informational" | "commercial" | "transactional" | "navigational"
- cluster: name of the thematic cluster this keyword belongs to
- relative_volume: "high" (>5000/mo estimated) | "medium" (500-5000) | "low" (<500)
- difficulty_estimate: "easy" | "medium" | "hard"

ALSO propose {form.num_cocoons} cluster names that could form semantic cocoons — \
each cluster should be a coherent topic where you have 5-6 related keywords.

Return JSON:
{{
  "keywords": [
    {{"keyword": "...", "intent": "informational", "cluster": "...", "relative_volume": "medium", "difficulty_estimate": "medium"}}
  ],
  "proposed_clusters": [
    {{"name": "...", "description": "...", "example_keywords": ["...", "..."]}}
  ]
}}"""


_SELECTION_SYSTEM = """You are an expert SEO strategist. Given a list of keywords enriched with real search volume, CPC, \
competition data, and SERP features, your job is to select the BEST keywords and organize them into \
semantic cocoons following the Bourrelly method.

Each cocoon needs:
- 1 pillar/mother keyword (broader, higher volume, main topic)
- 5 cluster/daughter keywords (more specific, related, long-tail variants of the pillar)

Rules for cocoon composition:
- The mother keyword should have HIGHER volume and BROADER intent than daughters
- All daughters must be semantically related to their mother (same core topic)
- Daughters together should cover the topic exhaustively (breadth in depth)
- Avoid mixing intents wildly (a mother "guide" + all "product review" daughters is bad)
- Prefer medium-difficulty keywords with decent volume over impossible head terms

VOLUME IS A HARD CONSTRAINT, NOT A TIEBREAKER. The agency is paid for traffic:
- The MOTHER must have a measured volume. Never pick a mother marked
  "0 — AUCUNE RECHERCHE MESURÉE" or "inconnu".
- At most 2 of the 5 daughters may lack measured volume, and only when they cover a
  genuinely necessary sub-topic. Everything else must have real volume.
- A semantically elegant cocoon built on dead keywords is worthless. If the best
  available cluster has no volume, pick a different cluster — even a less tidy one.
- "0 — AUCUNE RECHERCHE MESURÉE" means Google measured it and nobody searches it.
  That is a reason to reject the keyword, not a missing data point.
- Prefer building the cocoon around the highest-volume viable cluster in the list.

EVERY keyword you pick must carry a `reason` — one sentence, in the client's language,
addressed to an SEO professional who will review your selection and may overrule it.
State what actually drove the decision (volume, intent, position in the topic, gap in
the SERP), not a restatement of the keyword. For the mother, say why THIS keyword is
the pillar rather than any of its daughters. A reason like "pertinent pour le sujet" is
useless and will be rejected.

Return your selection as JSON."""


def _build_selection_prompt(
    form: ClientForm, enriched_keywords: list[KeywordWithData]
) -> str:
    kw_lines = []
    for k in enriched_keywords:
        serp_hint = ""
        if k.serp_features:
            hints = []
            if k.serp_features.featured_snippet:
                hints.append("featured_snippet")
            if k.serp_features.people_also_ask:
                hints.append(f"PAA={len(k.serp_features.people_also_ask)}")
            if k.serp_features.video_carousel:
                hints.append("video")
            if hints:
                serp_hint = f" | serp: {', '.join(hints)}"
        # `or 'N/A'` masquait les volumes à 0 : le modèle lisait « donnée
        # indisponible » là où Google dit « personne ne cherche ça ». Sur un run
        # réel il a composé un cocon dont 5 des 6 mots-clés faisaient 0/mois.
        if k.monthly_volume is None:
            volume = "inconnu"
        elif k.monthly_volume == 0:
            volume = "0 — AUCUNE RECHERCHE MESURÉE"
        else:
            volume = str(k.monthly_volume)
        kw_lines.append(
            f"- {k.keyword} | intent={k.intent.value} | cluster={k.cluster} | "
            f"volume={volume} | cpc={k.cpc if k.cpc is not None else 'inconnu'} | "
            f"competition={k.competition_score if k.competition_score is not None else 'inconnu'}"
            f"{serp_hint}"
        )
    kw_list = "\n".join(kw_lines)

    return f"""Client context:
- Product: {form.product}
- Niche: {form.niche}
- Audience: {form.audience}
- Language: {form.language}
- Number of cocoons requested: {form.num_cocoons}

Keywords available (with real DataForSEO data):
{kw_list}

TASK:
Select the best keywords and organize them into {form.num_cocoons} semantic cocoons.
Each cocoon = 1 mother + 5 daughters (6 articles total per cocoon).

For EACH cocoon, provide:
- theme: 1-sentence description of what this cocoon covers
- main_keyword: THE keyword the mother article will target
- rationale: why this cocoon makes SEO sense (topic authority angle)
- mother:
    - target_keyword
    - h1_title (compelling, 50-70 chars)
    - meta_title (max 60 chars)
    - meta_description (140-160 chars)
    - slug (URL-friendly, in {form.language})
    - intent
    - secondary_keywords (2-3 supporting KW to also target in the article)
    - reason (1 sentence in {form.language}: why this keyword is the pillar)
- daughters: array of 5 objects with same fields as mother, `reason` included
  (why this keyword earns one of the 5 slots)

Return JSON:
{{
  "cocoons": [
    {{
      "theme": "...",
      "main_keyword": "...",
      "rationale": "...",
      "mother": {{
        "target_keyword": "...",
        "h1_title": "...",
        "meta_title": "...",
        "meta_description": "...",
        "slug": "...",
        "intent": "informational",
        "secondary_keywords": ["...", "..."]
      }},
      "daughters": [
        {{"target_keyword": "...", "h1_title": "...", "meta_title": "...", "meta_description": "...", "slug": "...", "intent": "informational", "secondary_keywords": ["..."]}}
      ]
    }}
  ]
}}"""


# ============================================================
# CONVERSION DES SUGGESTIONS GOOGLE ADS
# ============================================================

# Marqueurs d'intention en français. Google Ads ne renvoie pas d'intent : le
# déduire de la formulation évite un appel LLM pour une information qui ne sert
# qu'à éclairer la sélection (l'intent définitif vient de l'étape cocon).
_INTENT_MARKERS: list[tuple[SearchIntent, tuple[str, ...]]] = [
    (SearchIntent.TRANSACTIONAL,
     ("acheter", "achat", "prix", "tarif", "commander", "devis", "pas cher",
      "promo", "abonnement", "souscrire", "ouvrir un compte")),
    (SearchIntent.COMMERCIAL,
     ("meilleur", "meilleure", "comparatif", "comparaison", "avis", "test",
      "alternative", "vs", "classement", "top ", "gratuit")),
    (SearchIntent.NAVIGATIONAL,
     ("connexion", "login", "se connecter", "site officiel", "application")),
]


def _guess_intent(keyword: str) -> SearchIntent:
    kw = keyword.lower()
    for intent, markers in _INTENT_MARKERS:
        if any(m in kw for m in markers):
            return intent
    return SearchIntent.INFORMATIONAL


def ideas_to_models(ideas: list[dict]) -> list[KeywordWithData]:
    """Suggestions Google Ads → KeywordWithData.

    `cluster` reste vide : le regroupement sémantique est fait par le LLM à
    l'étape de sélection, c'est précisément la répartition des rôles voulue —
    Google fournit les mots-clés réels, le modèle les organise.
    """
    out: list[KeywordWithData] = []
    for i in ideas:
        kw = i.get("keyword")
        if not kw:
            continue
        out.append(
            KeywordWithData(
                keyword=kw,
                intent=_guess_intent(kw),
                cluster="",
                monthly_volume=i.get("monthly_volume"),
                cpc=i.get("cpc"),
                competition_score=i.get("competition_score"),
                difficulty=i.get("difficulty"),
            )
        )
    return out


# ============================================================
# ORCHESTRATION
# ============================================================


class KeywordResearcher:
    def __init__(
        self,
        anthropic: AnthropicClient,
        dataforseo: DataForSEOClient,
        top_serp_analysis: int = 15,
        max_keyword_ideas: int = 200,
        min_viable_keywords: int = 20,
    ) -> None:
        self.anthropic = anthropic
        self.dataforseo = dataforseo
        self.top_serp_analysis = top_serp_analysis
        # 200 : assez pour donner du choix au LLM, assez peu pour tenir dans le
        # prompt. L'API peut en renvoyer 20 000.
        self.max_keyword_ideas = max_keyword_ideas
        # En dessous de ce nombre de mots-clés à volume non nul, on considère que
        # Google n'a pas assez de matière et on complète par l'expansion LLM.
        self.min_viable_keywords = min_viable_keywords

    async def research(
        self, form: ClientForm
    ) -> tuple[list[KeywordWithData], list[dict]]:
        """Retourne : (KW enrichis, propositions cocons brutes du LLM).

        La construction des `CoconStructure` finaux se fait dans cocon_builder.py.
        """
        enriched = await self._gather_keywords(form)
        with_serp = await self._add_serp_features(enriched)
        cocoon_proposals = await self._select_cocoons(form, with_serp)
        return with_serp, cocoon_proposals

    async def _gather_keywords(self, form: ClientForm) -> list[KeywordWithData]:
        """Mots-clés réels via Google Ads, repli sur l'expansion LLM si vide.

        L'ordre compte : on demande d'abord à Google ce que les gens cherchent
        vraiment. L'expansion par LLM ne sert plus que de filet — elle produisait
        24 mots-clés morts sur 30 quand elle pilotait l'étape.
        """
        try:
            ideas = await self.dataforseo.get_keyword_ideas(
                form.seed_keywords, limit=self.max_keyword_ideas
            )
        except Exception as e:
            logger.warning("keywords_for_keywords indisponible (%s) — repli LLM.", e)
            ideas = []

        viable = [i for i in ideas if (i.get("monthly_volume") or 0) > 0]
        if len(viable) < self.min_viable_keywords:
            logger.warning(
                "Seulement %d mot(s)-clé(s) avec volume via Google Ads — "
                "complément par expansion LLM.",
                len(viable),
            )
            candidates = await self._expand_seeds(form)
            known = {i["keyword"] for i in ideas}
            extra = [c for c in candidates if c.keyword not in known]
            return ideas_to_models(ideas) + await self._enrich_with_volume(extra)

        logger.info(
            "%d mots-clés réels récupérés (%d avec volume mesuré)", len(ideas), len(viable)
        )
        return ideas_to_models(ideas)

    # ------------------------------------------------------------

    async def _expand_seeds(self, form: ClientForm) -> list[KeywordCandidate]:
        parsed, _ = await self.anthropic.complete_json(
            model="haiku",
            system=_EXPANSION_SYSTEM,
            user_prompt=_build_expansion_prompt(form),
            max_tokens=4096,
        )
        raw_keywords = parsed.get("keywords", []) if isinstance(parsed, dict) else []
        candidates: list[KeywordCandidate] = []
        for item in raw_keywords:
            try:
                candidates.append(
                    KeywordCandidate(
                        keyword=item["keyword"].strip().lower(),
                        intent=SearchIntent(item["intent"]),
                        cluster=item["cluster"],
                        relative_volume=item.get("relative_volume", "medium"),
                        difficulty_estimate=item.get("difficulty_estimate", "medium"),
                    )
                )
            except (KeyError, ValueError) as e:
                logger.warning("KW invalide ignoré: %s (%s)", item, e)

        # Dedup case-insensitive
        seen: set[str] = set()
        unique: list[KeywordCandidate] = []
        for c in candidates:
            if c.keyword not in seen:
                seen.add(c.keyword)
                unique.append(c)
        logger.info("Expansion: %d KW candidats uniques", len(unique))
        return unique

    async def _enrich_with_volume(
        self, candidates: list[KeywordCandidate]
    ) -> list[KeywordWithData]:
        keywords = [c.keyword for c in candidates]
        # DataForSEO accepte jusqu'à 1000 KW par requête
        volume_data = await self.dataforseo.get_search_volume(keywords)
        volume_by_kw = {item["keyword"]: item for item in volume_data if item.get("keyword")}

        enriched: list[KeywordWithData] = []
        for c in candidates:
            data = volume_by_kw.get(c.keyword, {})
            enriched.append(
                KeywordWithData(
                    keyword=c.keyword,
                    intent=c.intent,
                    cluster=c.cluster,
                    monthly_volume=data.get("monthly_volume"),
                    cpc=data.get("cpc"),
                    competition_score=data.get("competition_score"),
                    difficulty=data.get("difficulty"),
                )
            )
        return enriched

    async def _add_serp_features(
        self, enriched: list[KeywordWithData]
    ) -> list[KeywordWithData]:
        """Analyse SERP features des KW les plus intéressants (volume-adjusted)."""
        # Top N par volume × (1 - competition) pour cibler les meilleurs opportunités
        def score(k: KeywordWithData) -> float:
            vol = k.monthly_volume or 0
            comp = k.competition_score or 0.5
            return vol * (1 - comp)

        top = sorted(enriched, key=score, reverse=True)[: self.top_serp_analysis]
        top_keywords = {k.keyword for k in top}

        # Requêtes SERP en parallèle
        serp_results = await asyncio.gather(
            *(self.dataforseo.get_serp(k.keyword, depth=10) for k in top),
            return_exceptions=True,
        )

        serp_map: dict[str, SerpFeatures] = {}
        for kw_obj, serp in zip(top, serp_results):
            if isinstance(serp, Exception):
                logger.warning("SERP failed for %s: %s", kw_obj.keyword, serp)
                continue
            features_raw = serp.get("features", {})
            serp_map[kw_obj.keyword] = SerpFeatures(
                featured_snippet=features_raw.get("featured_snippet", False),
                people_also_ask=serp.get("paa", []),
                video_carousel=features_raw.get("video_carousel", False),
                image_pack=features_raw.get("image_pack", False),
                local_pack=features_raw.get("local_pack", False),
                knowledge_panel=features_raw.get("knowledge_panel", False),
            )

        # Attacher aux enriched
        for k in enriched:
            if k.keyword in top_keywords and k.keyword in serp_map:
                k.serp_features = serp_map[k.keyword]
        return enriched

    async def _select_cocoons(
        self, form: ClientForm, enriched: list[KeywordWithData]
    ) -> list[dict]:
        parsed, _ = await self.anthropic.complete_json(
            model="sonnet",
            system=_SELECTION_SYSTEM,
            user_prompt=_build_selection_prompt(form, enriched),
            max_tokens=8192,
        )
        cocoons = parsed.get("cocoons", []) if isinstance(parsed, dict) else []
        logger.info("Sélection: %d cocons proposés par le LLM", len(cocoons))
        return cocoons
