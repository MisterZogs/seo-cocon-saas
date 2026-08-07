"""Vérifie la calibration de la structure (nombre de H2, plafond de H3).

Le run frelons du 2026-08-07 a servi de référence : les concurrents scrapés
faisaient en moyenne 6,2 H2 et 5,8 H3, le pipeline sortait 10,8 H2 et 28,3 H3
— soit 4,9× plus de sous-titres que le top 10, ce qui donnait au corps des
articles l'allure d'une FAQ.

Deux points sont testés :
  · `_clamp_h2` ramène la recommandation dans une bande liée au SERP observé.
    Claude renvoyait 9 ou 10 quel que soit le SERP, y compris face à des
    concurrents qui font 3 H2.
  · le budget de H3 et les règles de structure arrivent bien dans les deux
    prompts (Brief et Full) — une règle absente du prompt n'existe pas.

Usage :
    cd backend && .venv/bin/python -m tests.test_structure
"""

from __future__ import annotations

import sys

from models import (
    ArticleStub,
    ArticleType,
    CoconStructure,
    InterCoconPolicy,
    SearchIntent,
    SerpAnalysis,
)
from pipeline.article_generator import (
    _build_brief_prompt,
    _build_full_prompt,
    _h3_budget,
    _structure_rules,
)
from pipeline.serp_analyzer import _clamp_h2


def _check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}{(' — ' + detail) if detail and not cond else ''}")
    return cond


def _analysis(avg_h2: int, avg_h3: int, reco_h2: int) -> SerpAnalysis:
    return SerpAnalysis(
        keyword="nid de frelons",
        scraped_pages_count=6,
        serp_urls_count=9,
        rejected_pages={},
        low_sample=False,
        avg_word_count=1800,
        recommended_word_count=2400,
        avg_h2_count=avg_h2,
        recommended_h2_count=reco_h2,
        avg_h3_count=avg_h3,
        key_entities=["frelon asiatique"],
        key_topics=["identification"],
        common_questions=["Comment identifier un nid ?"],
        content_gaps=["tarifs réels"],
        competitive_angle="guide complet",
        top_result_format="guide long",
    )


def _stub() -> ArticleStub:
    return ArticleStub(
        cocon_id="cocon_1",
        article_type=ArticleType.MOTHER,
        target_keyword="nid de frelons",
        h1_title="Nid de frelons",
        slug="nid-de-frelons",
        meta_title="Nid de frelons",
        meta_description="desc",
        intent=SearchIntent.INFORMATIONAL,
        secondary_keywords=[],
    )


def _cocon(stub: ArticleStub) -> CoconStructure:
    return CoconStructure(
        id="cocon_1",
        theme="frelons",
        main_keyword="nid de frelons",
        rationale="r",
        mother=stub,
        daughters=[],
    )


def main() -> int:
    ok = True

    # --- Bornage du nombre de H2 ---------------------------------------
    print("\n_clamp_h2 — bandes calées sur le SERP observé")

    # Cas réels du run frelons : Claude recommandait 9 face à 3 concurrents.
    ok &= _check("concurrents sobres (avg 3) : 9 → 5", _clamp_h2(9, 3) == 5, str(_clamp_h2(9, 3)))
    ok &= _check("concurrents sobres (avg 4) : 9 → 6", _clamp_h2(9, 4) == 6, str(_clamp_h2(9, 4)))
    # Là où les concurrents sont riches, la reco passe intacte.
    ok &= _check("concurrents riches (avg 9) : 10 inchangé", _clamp_h2(10, 9) == 10, str(_clamp_h2(10, 9)))
    ok &= _check("concurrents riches (avg 8) : 9 inchangé", _clamp_h2(9, 8) == 9, str(_clamp_h2(9, 8)))
    # Jamais en dessous de la moyenne : couvrir moins que le top 10 n'a pas de sens.
    ok &= _check("plancher = moyenne concurrente", _clamp_h2(2, 7) == 7, str(_clamp_h2(2, 7)))
    # Un SERP dégénéré ne doit pas produire un plafond absurde.
    ok &= _check("avg 0 : plafond à 2, pas 0", _clamp_h2(9, 0) == 2, str(_clamp_h2(9, 0)))

    # --- Budget de H3 ---------------------------------------------------
    print("\n_h3_budget — plafond calé sur le top 10")
    ok &= _check("suit la moyenne concurrente", _h3_budget(_analysis(6, 6, 8)) == 6)
    ok &= _check("plancher à 3 si scrape pauvre", _h3_budget(_analysis(6, 0, 8)) == 3)
    ok &= _check(
        "reste très en dessous du comportement observé (28 H3/article)",
        _h3_budget(_analysis(7, 9, 9)) < 15,
    )

    # --- Les règles arrivent dans les prompts ---------------------------
    print("\nPrésence dans les prompts (une règle absente du prompt n'existe pas)")
    analysis = _analysis(avg_h2=4, avg_h3=4, reco_h2=6)
    stub, budget = _stub(), _h3_budget(_analysis(4, 4, 6))
    cocon = _cocon(stub)

    rules = _structure_rules(analysis)
    ok &= _check("le plafond chiffré figure dans les règles", f"{budget} MAXIMUM" in rules)
    ok &= _check("la FAQ est exclue du budget", "does not count against the budget" in rules)
    ok &= _check("le motif FAQ est nommé et interdit", "read like a FAQ" in rules)

    for name, prompt in (
        ("brief", _build_brief_prompt(stub, cocon, analysis, InterCoconPolicy.STRICT)),
        ("full", _build_full_prompt(stub, cocon, analysis, InterCoconPolicy.STRICT)),
    ):
        ok &= _check(f"prompt {name} : bloc STRUCTURE présent", "STRUCTURE — calibrate" in prompt)
        ok &= _check(f"prompt {name} : moyenne H3 du top 10 exposée", "Avg H3 count in top 10" in prompt)
        ok &= _check(
            f"prompt {name} : n'enseigne plus le motif H2→H3",
            '"h3s": ["..."]' not in prompt and '"h3s": ["Subsection 1", "Subsection 2"]' not in prompt,
        )

    full = _build_full_prompt(stub, cocon, analysis, InterCoconPolicy.STRICT)
    ok &= _check(
        "squelette markdown : plus de ### dans le corps d'exemple",
        full.count("### H3") == 0,
    )
    ok &= _check(
        "squelette markdown : la FAQ garde ses H3",
        "## FAQ" in full and "### Question ?" in full,
    )

    print("\n" + "=" * 60)
    print("TOUS LES CONTRÔLES PASSENT" if ok else "ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
