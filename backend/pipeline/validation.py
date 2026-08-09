"""Validation humaine de la sélection de mots-clés, entre l'étape 1 et l'étape 3.

Le pipeline s'arrête après la recherche de mots-clés et soumet à l'agence ce que
le modèle a retenu, avec sa justification. L'agence décoche, recoche, désigne la
mère, puis relance. Sans cette étape, une sélection ratée ne se découvre qu'après
avoir payé 12 générations d'articles — et le run est alors bon à jeter.

Deux fonctions, une dans chaque sens :

    build_snapshot()   propositions du modèle  →  ce que le front affiche
    apply_decision()   choix de l'agence       →  propositions reconstruites

`apply_decision` rend des dicts au format exact attendu par `CoconBuilder.build`,
donc le reste du pipeline ne sait rien de cette étape et n'a pas à en tenir compte.

Les métadonnées (H1, meta title/description, slug) sont conservées telles que le
modèle les avait écrites pour les mots-clés qu'il avait déjà choisis. Elles ne
sont régénérées que pour les mots-clés ajoutés à la main par l'agence, qui n'en
ont aucune — un appel Haiku, groupé pour tout le run.
"""

from __future__ import annotations

import logging
from typing import Any

from clients.anthropic_client import AnthropicClient
from models import (
    ArticleType,
    ClientForm,
    CoconProposal,
    KeywordPick,
    KeywordWithData,
    SearchIntent,
    ValidationDecision,
    ValidationSnapshot,
)

logger = logging.getLogger(__name__)


def _norm(keyword: str) -> str:
    return keyword.strip().lower()


# ============================================================
# SENS 1 — propositions du modèle → écran de validation
# ============================================================


def build_snapshot(
    run_id: str,
    keywords: list[KeywordWithData],
    proposals: list[dict],
) -> ValidationSnapshot:
    """Assemble ce que l'écran de validation affiche.

    Les données chiffrées viennent de DataForSEO et non du modèle : c'est la
    seule partie de l'écran qui ne soit pas une opinion. Un mot-clé proposé par
    le modèle mais absent du pool enrichi garde `monthly_volume=None` plutôt
    qu'un zéro trompeur — « Google n'a pas la donnée » et « personne ne
    cherche » ne se confondent pas.
    """
    by_kw = {_norm(k.keyword): k for k in keywords}

    out: list[CoconProposal] = []
    for index, raw in enumerate(proposals):
        picks: list[KeywordPick] = []
        entries = [(ArticleType.MOTHER, raw.get("mother"))]
        entries += [(ArticleType.DAUGHTER, d) for d in raw.get("daughters", [])]

        for role, data in entries:
            if not isinstance(data, dict):
                continue
            kw = (data.get("target_keyword") or "").strip()
            if not kw:
                continue
            enriched = by_kw.get(_norm(kw))
            picks.append(
                KeywordPick(
                    keyword=kw,
                    role=role,
                    reason=(data.get("reason") or "").strip(),
                    monthly_volume=enriched.monthly_volume if enriched else None,
                    cpc=enriched.cpc if enriched else None,
                    competition_score=enriched.competition_score if enriched else None,
                    difficulty=enriched.difficulty if enriched else None,
                    intent=_intent(data.get("intent"), enriched),
                )
            )

        out.append(
            CoconProposal(
                index=index,
                theme=raw.get("theme") or f"Cocon {index + 1}",
                main_keyword=raw.get("main_keyword") or "",
                rationale=raw.get("rationale") or "",
                picks=picks,
            )
        )

    return ValidationSnapshot(run_id=run_id, proposals=out, pool=keywords)


def _intent(raw: Any, enriched: KeywordWithData | None) -> SearchIntent:
    try:
        return SearchIntent(raw)
    except (ValueError, TypeError):
        return enriched.intent if enriched else SearchIntent.INFORMATIONAL


# ============================================================
# SENS 2 — décision de l'agence → propositions reconstruites
# ============================================================


async def apply_decision(
    decision: ValidationDecision,
    proposals: list[dict],
    keywords: list[KeywordWithData],
    form: ClientForm,
    anthropic: AnthropicClient,
) -> list[dict]:
    """Reconstruit les propositions de cocons à partir des choix de l'agence.

    Lève `ValueError` si un mot-clé retenu n'existe ni dans les propositions du
    modèle ni dans le pool enrichi : mieux vaut refuser la validation que lancer
    une génération sur un mot-clé inventé côté client.
    """
    known_stubs = _index_stubs(proposals)
    pool = {_norm(k.keyword): k for k in keywords}

    missing: list[str] = []
    for cocon in decision.cocoons:
        for kw in [cocon.mother_keyword, *cocon.daughter_keywords]:
            key = _norm(kw)
            if key not in known_stubs and key not in pool:
                missing.append(kw)
    if missing:
        raise ValueError(
            "Mot(s)-clé(s) inconnu(s) du run : " + ", ".join(sorted(set(missing)))
        )

    to_generate = [
        kw
        for cocon in decision.cocoons
        for kw in [cocon.mother_keyword, *cocon.daughter_keywords]
        if _norm(kw) not in known_stubs
    ]
    generated = await _generate_stubs(to_generate, form, anthropic) if to_generate else {}

    rebuilt: list[dict] = []
    for cocon in decision.cocoons:
        source = proposals[cocon.index] if cocon.index < len(proposals) else {}

        def stub_for(kw: str) -> dict:
            key = _norm(kw)
            data = dict(known_stubs.get(key) or generated.get(key) or {})
            # Le rôle change quand l'agence promeut une fille en mère : le stub
            # est réutilisé tel quel, seul `target_keyword` fait autorité.
            data["target_keyword"] = kw
            return data

        rebuilt.append(
            {
                "theme": cocon.theme or source.get("theme") or f"Cocon {cocon.index + 1}",
                "main_keyword": cocon.mother_keyword,
                "rationale": source.get("rationale", ""),
                "mother": stub_for(cocon.mother_keyword),
                "daughters": [stub_for(kw) for kw in cocon.daughter_keywords],
            }
        )

    logger.info(
        "Validation appliquée : %d cocon(s), %d mot(s)-clé(s) ajouté(s) à la main",
        len(rebuilt),
        len(to_generate),
    )
    return rebuilt


def _index_stubs(proposals: list[dict]) -> dict[str, dict]:
    """Tous les stubs déjà écrits par le modèle, quel que soit leur cocon d'origine.

    L'index est global et pas par cocon : l'agence peut déplacer un mot-clé du
    cocon 1 vers le cocon 2, auquel cas son H1 et sa meta restent valables.
    """
    stubs: dict[str, dict] = {}
    for raw in proposals:
        for data in [raw.get("mother"), *raw.get("daughters", [])]:
            if isinstance(data, dict) and (data.get("target_keyword") or "").strip():
                stubs[_norm(data["target_keyword"])] = data
    return stubs


_STUB_SYSTEM = """You write SEO metadata for articles in a semantic cocoon. You are given \
keywords an SEO professional added by hand to a selection. For each one, produce the \
article metadata. Be concrete and specific to the keyword — never generic filler."""


async def _generate_stubs(
    keywords: list[str], form: ClientForm, anthropic: AnthropicClient
) -> dict[str, dict]:
    """Métadonnées des mots-clés ajoutés à la main, en un seul appel Haiku.

    Un mot-clé ajouté par l'agence n'a ni H1, ni meta, ni slug : le modèle ne
    l'avait pas retenu, donc il n'a jamais écrit son stub. Les régénérer tous
    d'un coup coûte une fraction de centime ; refaire tourner la sélection
    complète risquerait au contraire de ne pas respecter le choix de l'agence.
    """
    listing = "\n".join(f"- {k}" for k in keywords)
    prompt = f"""Client context:
- Product: {form.product}
- Niche: {form.niche}
- Audience: {form.audience}
- Language: {form.language}

Keywords added by hand, needing metadata:
{listing}

For EACH keyword return:
- target_keyword: the keyword, copied exactly as given
- h1_title: compelling article title, 50-70 chars, in {form.language}
- meta_title: max 60 chars
- meta_description: 140-160 chars
- slug: URL-friendly, in {form.language}
- intent: "informational" | "commercial" | "transactional" | "navigational"
- secondary_keywords: 2-3 supporting keywords

Return JSON: {{"stubs": [{{...}}, ...]}} — one object per keyword, same order."""

    try:
        parsed, _ = await anthropic.complete_json(
            model="haiku",
            system=_STUB_SYSTEM,
            user_prompt=prompt,
            max_tokens=4096,
        )
    except Exception as e:
        logger.warning("Génération des stubs ajoutés impossible (%s) — repli minimal.", e)
        return {_norm(k): _fallback_stub(k) for k in keywords}

    out: dict[str, dict] = {}
    for item in (parsed or {}).get("stubs", []):
        kw = (item.get("target_keyword") or "").strip()
        if kw:
            out[_norm(kw)] = item

    # Un mot-clé oublié par le modèle ne doit pas faire échouer la validation :
    # l'agence a fait son choix, on lui livre un stub par défaut plutôt qu'une
    # erreur qu'elle ne peut pas corriger elle-même.
    for kw in keywords:
        if _norm(kw) not in out:
            logger.warning("Stub manquant pour « %s » — repli minimal.", kw)
            out[_norm(kw)] = _fallback_stub(kw)
    return out


def _fallback_stub(keyword: str) -> dict:
    title = keyword.strip().capitalize()
    return {
        "target_keyword": keyword.strip(),
        "h1_title": title,
        "meta_title": title[:60],
        "meta_description": f"{title} : guide complet."[:160],
        "slug": keyword.strip(),
        "intent": "informational",
        "secondary_keywords": [],
    }
