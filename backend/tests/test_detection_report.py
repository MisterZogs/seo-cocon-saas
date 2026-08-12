"""Vérifie le rapport de détection IA (chantier 6).

Ce rapport ne mesure rien — aucun appel à Pangram/Originality n'est fait à la
génération (payant, non maîtrisé côté produit). Il prédit le verdict à partir
de ce que le code contrôle : la présence de blocs verbatim. L'enjeu du test
est donc que le verdict et la part verbatim reflètent fidèlement les ids
réellement placés, jamais ceux fournis mais non utilisés.

Usage :
    cd backend && .venv/bin/python -m tests.test_detection_report
"""

from __future__ import annotations

import sys

from models import AIDetectionVerdict, ExperienceElement
from pipeline.article_generator import build_detection_report

ELEMENT_A = ExperienceElement(
    type="case_study",
    title="Intervention à 6 bars",
    content="On est intervenus sur un nid installé dans un conduit à 6 bars de pression.",
    source="Chantier Lyon 3e",
)
ELEMENT_B = ExperienceElement(
    type="data",
    title="Volumétrie annuelle",
    content="Douze mille visiteurs uniques par mois sur douze mois.",
)


def _check(condition: bool, label: str) -> bool:
    print(f"  {'✓' if condition else '✗'} {label}")
    return condition


def test_aucun_verbatim() -> bool:
    print("\n[1] Aucun élément placé")
    report = build_detection_report([], [ELEMENT_A], word_count=800)
    ok = _check(report.expected_verdict == AIDetectionVerdict.GENERATED, "verdict = generated")
    ok &= _check(report.verbatim_word_count == 0, "0 mot verbatim")
    ok &= _check(report.verbatim_share == 0.0, "part verbatim nulle")
    ok &= _check(len(report.talking_points) > 0, "argumentaire non vide malgré tout")
    ok &= _check(
        any("indétectable" in c for c in report.caveats), "mise en garde indétectable présente"
    )
    return ok


def test_un_verbatim_place() -> bool:
    print("\n[2] Un élément placé")
    content_words = len(ELEMENT_A.content.split())
    word_count = content_words * 10  # verbatim = 10 % de l'article
    report = build_detection_report([ELEMENT_A.id], [ELEMENT_A], word_count=word_count)
    ok = _check(report.expected_verdict == AIDetectionVerdict.MIXED, "verdict = mixed")
    ok &= _check(report.verbatim_word_count == content_words, "compte de mots verbatim exact")
    ok &= _check(abs(report.verbatim_share - 0.1) < 0.01, "part verbatim ≈ 10 %")
    return ok


def test_deux_verbatim_places() -> bool:
    print("\n[3] Deux éléments placés")
    total_words = len(ELEMENT_A.content.split()) + len(ELEMENT_B.content.split())
    report = build_detection_report(
        [ELEMENT_A.id, ELEMENT_B.id], [ELEMENT_A, ELEMENT_B], word_count=total_words * 2
    )
    ok = _check(report.verbatim_word_count == total_words, "les deux blocs comptés")
    ok &= _check(report.expected_verdict == AIDetectionVerdict.MIXED, "verdict = mixed")
    return ok


def test_id_fourni_mais_non_place() -> bool:
    print("\n[4] Élément fourni au cocon mais jamais placé dans CET article")
    # Cas réel : l'élément est attribué à un autre article de la run. build_detection_report
    # ne reçoit ici que les ids réellement injectés dans le markdown de cet article-ci.
    report = build_detection_report([], [ELEMENT_A, ELEMENT_B], word_count=500)
    ok = _check(report.expected_verdict == AIDetectionVerdict.GENERATED, "non crédité si non placé")
    ok &= _check(report.verbatim_word_count == 0, "aucun mot compté")
    return ok


def test_id_inconnu_ignore() -> bool:
    print("\n[5] Id verbatim inconnu (défensif)")
    report = build_detection_report(["id-fantome"], [ELEMENT_A], word_count=500)
    ok = _check(report.verbatim_word_count == 0, "id absent de la liste → 0 mot, pas de crash")
    return ok


def test_word_count_nul() -> bool:
    print("\n[6] word_count nul (défensif)")
    report = build_detection_report([ELEMENT_A.id], [ELEMENT_A], word_count=0)
    ok = _check(report.verbatim_share == 0.0, "part à 0 plutôt qu'une division par zéro")
    return ok


def test_part_plafonnee_a_un() -> bool:
    print("\n[7] Part verbatim plafonnée à 100 %")
    # Cas défensif : jamais censé arriver (le verbatim est un sous-ensemble de
    # l'article), mais un word_count incohérent ne doit pas produire une part > 1.
    report = build_detection_report([ELEMENT_A.id], [ELEMENT_A], word_count=1)
    ok = _check(report.verbatim_share <= 1.0, "part <= 1.0")
    return ok


def main() -> int:
    print("=" * 62)
    print("RAPPORT DE DÉTECTION IA")
    print("=" * 62)

    results = [
        test_aucun_verbatim(),
        test_un_verbatim_place(),
        test_deux_verbatim_places(),
        test_id_fourni_mais_non_place(),
        test_id_inconnu_ignore(),
        test_word_count_nul(),
        test_part_plafonnee_a_un(),
    ]

    print("\n" + "=" * 62)
    if all(results):
        print(f"✓ {len(results)}/{len(results)} groupes OK")
        return 0
    print(f"✗ {results.count(False)}/{len(results)} groupe(s) en échec")
    return 1


if __name__ == "__main__":
    sys.exit(main())
