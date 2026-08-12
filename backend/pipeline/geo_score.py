"""Chantier 15a : score GEO par article — calculé EN CODE, jamais par le modèle.

GEO (Generative Engine Optimization) = être *cité* par les moteurs génératifs
(AI Overviews, ChatGPT, Perplexity) plutôt qu'être classé. Les tactiques de base
sont publiques : réponse directe en tête, structure extractible, entités nommées
explicitement, données chiffrées, sources citées. Ce n'est donc pas un
différenciateur (voir CLAUDE.md, test « une agence peut-elle le refaire avec un
abonnement ChatGPT et un après-midi ? ») — c'est un rattrapage de parité, Hack
the SEO le donne dans sa version gratuite.

**Ce qui est fait différemment ici, et qui est le seul angle défendable :
le score est déterministe.** L'E-E-A-T est proposé par le LLM et a dû être
plafonné en code parce que le modèle se notait 70-80 sur des articles écrits
intégralement à partir de sources publiques. On ne refait pas cette erreur : rien
de ce fichier ne passe par un prompt. Chaque point est justifié par un contrôle
qu'une agence peut refaire à la main sur le markdown livré.

La couverture d'entités et de questions n'est possible que parce que
`SerpAnalysis` porte déjà `key_entities` et `common_questions`, extraites du top
10 réel. On mesure donc l'article contre la SERP, pas contre une opinion.

⚠️ Un score n'est pas une garantie de citation. Aucun moteur génératif ne publie
son critère de sélection ; ces cinq axes sont les signaux publiquement documentés
et consensuels, pas une formule officielle. Le libellé côté produit doit rester
« optimisation vérifiée », jamais « garanti cité ».
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from models import GEOScore, GeneratedArticle, SerpAnalysis

logger = logging.getLogger(__name__)

_LINK_MARKER = re.compile(r"\[\[INTERNAL_LINK:[^|\]]+\|([^\]]+)\]\]")
_EXPERIENCE_MARKER = re.compile(r"\[\[EXPERIENCE:[^\]]+\]\]")
_H1 = re.compile(r"^#\s+.*$", re.MULTILINE)
_H2 = re.compile(r"^##\s+(?!#)(.*)$", re.MULTILINE)
_H3 = re.compile(r"^###\s+(.*)$", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+]\s+\S", re.MULTILINE)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+\S", re.MULTILINE)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_NUMERIC = re.compile(r"\d")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Mots vides FR + EN : on compare des ensembles de mots porteurs, pas des
# phrases entières. Sans ce filtre, « comment » et « le » suffiraient à faire
# passer une question pour couverte.
_STOPWORDS = {
    "a", "à", "au", "aux", "avec", "ce", "ces", "cet", "cette", "ceux", "dans",
    "de", "des", "du", "elle", "elles", "en", "est", "et", "eux", "il", "ils",
    "je", "la", "le", "les", "leur", "lui", "ma", "mais", "me", "même", "mes",
    "moi", "mon", "ne", "nos", "notre", "nous", "on", "ou", "où", "par", "pas",
    "pour", "qu", "que", "qui", "quoi", "sa", "se", "ses", "son", "sur", "ta",
    "te", "tes", "toi", "ton", "tu", "un", "une", "vos", "votre", "vous", "y",
    "c", "d", "j", "l", "m", "n", "s", "t", "quel", "quelle", "quels",
    "quelles", "comment", "pourquoi", "quand", "combien", "faut", "il",
    "the", "of", "and", "to", "in", "is", "for", "on", "what", "how", "why",
    "when", "does", "do", "are", "with", "can",
}


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _normalize(text: str) -> str:
    """Minuscules, sans accents, ponctuation réduite à des espaces."""
    flat = _strip_accents(text.lower())
    return re.sub(r"[^a-z0-9]+", " ", flat).strip()


def _content_words(text: str) -> set[str]:
    return {w for w in _normalize(text).split() if len(w) > 2 and w not in _STOPWORDS}


def _plain_text(markdown: str) -> str:
    """Markdown → prose lisible, marqueurs résolus.

    Les marqueurs de maillage deviennent leur ancre : l'ancre est du texte que
    le lecteur voit, elle doit compter comme telle. Un marqueur d'expérience
    résiduel, lui, ne devrait plus exister à ce stade (il a été substitué), donc
    on le retire au lieu de le compter comme du contenu.
    """
    text = _LINK_MARKER.sub(r"\1", markdown)
    text = _EXPERIENCE_MARKER.sub("", text)
    text = re.sub(r"[#*_`>|]", " ", text)
    return text


@dataclass(frozen=True)
class _Check:
    """Un point de contrôle : des points, et ce qu'il faut faire s'il échoue."""

    points: int
    passed: bool
    fix: str


def _tally(checks: list[_Check]) -> tuple[int, list[str]]:
    """(score sur 100, corrections à faire). Le total des points fait 100."""
    total = sum(c.points for c in checks)
    earned = sum(c.points for c in checks if c.passed)
    score = round(earned * 100 / total) if total else 0
    return score, [c.fix for c in checks if not c.passed]


# ============================================================
# LES CINQ AXES
# ============================================================


def _intro_text(markdown: str) -> str:
    """Ce qui se trouve entre le H1 et le premier H2 — la zone de réponse."""
    body = _H1.sub("", markdown, count=1)
    first_h2 = _H2.search(body)
    intro = body[: first_h2.start()] if first_h2 else body
    return _plain_text(intro).strip()


def _score_direct_answer(markdown: str, target_keyword: str) -> tuple[int, list[str]]:
    """Réponse directe dans les 100 premiers mots — le signal GEO le plus fort.

    Un moteur génératif cite un passage qui répond ; il ne lit pas une mise en
    bouche. C'est aussi le seul axe où le pipeline avait déjà une consigne de
    prompt (« Answer the target keyword's question in the first 100 words »)
    sans jamais vérifier qu'elle avait été suivie.
    """
    intro = _intro_text(markdown)
    words = intro.split()
    first_100 = " ".join(words[:100])

    keyword_words = _content_words(target_keyword)
    covered = keyword_words & _content_words(first_100)
    keyword_present = bool(keyword_words) and len(covered) >= max(
        1, round(len(keyword_words) * 0.6)
    )

    sentences = [s for s in _SENTENCE_SPLIT.split(intro) if s.strip()]
    first_sentence_words = len(sentences[0].split()) if sentences else 0

    checks = [
        _Check(
            25,
            bool(words),
            "Aucun texte entre le H1 et le premier H2 : l'article n'offre aucune "
            "zone de réponse à extraire. Ajouter un chapô qui répond directement.",
        ),
        _Check(
            40,
            keyword_present,
            f"Le sujet « {target_keyword} » n'est pas explicitement traité dans les "
            "100 premiers mots. Un moteur génératif n'y trouve pas la réponse à citer.",
        ),
        _Check(
            20,
            0 < first_sentence_words <= 35,
            "La première phrase dépasse 35 mots (ou manque) : trop longue pour être "
            "reprise telle quelle comme réponse. La raccourcir.",
        ),
        _Check(
            15,
            40 <= len(words) <= 200,
            "Le chapô fait moins de 40 mots ou plus de 200 : trop court pour répondre, "
            "ou trop long pour être extrait d'un bloc.",
        ),
    ]
    return _tally(checks)


def _score_extractable_structure(markdown: str) -> tuple[int, list[str]]:
    """Structure extractible : listes, tableaux, FAQ, découpage en H2."""
    lists = len(_BULLET.findall(markdown)) + len(_NUMBERED.findall(markdown))
    tables = len(_TABLE_ROW.findall(markdown))
    h2s = _H2.findall(markdown)
    faq_h3 = len(_H3.findall(markdown))
    has_faq = any("faq" in _normalize(h) for h in h2s)

    checks = [
        _Check(
            30,
            lists >= 3,
            "Moins de 3 éléments de liste dans tout l'article : rien à extraire sous "
            "forme de puces. Convertir au moins une énumération en liste.",
        ),
        _Check(
            20,
            tables >= 2,
            "Aucun tableau : les comparaisons et chiffres en tableau sont ce que les "
            "moteurs génératifs reprennent le plus volontiers.",
        ),
        _Check(
            25,
            has_faq and faq_h3 >= 3,
            "Section FAQ absente ou de moins de 3 questions. La FAQ alimente le schema "
            "FAQPage et fournit des paires question/réponse directement citables.",
        ),
        _Check(
            25,
            len(h2s) >= 4,
            "Moins de 4 sections H2 : l'article est trop peu découpé pour qu'un passage "
            "précis soit isolé et cité.",
        ),
    ]
    return _tally(checks)


def _score_entity_coverage(markdown: str, analysis: SerpAnalysis) -> tuple[int, list[str]]:
    """Part des entités du top 10 réellement nommées dans l'article.

    Mesuré contre `SerpAnalysis.key_entities`, extraites des pages qui se
    classent déjà. Une entité absente est une entité que le moteur ne pourra pas
    relier à l'article.
    """
    entities = [e for e in analysis.key_entities if e.strip()]
    if not entities:
        # Sans entité de référence, il n'y a rien à mesurer. Rendre 0 serait
        # accuser l'article d'un défaut qui vient de l'analyse SERP.
        return 100, []

    text = _normalize(_plain_text(markdown))
    text_words = set(text.split())

    missing: list[str] = []
    for entity in entities:
        normalized = _normalize(entity)
        if normalized and normalized in text:
            continue
        words = _content_words(entity)
        if words and words <= text_words:
            continue
        missing.append(entity)

    covered = len(entities) - len(missing)
    score = round(covered * 100 / len(entities))
    fixes = (
        [
            f"{len(missing)} entité(s) du top 10 jamais nommée(s) : "
            f"{', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}."
        ]
        if missing
        else []
    )
    return score, fixes


def _score_question_coverage(markdown: str, analysis: SerpAnalysis) -> tuple[int, list[str]]:
    """Part des questions PAA reprises en titre ou en FAQ.

    Une question posée telle quelle dans un H2/H3 est la forme la plus
    directement citable qui existe : c'est littéralement la requête de
    l'utilisateur, suivie de sa réponse.
    """
    questions = [q for q in analysis.common_questions if q.strip()]
    if not questions:
        return 100, []

    headings = _H2.findall(markdown) + _H3.findall(markdown)
    heading_words = [_content_words(h) for h in headings]

    missing: list[str] = []
    for question in questions:
        words = _content_words(question)
        if not words:
            continue
        # Deux mots porteurs au minimum. « Qu'est-ce qu'un cocon sémantique ? »
        # se réduit à {cocon, sémantique} : un seuil de 60 % y vaudrait UN seul
        # mot commun, et n'importe quel titre contenant « cocon » suffirait à
        # déclarer la question couverte. Mesuré : le score restait à 100 sur un
        # article dont tous les titres avaient été réécrits.
        needed = max(2, ceil(len(words) * 0.6)) if len(words) >= 2 else 1
        if any(len(words & hw) >= needed for hw in heading_words):
            continue
        missing.append(question)

    answered = len(questions) - len(missing)
    score = round(answered * 100 / len(questions))
    fixes = (
        [
            f"{len(missing)} question(s) fréquente(s) de la SERP sans titre ni entrée "
            f"FAQ correspondante : « {missing[0]} »"
            + (f" (+{len(missing) - 1} autre(s))" if len(missing) > 1 else "")
            + "."
        ]
        if missing
        else []
    )
    return score, fixes


def _score_citations_and_data(article: GeneratedArticle) -> tuple[int, list[str]]:
    """Sources externes citées et données chiffrées attribuables."""
    text = _plain_text(article.content_markdown)
    numeric_tokens = [t for t in text.split() if _NUMERIC.search(t)]
    sourced_links = [l for l in article.external_links if l.url_suggestion]

    checks = [
        _Check(
            35,
            len(article.external_links) >= 2,
            "Moins de 2 sources externes citées : rien qui rattache les affirmations "
            "de l'article à une référence vérifiable.",
        ),
        _Check(
            25,
            len(sourced_links) >= 1,
            "Aucune source externe avec une URL : une source sans lien n'est pas "
            "vérifiable par un moteur.",
        ),
        _Check(
            25,
            len(numeric_tokens) >= 8,
            "Moins de 8 valeurs chiffrées dans l'article : les moteurs génératifs "
            "reprennent en priorité les passages qui portent des chiffres datés.",
        ),
        _Check(
            15,
            bool(article.experience_used),
            "Aucun élément d'expérience client : pas de donnée propriétaire, donc rien "
            "que les concurrents ne puissent dire aussi.",
        ),
    ]
    return _tally(checks)


# ============================================================
# ENTRÉE PUBLIQUE
# ============================================================

# La réponse directe pèse le plus : c'est le seul axe dont l'absence disqualifie
# l'article pour une citation, quel que soit le reste.
_WEIGHTS = {
    "direct_answer": 0.30,
    "extractable_structure": 0.20,
    "entity_coverage": 0.20,
    "question_coverage": 0.20,
    "citations_and_data": 0.10,
}


def score_geo(article: GeneratedArticle) -> GEOScore:
    """Note un article sur les cinq axes GEO. Aucun appel LLM, aucun coût."""
    markdown = article.content_markdown
    analysis = article.serp_analysis

    direct, f1 = _score_direct_answer(markdown, article.stub.target_keyword)
    structure, f2 = _score_extractable_structure(markdown)
    entities, f3 = _score_entity_coverage(markdown, analysis)
    questions, f4 = _score_question_coverage(markdown, analysis)
    citations, f5 = _score_citations_and_data(article)

    overall = round(
        direct * _WEIGHTS["direct_answer"]
        + structure * _WEIGHTS["extractable_structure"]
        + entities * _WEIGHTS["entity_coverage"]
        + questions * _WEIGHTS["question_coverage"]
        + citations * _WEIGHTS["citations_and_data"]
    )

    return GEOScore(
        direct_answer=direct,
        extractable_structure=structure,
        entity_coverage=entities,
        question_coverage=questions,
        citations_and_data=citations,
        overall=overall,
        findings=[*f1, *f2, *f3, *f4, *f5],
    )


def score_articles_geo(articles: list[GeneratedArticle]) -> None:
    """Note tous les articles d'un run, sur place.

    Appelé APRÈS la normalisation du maillage : celle-ci ajoute des marqueurs de
    lien et, au besoin, une section « Sur le même sujet ». Noter avant ferait
    porter le score sur un texte qui n'est pas celui qu'on livre.
    """
    for article in articles:
        article.geo_score = score_geo(article)

    if articles:
        scores = [a.geo_score.overall for a in articles if a.geo_score]
        logger.info(
            "GEO: %d article(s) notés, moyenne %d/100, min %d, max %d",
            len(scores),
            round(sum(scores) / len(scores)) if scores else 0,
            min(scores) if scores else 0,
            max(scores) if scores else 0,
        )
