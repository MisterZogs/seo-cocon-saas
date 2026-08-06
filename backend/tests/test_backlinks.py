"""Vérifie que l'analyse backlinks cible bien le DOMAINE d'un concurrent.

Contexte : le premier run sur données réelles passait l'URL complète à l'API,
qui répondait alors au niveau page. Mesuré sur binance.com :

    binance.com/fr/copy-trading  → DR=0,  2 backlinks,  2 domaines référents
    binance.com                  → DR=85, 41 721 173 backlinks, 141 441 domaines

Le rapport livré à l'agence annonçait donc DR=0 pour Binance. Ce qui l'intéresse,
c'est l'autorité du concurrent, pas celle d'une de ses pages.

Usage :
    cd backend && .venv/bin/python -m tests.test_backlinks
"""

from __future__ import annotations

import sys

from pipeline.backlink_analyzer import _domain_of, _unique_domains


def _check(condition: bool, label: str) -> bool:
    print(f"  {'✓' if condition else '✗'} {label}")
    return condition


def test_extraction_domaine() -> bool:
    print("\n[1] Extraction du domaine")
    cas = [
        ("https://www.binance.com/fr/copy-trading", "binance.com", "www et chemin retirés"),
        ("http://coinacademy.fr/academie/copy-trading/", "coinacademy.fr", "http et chemin retirés"),
        ("https://fr.investing.com/brokers/", "fr.investing.com", "sous-domaine conservé"),
        ("https://EXAMPLE.com/Path", "example.com", "casse normalisée"),
        ("https://user:pw@example.com:8443/x", "example.com", "auth et port retirés"),
        ("", None, "chaîne vide → None"),
    ]
    ok = True
    for url, attendu, label in cas:
        ok &= _check(_domain_of(url) == attendu, label)
    return ok


def test_deduplication() -> bool:
    """Le top 10 contient souvent plusieurs pages d'un même site."""
    print("\n[2] Déduplication des concurrents")
    urls = [
        "https://www.binance.com/fr/copy-trading",
        "https://www.binance.com/fr/support/faq",   # même domaine
        "https://binance.com/autre",                 # même domaine, sans www
        "https://coinacademy.fr/academie/",
        "https://fr.investing.com/brokers/",
    ]
    out = _unique_domains(urls, limit=5)

    ok = _check(out == ["binance.com", "coinacademy.fr", "fr.investing.com"],
                "trois domaines distincts, ordre SERP conservé")
    ok &= _check(len(out) == len(set(out)), "aucun doublon")
    # Sans dédup on payait deux requêtes backlinks pour le même site.
    ok &= _check(out.count("binance.com") == 1, "binance.com compté une seule fois")
    return ok


def test_limite() -> bool:
    print("\n[3] Respect de la limite")
    urls = [f"https://site{i}.com/page" for i in range(10)]
    ok = _check(len(_unique_domains(urls, limit=5)) == 5, "limite à 5 respectée")
    ok &= _check(_unique_domains(urls, limit=0) == [], "limite 0 → liste vide")
    ok &= _check(_unique_domains([], limit=5) == [], "aucune URL → liste vide")
    # Cas du run réel : 10 résultats mais peu de domaines distincts.
    peu = ["https://a.com/1", "https://a.com/2", "https://b.com/1"]
    ok &= _check(_unique_domains(peu, limit=5) == ["a.com", "b.com"],
                 "moins de domaines que la limite : pas de plantage")
    return ok


def main() -> int:
    print("=" * 62)
    print("ANALYSE BACKLINKS — CIBLER LE DOMAINE, PAS LA PAGE")
    print("=" * 62)

    results = [test_extraction_domaine(), test_deduplication(), test_limite()]

    print("\n" + "=" * 62)
    if all(results):
        print(f"✓ {len(results)}/{len(results)} groupes OK")
        return 0
    print(f"✗ {results.count(False)}/{len(results)} groupe(s) en échec")
    return 1


if __name__ == "__main__":
    sys.exit(main())
