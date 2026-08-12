"""Vérifie le score GEO (chantier 15a).

L'enjeu : ce score est déterministe, contrairement à l'E-E-A-T que le modèle
propose et qu'on doit plafonner. Chaque test part donc d'un article dont on
connaît le défaut, et vérifie que l'axe correspondant baisse — ET que les
autres ne bougent pas. Un score qui bouge pour la mauvaise raison serait pire
qu'une absence de score : c'est un chiffre qu'on affiche à une agence.

Usage :
    cd backend && .venv/bin/python -m tests.test_geo_score
"""

from __future__ import annotations

import sys

from models import (
    ArticleStub,
    ArticleType,
    ExternalLink,
    GeneratedArticle,
    SearchIntent,
    SerpAnalysis,
)
from pipeline.geo_score import score_articles_geo, score_geo

KEYWORD = "cocon sémantique"

ENTITIES = ["Laurent Bourrelly", "Google", "maillage interne", "silo"]
QUESTIONS = [
    "Qu'est-ce qu'un cocon sémantique ?",
    "Combien de pages dans un cocon ?",
]

SERP = SerpAnalysis(
    keyword=KEYWORD,
    scraped_pages_count=8,
    avg_word_count=1500,
    recommended_word_count=1800,
    avg_h2_count=6,
    recommended_h2_count=6,
    avg_h3_count=4,
    key_entities=ENTITIES,
    key_topics=["structure", "maillage"],
    common_questions=QUESTIONS,
    competitive_angle="angle",
    top_result_format="guide",
)

# Article volontairement conforme sur les cinq axes : sert de référence haute.
# Tous les tests suivants en dégradent UN seul point.
GOOD_MARKDOWN = """# Le cocon sémantique, expliqué

Un cocon sémantique est une arborescence de pages liées entre elles selon une
règle stricte. Popularisée par Laurent Bourrelly, la méthode organise le site
en silo étanche pour que Google comprenne le sujet central. Le maillage interne
y est calculé, jamais improvisé : chaque page reçoit exactement 5 liens.

## Qu'est-ce qu'un cocon sémantique ?

Le principe repose sur trois éléments :

- une page mère qui porte le sujet principal
- cinq pages filles qui traitent chacune un sous-sujet
- un maillage interne réciproque entre toutes les filles

Google évalue la cohérence de l'ensemble, pas des pages isolées.

## Combien de pages dans un cocon ?

| Rôle | Nombre | Liens sortants |
|---|---|---|
| Mère | 1 | 5 |
| Filles | 5 | 5 |

En 2026, la structure de référence compte 6 pages et 30 liens au total, soit
5 liens entrants par page.

## Comment construire le silo

Le silo repose sur l'étanchéité : aucun lien vers un autre cocon. Cette règle
date de 2013 et reste valable après 12 ans de mises à jour.

## Les erreurs fréquentes

Les 4 erreurs les plus courantes coûtent en moyenne 30 % du trafic potentiel.

## FAQ

### Qu'est-ce qu'un cocon sémantique ?

Une arborescence de pages liées selon une règle stricte.

### Combien de pages dans un cocon ?

Six : une mère et cinq filles.

### Le cocon fonctionne-t-il encore en 2026 ?

Oui, la méthode reste pertinente.
"""


def _check(condition: bool, label: str) -> bool:
    print(f"  {'✓' if condition else '✗'} {label}")
    return condition


def _article(markdown: str, *, links: int = 2, experience: bool = True) -> GeneratedArticle:
    return GeneratedArticle(
        stub=ArticleStub(
            cocon_id="c1",
            article_type=ArticleType.MOTHER,
            target_keyword=KEYWORD,
            h1_title="Le cocon sémantique, expliqué",
            meta_title="Cocon sémantique",
            meta_description="Guide du cocon sémantique.",
            slug="cocon-semantique",
            intent=SearchIntent.INFORMATIONAL,
        ),
        serp_analysis=SERP,
        sections=[],
        faq=[],
        internal_links=[],
        external_links=[
            ExternalLink(
                anchor_text=f"source {i}",
                url_suggestion=f"https://exemple.fr/{i}",
                domain_type="média",
                reason="référence",
            )
            for i in range(links)
        ],
        schema_jsonld={},
        content_markdown=markdown,
        experience_used=["e1"] if experience else [],
        word_count=len(markdown.split()),
    )


def test_article_conforme() -> bool:
    print("\n[1] Article conforme sur les cinq axes")
    score = score_geo(_article(GOOD_MARKDOWN))
    ok = _check(score.direct_answer == 100, f"réponse directe = 100 (obtenu {score.direct_answer})")
    ok &= _check(
        score.extractable_structure == 100,
        f"structure = 100 (obtenu {score.extractable_structure})",
    )
    ok &= _check(
        score.entity_coverage == 100, f"entités = 100 (obtenu {score.entity_coverage})"
    )
    ok &= _check(
        score.question_coverage == 100, f"questions = 100 (obtenu {score.question_coverage})"
    )
    ok &= _check(
        score.citations_and_data == 100, f"citations = 100 (obtenu {score.citations_and_data})"
    )
    ok &= _check(score.overall == 100, f"overall = 100 (obtenu {score.overall})")
    ok &= _check(score.findings == [], "aucune correction à proposer")
    return ok


def test_sans_chapo() -> bool:
    print("\n[2] Aucune réponse avant le premier H2")
    sans_intro = GOOD_MARKDOWN.replace(
        """Un cocon sémantique est une arborescence de pages liées entre elles selon une
règle stricte. Popularisée par Laurent Bourrelly, la méthode organise le site
en silo étanche pour que Google comprenne le sujet central. Le maillage interne
y est calculé, jamais improvisé : chaque page reçoit exactement 5 liens.

""",
        "",
    )
    score = score_geo(_article(sans_intro))
    ok = _check(score.direct_answer == 0, f"réponse directe effondrée (obtenu {score.direct_answer})")
    ok &= _check(
        score.extractable_structure == 100, "la structure n'est pas affectée"
    )
    ok &= _check(score.overall < 80, f"overall entraîné vers le bas (obtenu {score.overall})")
    ok &= _check(any("H1" in f for f in score.findings), "la correction nomme le chapô manquant")
    return ok


def test_mot_cle_absent_du_chapo() -> bool:
    print("\n[3] Le sujet n'est pas traité dans les 100 premiers mots")
    hors_sujet = GOOD_MARKDOWN.replace(
        "Un cocon sémantique est une arborescence de pages liées entre elles selon une\n"
        "règle stricte. Popularisée par Laurent Bourrelly, la méthode organise le site\n"
        "en silo étanche pour que Google comprenne le sujet central.",
        "Le référencement naturel a beaucoup changé. Laurent Bourrelly le rappelait "
        "déjà. Google fait évoluer ses critères en silo permanent.",
    )
    score = score_geo(_article(hors_sujet))
    ok = _check(score.direct_answer < 100, f"réponse directe pénalisée ({score.direct_answer})")
    ok &= _check(
        any(KEYWORD in f for f in score.findings), "la correction nomme le mot-clé absent"
    )
    return ok


def test_entites_manquantes() -> bool:
    print("\n[4] Entités du top 10 jamais nommées")
    sans_entites = GOOD_MARKDOWN.replace("Laurent Bourrelly", "un consultant").replace(
        "Google", "le moteur"
    )
    score = score_geo(_article(sans_entites))
    ok = _check(score.entity_coverage == 50, f"2 entités sur 4 → 50 (obtenu {score.entity_coverage})")
    ok &= _check(
        any("Laurent Bourrelly" in f for f in score.findings),
        "la correction liste les entités absentes",
    )
    ok &= _check(score.direct_answer == 100, "la réponse directe n'est pas affectée")
    return ok


def test_questions_non_reprises() -> bool:
    print("\n[5] Questions PAA sans titre correspondant")
    sans_questions = (
        GOOD_MARKDOWN.replace("## Qu'est-ce qu'un cocon sémantique ?", "## Le principe")
        .replace("## Combien de pages dans un cocon ?", "## Le format")
        .replace("### Qu'est-ce qu'un cocon sémantique ?", "### Le principe en bref")
        .replace("### Combien de pages dans un cocon ?", "### Le format retenu")
    )
    score = score_geo(_article(sans_questions))
    ok = _check(score.question_coverage == 0, f"aucune question reprise (obtenu {score.question_coverage})")
    ok &= _check(
        any("question" in f.lower() for f in score.findings), "la correction le signale"
    )
    ok &= _check(score.entity_coverage == 100, "les entités ne sont pas affectées")
    return ok


def test_structure_pauvre() -> bool:
    print("\n[6] Ni liste, ni tableau, ni FAQ")
    pauvre = "# Titre\n\n" + GOOD_MARKDOWN.split("\n\n", 1)[1].split("## Qu'est-ce")[0]
    pauvre += "\n## Une seule section\n\nDu texte sans structure particulière.\n"
    score = score_geo(_article(pauvre))
    ok = _check(
        score.extractable_structure == 0, f"structure effondrée (obtenu {score.extractable_structure})"
    )
    ok &= _check(len(score.findings) >= 4, "chaque contrôle manqué produit une correction")
    return ok


def test_sans_sources_ni_chiffres() -> bool:
    print("\n[7] Aucune source externe, aucun élément d'expérience")
    score = score_geo(_article(GOOD_MARKDOWN, links=0, experience=False))
    ok = _check(
        score.citations_and_data < 50, f"axe citations pénalisé (obtenu {score.citations_and_data})"
    )
    ok &= _check(
        any("source" in f.lower() for f in score.findings), "la correction nomme les sources"
    )
    ok &= _check(
        any("expérience" in f.lower() for f in score.findings),
        "la correction nomme l'absence d'expérience client",
    )
    return ok


def test_serp_sans_reference() -> bool:
    print("\n[8] Analyse SERP sans entité ni question — axes ÉCARTÉS, pas crédités")
    # Le vrai piège du chantier. Sans référence SERP, l'axe n'est pas mesurable.
    # Le noter 0 accuserait l'article d'un défaut qui vient de l'analyse ; le
    # noter 100 offrirait 40 % du score à un run dont le scrape a échoué —
    # exactement l'auto-flatterie que ce score est censé éviter. Mesuré sur deux
    # runs à SERP simulée : 4 articles sortaient à 100/100 pour cette seule raison.
    article = _article(GOOD_MARKDOWN)
    article.serp_analysis = SERP.model_copy(update={"key_entities": [], "common_questions": []})
    score = score_geo(article)
    ok = _check(score.entity_coverage is None, "couverture d'entités non mesurée")
    ok &= _check(score.question_coverage is None, "couverture des questions non mesurée")
    ok &= _check(len(score.unmeasured) == 2, "les deux axes écartés sont annoncés")
    ok &= _check(
        not any("entité" in f for f in score.findings), "aucune correction inventée"
    )
    # Les trois axes restants sont parfaits : la moyenne renormalisée vaut 100.
    ok &= _check(score.overall == 100, f"moyenne sur les axes mesurables (obtenu {score.overall})")

    # Et surtout : un article médiocre ne doit PAS être sauvé par les axes écartés.
    mediocre = _article(GOOD_MARKDOWN, links=0, experience=False)
    mediocre.serp_analysis = article.serp_analysis
    plein = _article(GOOD_MARKDOWN, links=0, experience=False)
    ok &= _check(
        score_geo(mediocre).overall <= score_geo(plein).overall,
        "écarter un axe ne gonfle jamais le score d'un article médiocre",
    )
    return ok


def test_low_sample_signale() -> bool:
    print("\n[8b] Échantillon SERP faible signalé")
    article = _article(GOOD_MARKDOWN)
    article.serp_analysis = SERP.model_copy(update={"low_sample": True})
    score = score_geo(article)
    ok = _check(
        any("faible" in u for u in score.unmeasured),
        "la faiblesse de l'échantillon est annoncée",
    )
    ok &= _check(score.entity_coverage == 100, "les axes restent mesurés pour autant")
    return ok


def test_marqueurs_de_maillage_ignores() -> bool:
    print("\n[9] Les marqueurs de maillage comptent pour leur ancre")
    # Le score tourne APRÈS la normalisation du maillage : le markdown porte des
    # [[INTERNAL_LINK:slug|ancre]]. L'ancre est du texte visible, le slug non.
    avec_marqueurs = GOOD_MARKDOWN.replace(
        "Popularisée par Laurent Bourrelly",
        "Popularisée par [[INTERNAL_LINK:methode-bourrelly|Laurent Bourrelly]]",
    )
    score = score_geo(_article(avec_marqueurs))
    ok = _check(score.entity_coverage == 100, "l'entité dans l'ancre est bien comptée")
    ok &= _check(score.direct_answer == 100, "le marqueur ne casse pas l'analyse du chapô")
    return ok


def test_notation_sur_place() -> bool:
    print("\n[10] score_articles_geo note la liste sur place")
    articles = [_article(GOOD_MARKDOWN), _article(GOOD_MARKDOWN, links=0, experience=False)]
    ok = _check(all(a.geo_score is None for a in articles), "aucun score avant l'appel")
    score_articles_geo(articles)
    ok &= _check(all(a.geo_score is not None for a in articles), "les deux articles sont notés")
    ok &= _check(
        articles[0].geo_score.overall > articles[1].geo_score.overall,
        "l'article mieux sourcé obtient un meilleur score",
    )
    score_articles_geo([])
    ok &= _check(True, "liste vide tolérée")
    return ok


def main() -> int:
    print("=" * 62)
    print("SCORE GEO — DÉTERMINISTE")
    print("=" * 62)

    results = [
        test_article_conforme(),
        test_sans_chapo(),
        test_mot_cle_absent_du_chapo(),
        test_entites_manquantes(),
        test_questions_non_reprises(),
        test_structure_pauvre(),
        test_sans_sources_ni_chiffres(),
        test_serp_sans_reference(),
        test_marqueurs_de_maillage_ignores(),
        test_notation_sur_place(),
    ]

    print("\n" + "=" * 62)
    if all(results):
        print(f"✓ {len(results)}/{len(results)} groupes OK")
        return 0
    print(f"✗ {results.count(False)}/{len(results)} groupe(s) en échec")
    return 1


if __name__ == "__main__":
    sys.exit(main())
