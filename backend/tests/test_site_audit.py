"""Vérifie l'audit de maillage d'un site existant (chantier 14).

Deux zones de risque, très inégales :

1. **La normalisation d'URL.** Toute la justesse du rapport en dépend. Si
   `/page` et `/page/` comptent pour deux nœuds, la moitié du site devient
   orpheline et le rapport est bon à jeter — pire qu'absent, parce qu'un
   chiffre faux se cite en réunion.
2. **Le calcul du graphe**, et notamment la distinction entre *orpheline* (ne
   reçoit aucun lien) et *inatteignable* (reçoit un lien, mais depuis une page
   elle-même inatteignable). Les confondre sous-estime le problème.

Usage :
    cd backend && .venv/bin/python -m tests.test_site_audit
"""

from __future__ import annotations

import gzip
import sys

from pipeline.site_audit import (
    build_report,
    normalize_url,
    parse_sitemap,
    same_site,
    _decode_sitemap,
)

ROOT = "https://exemple.fr"


def _check(condition: bool, label: str, detail: str = "") -> bool:
    print(f"  {'✓' if condition else '✗'} {label}" + (f"  [{detail}]" if detail and not condition else ""))
    return condition


def test_normalisation() -> bool:
    print("\n[1] Normalisation d'URL")
    n = normalize_url
    cas = [
        ("https://Exemple.FR/Blog", "https://exemple.fr/Blog", "hôte en minuscules, chemin intact"),
        ("https://exemple.fr/blog/", "https://exemple.fr/blog", "slash final retiré"),
        ("https://www.exemple.fr/blog", "https://exemple.fr/blog", "www retiré"),
        ("https://exemple.fr/blog/index.html", "https://exemple.fr/blog", "index.html retiré"),
        ("https://exemple.fr/p#ancre", "https://exemple.fr/p", "fragment retiré"),
        ("https://exemple.fr/p?utm_source=nl", "https://exemple.fr/p", "utm retiré"),
        ("https://exemple.fr/p?page=2", "https://exemple.fr/p?page=2", "vrai paramètre gardé"),
        ("https://exemple.fr/p?b=2&a=1", "https://exemple.fr/p?a=1&b=2", "paramètres triés"),
        ("https://exemple.fr:443/x", "https://exemple.fr/x", "port par défaut retiré"),
        ("https://exemple.fr", "https://exemple.fr/", "racine → /"),
        ("https://exemple.fr/", "https://exemple.fr/", "la racine garde son slash"),
    ]
    ok = True
    for brut, attendu, label in cas:
        got = n(brut)
        ok &= _check(got == attendu, label, f"{brut} → {got} ≠ {attendu}")

    # Ce qui n'est pas une page doit disparaître, pas devenir un nœud muet.
    for brut, label in [
        ("mailto:a@b.fr", "mailto"),
        ("tel:+33100000000", "tel"),
        ("javascript:void(0)", "javascript"),
        ("#section", "ancre seule"),
        ("https://exemple.fr/doc.pdf", "pdf"),
        ("https://exemple.fr/style.css", "css"),
        ("https://exemple.fr/photo.JPG", "image (casse mixte)"),
        ("ftp://exemple.fr/x", "schéma non http"),
        ("", "chaîne vide"),
    ]:
        ok &= _check(n(brut) is None, f"écarté : {label}", str(n(brut)))

    ok &= _check(
        n("/relatif", base="https://exemple.fr/dossier/page") == "https://exemple.fr/relatif",
        "résolution d'une URL relative",
    )
    ok &= _check(
        n("../autre", base="https://exemple.fr/a/b/c") == "https://exemple.fr/a/autre",
        "résolution d'un chemin remontant",
    )

    # Le piège qui fabrique de fausses orphelines : les trois formes doivent
    # converger vers un SEUL nœud.
    formes = {
        n("https://exemple.fr/guide/"),
        n("https://www.exemple.fr/guide"),
        n("https://exemple.fr/guide/index.html"),
        n("https://exemple.fr/guide#intro"),
    }
    ok &= _check(len(formes) == 1, "4 écritures de la même page → 1 nœud", str(formes))
    return ok


def test_meme_site() -> bool:
    print("\n[2] Périmètre du site")
    ok = _check(same_site("https://exemple.fr/a", ROOT), "même hôte")
    ok &= _check(
        not same_site("https://autre.fr/a", ROOT), "hôte différent exclu"
    )
    # Un sous-domaine est un autre site pour le maillage interne, et l'inclure
    # ferait exploser le périmètre du crawl.
    ok &= _check(
        not same_site("https://blog.exemple.fr/a", ROOT), "sous-domaine exclu"
    )
    ok &= _check(
        same_site(normalize_url("https://www.exemple.fr/a"), ROOT),
        "www normalisé = même site",
    )
    return ok


def test_sitemap() -> bool:
    print("\n[3] Lecture du sitemap")
    urlset = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://exemple.fr/a</loc></url>
      <url><loc>https://exemple.fr/b</loc></url>
    </urlset>"""
    pages, nested = parse_sitemap(urlset)
    ok = _check(len(pages) == 2 and not nested, "urlset simple", f"{pages} {nested}")

    index = """<?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://exemple.fr/sitemap-1.xml</loc></sitemap>
      <sitemap><loc>https://exemple.fr/sitemap-2.xml</loc></sitemap>
    </sitemapindex>"""
    pages, nested = parse_sitemap(index)
    ok &= _check(
        not pages and len(nested) == 2,
        "sitemapindex : des sous-sitemaps, PAS des pages",
        f"pages={pages} nested={nested}",
    )

    # Sans cette distinction, un site rendrait « 2 pages », toutes deux des .xml.
    ok &= _check(
        all(u.endswith(".xml") for u in nested), "les sous-sitemaps sont bien des .xml"
    )

    gz = gzip.compress(urlset.encode())
    ok &= _check(len(parse_sitemap(_decode_sitemap(gz))[0]) == 2, "sitemap gzippé")
    ok &= _check(
        len(parse_sitemap(_decode_sitemap(urlset.encode()))[0]) == 2, "sitemap non gzippé"
    )
    ok &= _check(parse_sitemap("<html><body>pas un sitemap</body></html>") == ([], []),
                 "HTML servi à la place d'un sitemap → rien")
    return ok


def _report(outgoing: dict[str, set[str]], start: str = ROOT + "/"):
    return build_report(
        start_url=start,
        sitemap_url=None,
        pages_discovered=len(outgoing),
        truncated=False,
        titles={u: f"Titre {u}" for u in outgoing},
        words={u: 500 for u in outgoing},
        outgoing=outgoing,
        failed={},
    )


def test_cocon_conforme() -> bool:
    print("\n[4] Un cocon conforme obtient un rapport vierge")
    # 1 mère + 5 filles, toutes liées entre elles : exactement ce que le produit
    # génère. Si l'audit trouvait un défaut ici, il serait inutilisable.
    mere = f"{ROOT}/"
    filles = [f"{ROOT}/f{i}" for i in range(1, 6)]
    outgoing = {mere: set(filles)}
    for f in filles:
        outgoing[f] = {mere} | {o for o in filles if o != f}

    r = _report(outgoing)
    ok = _check(r.pages_crawled == 6, "6 pages")
    ok &= _check(r.total_internal_links == 30, "30 liens internes", str(r.total_internal_links))
    ok &= _check(not r.orphans, "aucune orpheline", str(r.orphans))
    ok &= _check(not r.dead_ends, "aucun cul-de-sac", str(r.dead_ends))
    ok &= _check(not r.unreachable, "aucune inatteignable", str(r.unreachable))
    ok &= _check(r.reciprocity_rate == 1.0, "réciprocité totale", str(r.reciprocity_rate))
    ok &= _check(r.depth_distribution == {"0": 1, "1": 5}, "tout à 1 clic de l'accueil",
                 str(r.depth_distribution))
    ok &= _check(
        any("Aucun défaut structurel" in f for f in r.findings),
        "le rapport le dit explicitement",
        str(r.findings),
    )
    return ok


def test_orpheline_et_inatteignable() -> bool:
    print("\n[5] 🔴 Orpheline ≠ inatteignable")
    # / → /a → /   (composante atteignable)
    # /b → /c      (b n'est lié par personne ; c reçoit un lien, de b seulement)
    # /c → rien    (cul-de-sac)
    outgoing = {
        f"{ROOT}/": {f"{ROOT}/a"},
        f"{ROOT}/a": {f"{ROOT}/"},
        f"{ROOT}/b": {f"{ROOT}/c"},
        f"{ROOT}/c": set(),
    }
    r = _report(outgoing)

    ok = _check(r.orphans == [f"{ROOT}/b"], "seule /b est orpheline", str(r.orphans))
    ok &= _check(
        sorted(r.unreachable) == [f"{ROOT}/b", f"{ROOT}/c"],
        "/b ET /c sont inatteignables",
        str(r.unreachable),
    )
    # C'est tout l'intérêt de la distinction : /c reçoit bien un lien, donc
    # n'est pas orpheline, mais aucun visiteur venu de l'accueil ne l'atteindra.
    ok &= _check(
        f"{ROOT}/c" not in r.orphans and f"{ROOT}/c" in r.unreachable,
        "/c : pas orpheline, mais inatteignable",
    )
    ok &= _check(r.dead_ends == [f"{ROOT}/c"], "/c est le seul cul-de-sac", str(r.dead_ends))
    ok &= _check(
        any("inatteignable" in f for f in r.findings), "signalé dans les constats"
    )
    ok &= _check(
        any("orpheline" in f for f in r.findings), "orphelines signalées aussi"
    )
    return ok


def test_liens_hors_perimetre() -> bool:
    print("\n[6] Les liens vers des pages non crawlées ne comptent pas")
    # `build_report` reçoit déjà des liens filtrés sur les pages connues :
    # ce test verrouille l'invariant, parce qu'un lien vers l'extérieur compté
    # comme interne gonflerait la réciprocité et masquerait les culs-de-sac.
    outgoing = {f"{ROOT}/": {f"{ROOT}/a"}, f"{ROOT}/a": set()}
    r = _report(outgoing)
    ok = _check(r.total_internal_links == 1, "1 seul lien compté", str(r.total_internal_links))
    ok &= _check(r.avg_outbound == 0.5, "moyenne sortante = 0,5", str(r.avg_outbound))
    ok &= _check(r.reciprocity_rate == 0.0, "aucune réciprocité", str(r.reciprocity_rate))
    return ok


def test_profondeur() -> bool:
    print("\n[7] Profondeur de clic")
    # Une chaîne : / → /a → /b → /c → /d  (d est à 4 clics)
    chaine = [f"{ROOT}/", f"{ROOT}/a", f"{ROOT}/b", f"{ROOT}/c", f"{ROOT}/d"]
    outgoing = {u: ({chaine[i + 1]} if i + 1 < len(chaine) else set())
                for i, u in enumerate(chaine)}
    r = _report(outgoing)
    ok = _check(
        r.depth_distribution == {"0": 1, "1": 1, "2": 1, "3": 1, "4": 1},
        "un palier par niveau",
        str(r.depth_distribution),
    )
    ok &= _check(
        any("4 clics ou plus" in f for f in r.findings), "les pages profondes sont signalées"
    )
    profondeurs = {p.url: p.depth for p in r.pages}
    ok &= _check(profondeurs[f"{ROOT}/d"] == 4, "/d à 4 clics", str(profondeurs))
    return ok


def test_site_vide_et_plafond() -> bool:
    print("\n[8] Cas limites")
    vide = build_report(
        start_url=ROOT, sitemap_url=None, pages_discovered=0, truncated=False,
        titles={}, words={}, outgoing={}, failed={"HTTP 403": 3},
        failed_urls={f"{ROOT}/x": "HTTP 403", f"{ROOT}/y": "HTTP 403", f"{ROOT}/z": "HTTP 403"},
    )
    ok = _check(vide.pages_crawled == 0, "0 page crawlée")
    ok &= _check(vide.reciprocity_rate == 0.0, "pas de division par zéro")
    ok &= _check(vide.avg_inbound == 0.0 and vide.avg_outbound == 0.0, "moyennes à 0")
    ok &= _check(vide.pages_failed == {"HTTP 403": 3}, "les échecs sont rapportés")
    # Un décompte par motif ne suffit pas dans un livrable : sans les URL,
    # l'agence ne peut ni vérifier ni corriger.
    ok &= _check(
        len(vide.failed_urls) == 3 and f"{ROOT}/y" in vide.failed_urls,
        "les URL en échec sont nommées, pas seulement comptées",
        str(vide.failed_urls),
    )

    tronque = build_report(
        start_url=ROOT, sitemap_url=None, pages_discovered=5000, truncated=True,
        titles={f"{ROOT}/": None}, words={}, outgoing={f"{ROOT}/": set()}, failed={},
    )
    ok &= _check(
        any("plafond" in f for f in tronque.findings),
        "la troncature est annoncée, pas masquée",
        str(tronque.findings),
    )
    return ok


def main() -> int:
    print("=" * 62)
    print("AUDIT DE MAILLAGE D'UN SITE EXISTANT")
    print("=" * 62)

    results = [
        test_normalisation(),
        test_meme_site(),
        test_sitemap(),
        test_cocon_conforme(),
        test_orpheline_et_inatteignable(),
        test_liens_hors_perimetre(),
        test_profondeur(),
        test_site_vide_et_plafond(),
    ]

    print("\n" + "=" * 62)
    if all(results):
        print(f"✓ {len(results)}/{len(results)} groupes OK")
        return 0
    print(f"✗ {results.count(False)}/{len(results)} groupe(s) en échec")
    return 1


if __name__ == "__main__":
    sys.exit(main())
