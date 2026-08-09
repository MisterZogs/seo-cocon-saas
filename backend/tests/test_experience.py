"""Vérifie l'injection verbatim des éléments d'expérience et le plafond E-E-A-T.

L'enjeu : ces blocs sont les SEULS passages de l'article qui ne sont pas générés.
Si le modèle les reformule, on perd à la fois le signal E-E-A-T réel et les seuls
segments qu'un détecteur puisse créditer comme humains. D'où la substitution en
code, au caractère près — le prompt ne fait que poser le marqueur.

Usage :
    cd backend && .venv/bin/python -m tests.test_experience
"""

from __future__ import annotations

import sys

from models import (
    ArticleStub,
    ArticleType,
    ClientForm,
    CoconStructure,
    EEATScore,
    ExperienceElement,
    StyleSample,
)
from pipeline.article_generator import (
    _NO_EXPERIENCE_CAP,
    _build_cached_context,
    _build_experience_context,
    _cap_experience_score,
    assign_experience_elements,
    inject_experience_blocks,
)

# Le cas réel de Gaetan : l'anecdote qui ressortait paraphrasée du pipeline.
VERBATIM = (
    "On est intervenus sur un nid installé dans un conduit à 6 bars de pression.\n\n"
    "Le client avait déjà tenté deux fois seul, avec une bombe du commerce."
)

ELEMENT = ExperienceElement(
    type="case_study",
    title="Intervention à 6 bars",
    content=VERBATIM,
    source="Chantier Lyon 3e, mars 2026",
)


def _check(condition: bool, label: str) -> bool:
    print(f"  {'✓' if condition else '✗'} {label}")
    return condition


def _element(n: int) -> ExperienceElement:
    return ExperienceElement(type="insight", title=f"Retour {n}", content=f"Vécu numéro {n}.")


def _stub(slug: str, article_type: ArticleType) -> ArticleStub:
    return ArticleStub(
        cocon_id="c",
        article_type=article_type,
        target_keyword=slug,
        h1_title=slug,
        meta_title=slug,
        meta_description=slug,
        slug=slug,
        intent="informational",
    )


def _cocon(prefix: str, n_daughters: int = 5) -> CoconStructure:
    return CoconStructure(
        id=prefix,
        theme="t",
        main_keyword="k",
        mother=_stub(f"{prefix}-mere", ArticleType.MOTHER),
        daughters=[
            _stub(f"{prefix}-fille{i}", ArticleType.DAUGHTER) for i in range(1, n_daughters + 1)
        ],
        rationale="r",
    )


def test_verbatim_preserved() -> bool:
    print("\n[1] Le contenu client est repris au caractère près")
    markdown = f"Intro.\n\n## Terrain\n\nCe qu'on a vu :\n\n[[EXPERIENCE:{ELEMENT.id}]]\n\nLa leçon.\n"
    out, used, unused = inject_experience_blocks(markdown, [ELEMENT])

    ok = _check(all(line in out for line in VERBATIM.splitlines() if line), "chaque ligne intacte")
    ok &= _check(used == [ELEMENT.id], "élément marqué comme utilisé")
    ok &= _check(unused == [], "aucun élément orphelin")
    ok &= _check("> " in out, "rendu en block quote")
    ok &= _check("Chantier Lyon 3e" in out, "source attribuée")
    ok &= _check("[[EXPERIENCE" not in out, "aucun marqueur résiduel")
    return ok


def test_marqueurs_invalides() -> bool:
    """Un marqueur cassé ne doit jamais atteindre le livrable de l'agence."""
    print("\n[2] Marqueurs inconnus et doublons")
    out, used, _ = inject_experience_blocks(
        f"a\n\n[[EXPERIENCE:nexistepas]]\n\nb\n\n[[EXPERIENCE:{ELEMENT.id}]]\n\n[[EXPERIENCE:{ELEMENT.id}]]\n",
        [ELEMENT],
    )
    ok = _check("[[EXPERIENCE" not in out, "marqueurs retirés du markdown")
    ok &= _check(len(used) == 1, "élément compté une seule fois malgré la double référence")
    # Sur le corps, pas sur le titre — celui-ci est repris dans l'attribution.
    ok &= _check(out.count("avec une bombe du commerce") == 1, "bloc non dupliqué")
    return ok


def test_element_non_place() -> bool:
    print("\n[3] Élément fourni mais jamais placé par le modèle")
    out, used, unused = inject_experience_blocks("Article sans marqueur.", [ELEMENT])
    ok = _check(out == "Article sans marqueur.", "markdown inchangé")
    ok &= _check(used == [] and unused == [ELEMENT.id], "signalé comme non utilisé")
    return ok


def test_unicite_sur_la_run() -> bool:
    """Un élément placé dans un article ne doit pas réapparaître dans le suivant.

    Sans ce garde-fou, un run réel plaçait le même bloc dans les 6 articles du
    cocon — du contenu dupliqué à l'intérieur du silo.
    """
    print("\n[4] Unicité de l'élément sur toute la run")
    md = f"Intro.\n\n[[EXPERIENCE:{ELEMENT.id}]]\n\nSuite."

    premier, used1, _ = inject_experience_blocks(md, [ELEMENT], already_used=set())
    ok = _check(used1 == [ELEMENT.id], "1er article : élément placé")
    ok &= _check("6 bars" in premier, "bloc présent dans le 1er article")

    second, used2, unused2 = inject_experience_blocks(md, [ELEMENT], already_used=set(used1))
    ok &= _check(used2 == [], "2e article : élément refusé")
    ok &= _check("6 bars" not in second, "bloc absent du 2e article")
    ok &= _check("[[EXPERIENCE" not in second, "marqueur retiré proprement")
    ok &= _check(unused2 == [ELEMENT.id], "signalé comme non placé ici")
    return ok


def test_amorce_orpheline() -> bool:
    """Retirer un marqueur doit emporter l'amorce et la chute qui l'encadraient.

    Cas réel (`nid-de-frelon-dans-la-terre_APRES.md`) : l'élément avait été
    consommé par la mère, le marqueur a sauté, et il restait dans la fille
    « Voici comment se passe concrètement une intervention de ce type. » suivi
    de rien du tout, puis une chute qui commentait un bloc absent.
    """
    print("\n[5] Amorce et chute orphelines après retrait du marqueur")
    md = (
        "## L'intervention professionnelle\n\n"
        "Un professionnel dispose d'outils que le grand public n'a pas.\n\n"
        "Voici comment se passe concrètement une intervention de ce type.\n\n"
        f"[[EXPERIENCE:{ELEMENT.id}]]\n\n"
        "Ce type d'intervention garantit que l'insecticide atteint toutes les loges.\n\n"
        "## Coût d'une intervention"
    )
    out, used, _ = inject_experience_blocks(md, [ELEMENT], already_used={ELEMENT.id})

    ok = _check(used == [], "élément non replacé")
    ok &= _check("Voici comment se passe" not in out, "amorce retirée")
    ok &= _check("Ce type d'intervention garantit" not in out, "chute retirée")
    ok &= _check("Un professionnel dispose d'outils" in out, "paragraphe de fond conservé")
    ok &= _check(out.count("## ") == 2, "les deux H2 conservés")
    ok &= _check("\n\n\n" not in out, "pas de trou dans le markdown")

    # Le nettoyage ne doit jamais mordre sur autre chose que de la prose courte.
    protege = (
        "## Titre\n\n"
        "| Critère | Valeur |\n|---|---|\n| Taille | 25 mm |\n\n"
        f"[[EXPERIENCE:{ELEMENT.id}]]\n\n"
        "- premier point\n- second point"
    )
    out2, _, _ = inject_experience_blocks(protege, [ELEMENT], already_used={ELEMENT.id})
    ok &= _check("| Taille | 25 mm |" in out2, "tableau au-dessus préservé")
    ok &= _check("- second point" in out2, "liste en dessous préservée")
    ok &= _check("## Titre" in out2, "titre préservé")

    # Un marqueur bien résolu ne déclenche aucun nettoyage.
    intact, used3, _ = inject_experience_blocks(md, [ELEMENT])
    ok &= _check(used3 == [ELEMENT.id], "élément placé quand il est disponible")
    ok &= _check("Voici comment se passe" in intact, "amorce conservée quand le bloc arrive")
    ok &= _check("Ce type d'intervention garantit" in intact, "chute conservée aussi")
    return ok


def test_repartition() -> bool:
    """Chaque élément est attribué à un article précis, mères servies en premier.

    Avant : la mère passait en premier et consommait tout ce qu'elle voulait,
    donc les cinq filles sortaient sans un seul passage non généré.
    """
    print("\n[6] Répartition des éléments sur les articles de la run")
    cocons = [_cocon("a"), _cocon("b")]
    elements = [_element(i) for i in range(1, 5)]
    form = ClientForm(
        product="p", description="d", seed_keywords=["k"], audience="a", niche="n",
        experience_elements=elements,
    )
    assigned = assign_experience_elements(form, cocons)

    ok = _check(len(assigned) == 12, "les 12 articles de la run sont dans la table")
    ok &= _check(
        [e.id for e in assigned["a-mere"]] == [elements[0].id]
        and [e.id for e in assigned["b-mere"]] == [elements[1].id],
        "les deux mères servies en premier",
    )
    ok &= _check(
        assigned["a-fille1"] and assigned["b-fille1"],
        "les filles suivantes alternent entre les cocons",
    )
    place = [e.id for els in assigned.values() for e in els]
    ok &= _check(len(place) == len(set(place)) == 4, "chaque élément attribué une seule fois")

    # Plus d'éléments que d'articles : on boucle, sans jamais dupliquer un id.
    beaucoup = ClientForm(
        product="p", description="d", seed_keywords=["k"], audience="a", niche="n",
        experience_elements=[_element(i) for i in range(1, 15)],
    )
    large = assign_experience_elements(beaucoup, cocons)
    tous = [e.id for els in large.values() for e in els]
    ok &= _check(len(tous) == len(set(tous)) == 14, "14 éléments répartis sans doublon")
    ok &= _check(len(large["a-mere"]) == 2, "la mère en reçoit un deuxième au second tour")

    vide = assign_experience_elements(
        ClientForm(product="p", description="d", seed_keywords=["k"], audience="a", niche="n"),
        cocons,
    )
    ok &= _check(all(not v for v in vide.values()), "aucun élément fourni → table vide")
    ok &= _check(assign_experience_elements(form, []) == {}, "aucun cocon → pas de plantage")
    return ok


def test_plafond_eeat() -> bool:
    """Sans matériau first-hand, le score « expérience » ne doit pas mentir."""
    print("\n[7] Plafond E-E-A-T quand aucun bloc verbatim n'est placé")
    inflated = EEATScore(
        experience=80, expertise=80, authoritativeness=60, trustworthiness=60,
        overall=70, warnings=[],
    )

    capped = _cap_experience_score(inflated, [])
    ok = _check(capped.experience == _NO_EXPERIENCE_CAP, f"expérience plafonnée à {_NO_EXPERIENCE_CAP}")
    ok &= _check(capped.overall == 60, "overall recalculé (60), pas laissé à 70")
    ok &= _check(len(capped.warnings) == 1, "avertissement ajouté pour l'agence")

    kept = _cap_experience_score(inflated, [ELEMENT.id])
    ok &= _check(kept.experience == 80, "score conservé quand un bloc est bien placé")

    honest = EEATScore(
        experience=20, expertise=70, authoritativeness=70, trustworthiness=70,
        overall=55, warnings=["déjà noté"],
    )
    ok &= _check(_cap_experience_score(honest, []) is honest, "score déjà bas laissé intact")
    ok &= _check(_cap_experience_score(None, []) is None, "absence de score tolérée")
    return ok


def test_contexte_cache() -> bool:
    """Le style reste cacheable ; l'expérience passe par article.

    Les échantillons de style sont identiques pour toute la run, donc ils vont
    dans le préfixe mis en cache. Les éléments d'expérience, eux, sont désormais
    attribués article par article : les mettre dans le préfixe partagé les
    exposerait à tous les articles, ce qui est exactement le comportement qu'on
    a supprimé.
    """
    print("\n[8] Contexte partagé (cache) vs contexte par article")
    form = ClientForm(
        product="Désinsectisation", description="Traitement nids de frelons",
        seed_keywords=["nid de frelons"], audience="particuliers", niche="services",
        experience_elements=[ELEMENT],
        style_samples=[StyleSample(title="Guide frelons", content="Un frelon, ça ne prévient pas. " * 20)],
    )
    context = _build_cached_context(form, [])

    ok = _check("AUTHOR VOICE" in context, "échantillons de style présents")
    ok &= _check("Un frelon, ça ne prévient pas." in context, "contenu du sample injecté")
    ok &= _check("EXPERIENCE ELEMENTS" not in context, "expérience hors du préfixe caché")
    ok &= _check(ELEMENT.content not in context, "aucun matériau client dans le cache partagé")

    par_article = _build_experience_context([ELEMENT])
    ok &= _check(f"id=`{ELEMENT.id}`" in par_article, "id de l'élément exposé au modèle")
    ok &= _check("NEVER paraphrase" in par_article, "règle du verbatim énoncée")
    ok &= _check("assigned to THIS article" in par_article, "attribution explicite dans le prompt")
    ok &= _check(_build_experience_context([]) == "", "rien à dire sans élément attribué")

    vide = _build_cached_context(
        ClientForm(product="x", description="y", seed_keywords=["z"], audience="a", niche="n"), []
    )
    ok &= _check("AUTHOR VOICE" not in vide, "aucun bloc parasite sans échantillon")
    return ok


def main() -> int:
    print("=" * 62)
    print("INJECTION VERBATIM DES ÉLÉMENTS D'EXPÉRIENCE")
    print("=" * 62)

    results = [
        test_verbatim_preserved(),
        test_marqueurs_invalides(),
        test_element_non_place(),
        test_unicite_sur_la_run(),
        test_plafond_eeat(),
        test_contexte_cache(),
    ]

    print("\n" + "=" * 62)
    if all(results):
        print(f"✓ {len(results)}/{len(results)} groupes OK")
        return 0
    print(f"✗ {results.count(False)}/{len(results)} groupe(s) en échec")
    return 1


if __name__ == "__main__":
    sys.exit(main())
