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

from models import ClientForm, EEATScore, ExperienceElement, StyleSample
from pipeline.article_generator import (
    _NO_EXPERIENCE_CAP,
    _build_cached_context,
    _cap_experience_score,
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


def test_plafond_eeat() -> bool:
    """Sans matériau first-hand, le score « expérience » ne doit pas mentir."""
    print("\n[5] Plafond E-E-A-T quand aucun bloc verbatim n'est placé")
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
    """Style + expérience doivent finir dans le préfixe cacheable, pas par article."""
    print("\n[6] Contexte partagé (mis en cache)")
    form = ClientForm(
        product="Désinsectisation", description="Traitement nids de frelons",
        seed_keywords=["nid de frelons"], audience="particuliers", niche="services",
        experience_elements=[ELEMENT],
        style_samples=[StyleSample(title="Guide frelons", content="Un frelon, ça ne prévient pas. " * 20)],
    )
    context = _build_cached_context(form, [])

    ok = _check("AUTHOR VOICE" in context, "échantillons de style présents")
    ok &= _check("Un frelon, ça ne prévient pas." in context, "contenu du sample injecté")
    ok &= _check(f"id=`{ELEMENT.id}`" in context, "id de l'élément exposé au modèle")
    ok &= _check("NEVER paraphrase" in context, "règle du verbatim énoncée")

    vide = _build_cached_context(
        ClientForm(product="x", description="y", seed_keywords=["z"], audience="a", niche="n"), []
    )
    ok &= _check("AUTHOR VOICE" not in vide, "aucun bloc parasite sans échantillon")
    ok &= _check("EXPERIENCE ELEMENTS" not in vide, "aucun bloc parasite sans élément")
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
