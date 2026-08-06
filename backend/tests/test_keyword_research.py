"""Vérifie que le volume de recherche arrive intact jusqu'au prompt de sélection.

Contexte : le premier run sur données réelles a produit un cocon de 6 articles
totalisant 50 recherches/mois — mère à 50, les cinq filles à zéro. Deux bugs de
la même famille, une valeur réelle mais *falsy* écrasée par un placeholder :

  - `item.get("search_volume") or 0` confondait `null` (Google n'a pas de donnée)
    et `0` (mesuré, personne ne cherche) ;
  - `volume={k.monthly_volume or 'N/A'}` affichait « N/A » pour un volume de 0,
    donc le modèle ne pouvait pas distinguer un mot-clé mort d'une donnée absente.

Usage :
    cd backend && .venv/bin/python -m tests.test_keyword_research
"""

from __future__ import annotations

import sys

from models import ClientForm, KeywordWithData, SearchIntent
from pipeline.keyword_research import (
    _build_selection_prompt,
    _guess_intent,
    ideas_to_models,
)

FORM = ClientForm(
    product="Wall of Traders", description="Plateforme de crypto trading social",
    seed_keywords=["copy trading"], audience="investisseurs particuliers",
    niche="crypto trading",
)


def _check(condition: bool, label: str) -> bool:
    print(f"  {'✓' if condition else '✗'} {label}")
    return condition


def test_zero_vs_inconnu() -> bool:
    """Le prompt doit rendre « 0 mesuré » et « inconnu » visiblement différents."""
    print("\n[1] Volume 0 et volume inconnu ne se confondent plus")
    kws = [
        KeywordWithData(keyword="copy trading crypto", intent=SearchIntent.INFORMATIONAL,
                        cluster="", monthly_volume=50, cpc=3.81, competition_score=0.4),
        KeywordWithData(keyword="copier traders experimentes", intent=SearchIntent.INFORMATIONAL,
                        cluster="", monthly_volume=0, cpc=0.0, competition_score=None),
        KeywordWithData(keyword="signaux crypto fiables", intent=SearchIntent.COMMERCIAL,
                        cluster="", monthly_volume=None, cpc=None, competition_score=None),
    ]
    prompt = _build_selection_prompt(FORM, kws)

    ok = _check("volume=50" in prompt, "volume mesuré affiché tel quel")
    ok &= _check("AUCUNE RECHERCHE MESURÉE" in prompt, "zéro signalé explicitement")
    ok &= _check("volume=inconnu" in prompt, "donnée absente signalée comme inconnue")
    ok &= _check("volume=N/A" not in prompt, "plus aucun « N/A » ambigu")
    # Le cœur du bug : les deux lignes devaient être indiscernables, elles ne le sont plus.
    ligne_zero = [l for l in prompt.splitlines() if "copier traders" in l][0]
    ligne_inconnu = [l for l in prompt.splitlines() if "signaux crypto" in l][0]
    ok &= _check(
        ligne_zero.split("volume=")[1] != ligne_inconnu.split("volume=")[1],
        "les deux cas produisent bien un rendu différent",
    )
    return ok


def test_regles_de_selection() -> bool:
    print("\n[2] Le volume est posé comme contrainte dure")
    from pipeline.keyword_research import _SELECTION_SYSTEM

    ok = _check("HARD CONSTRAINT" in _SELECTION_SYSTEM, "contrainte dure énoncée")
    ok &= _check("Never pick a mother" in _SELECTION_SYSTEM, "mère sans volume interdite")
    ok &= _check("At most 2 of the 5 daughters" in _SELECTION_SYSTEM, "quota de filles sans volume")
    return ok


def test_ideas_to_models() -> bool:
    """La conversion ne doit rien inventer ni rien écraser."""
    print("\n[3] Conversion des suggestions Google Ads")
    ideas = [
        {"keyword": "acheter bitcoin", "monthly_volume": 1200, "cpc": 2.5,
         "competition_score": 0.7, "difficulty": 70},
        {"keyword": "trading crypto debutant", "monthly_volume": 0, "cpc": 0.0,
         "competition_score": None, "difficulty": None},
        {"keyword": "signaux trading", "monthly_volume": None, "cpc": None,
         "competition_score": None, "difficulty": None},
        {"keyword": None, "monthly_volume": 10},  # ligne corrompue
    ]
    models = ideas_to_models(ideas)

    ok = _check(len(models) == 3, "ligne sans mot-clé écartée")
    ok &= _check(models[1].monthly_volume == 0, "le zéro est conservé, pas transformé en None")
    ok &= _check(models[2].monthly_volume is None, "l'inconnu reste None, pas transformé en 0")
    ok &= _check(all(m.cluster == "" for m in models), "cluster laissé au LLM de sélection")
    ok &= _check(models[0].intent is SearchIntent.TRANSACTIONAL, "intent déduit de la formulation")
    return ok


def test_intent() -> bool:
    print("\n[4] Heuristique d'intention (français)")
    cas = [
        ("acheter bitcoin", SearchIntent.TRANSACTIONAL),
        ("prix du bitcoin", SearchIntent.TRANSACTIONAL),
        ("meilleur bot de trading", SearchIntent.COMMERCIAL),
        ("comparatif plateformes crypto", SearchIntent.COMMERCIAL),
        ("binance connexion", SearchIntent.NAVIGATIONAL),
        ("comment trader la crypto", SearchIntent.INFORMATIONAL),
        ("qu'est-ce que le dca", SearchIntent.INFORMATIONAL),
    ]
    ok = True
    for kw, attendu in cas:
        ok &= _check(_guess_intent(kw) is attendu, f"« {kw} » → {attendu.value}")
    return ok


def main() -> int:
    print("=" * 62)
    print("SÉLECTION DES MOTS-CLÉS — LE VOLUME DOIT SURVIVRE AU TRAJET")
    print("=" * 62)

    results = [
        test_zero_vs_inconnu(),
        test_regles_de_selection(),
        test_ideas_to_models(),
        test_intent(),
    ]

    print("\n" + "=" * 62)
    if all(results):
        print(f"✓ {len(results)}/{len(results)} groupes OK")
        return 0
    print(f"✗ {results.count(False)}/{len(results)} groupe(s) en échec")
    return 1


if __name__ == "__main__":
    sys.exit(main())
