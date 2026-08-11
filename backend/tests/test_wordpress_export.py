"""Export WordPress — chantier 9.

Le contrôle qui porte tout le reste est celui des **deux passes**.

Le graphe d'un cocon est cyclique par construction : la mère pointe vers ses
cinq filles, chaque fille pointe vers la mère et vers ses quatre sœurs. Aucun
ordre de création ne permet donc de résoudre les liens en une seule passe —
quel que soit l'article publié en premier, il pointe vers des pages qui
n'existent pas encore et dont l'URL est donc inconnue.

Le test vérifie qu'après export **chaque page contient les URL réelles de ses
cinq voisines**. En une seule passe, la première page publiée en aurait zéro.

Corollaire pour le chantier 10 : le backfill de liens n'est pas la contrepartie
d'un étalement dans le temps, il est **obligatoire même en publiant tout d'un
bloc**. Un calendrier ajoute une passe, il ne crée pas le problème.

Aucun appel réseau — WordPress est simulé en mémoire.

Usage :
    cd backend && .venv/bin/python -m tests.test_wordpress_export
"""

from __future__ import annotations

import asyncio
import re
import sys
from typing import Any

from clients.wordpress_client import WordPressError, normalize_site_url
from models import GenerationMode, InternalLink, InternalLinkType
from pipeline.wordpress_export import (
    brief_to_html,
    export_cocoons_to_wordpress,
    markdown_to_html,
)
from tests.test_regeneration import _finished_run


def _check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f" — {detail}" if not cond else ""))
    return bool(cond)


class FakeWordPress:
    """WordPress en mémoire — même interface que `WordPressClient`.

    Simule ce qui compte : l'URL d'un article n'existe qu'une fois l'article
    créé, et elle est calculée par WordPress (pas devinée par nous).
    """

    def __init__(self, fail_on: set[str] | None = None, existing: dict[str, int] | None = None) -> None:
        self.site_url = "https://client.fr/"
        self.posts: dict[int, dict[str, Any]] = {}
        self.by_slug: dict[str, int] = {}
        self._next_id = 100
        self.fail_on = fail_on or set()
        self.creates = 0
        self.updates = 0
        # Articles déjà présents dans le WordPress avant l'export.
        for slug, pid in (existing or {}).items():
            self.posts[pid] = {
                "id": pid,
                "slug": slug,
                "link": f"https://client.fr/{slug}/",
                "status": "publish",
                "content": "ancien contenu",
                "title": "ancien titre",
            }
            self.by_slug[slug] = pid

    async def check_connection(self) -> dict[str, Any]:
        return {"name": "Agence Test", "capabilities": {"publish_posts": True}}

    async def find_post_by_slug(self, slug: str) -> dict[str, Any] | None:
        pid = self.by_slug.get(slug)
        return self.posts.get(pid) if pid else None

    async def create_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        slug = payload.get("slug", "")
        if slug in self.fail_on:
            raise WordPressError(f"refus simulé sur {slug}")
        self.creates += 1
        pid = self._next_id
        self._next_id += 1
        # C'est WordPress qui décide de l'URL, selon sa structure de permaliens.
        post = {
            "id": pid,
            "slug": slug,
            "link": f"https://client.fr/blog/{slug}/",
            "status": payload.get("status", "draft"),
            "content": payload.get("content", ""),
            "title": payload.get("title", ""),
            "excerpt": payload.get("excerpt", ""),
        }
        self.posts[pid] = post
        self.by_slug[slug] = pid
        return post

    async def update_post(self, post_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        post = self.posts[post_id]
        if post["slug"] in self.fail_on:
            raise WordPressError(f"refus simulé sur {post['slug']}")
        self.updates += 1
        post.update(payload)
        return post


def _with_markers(result: Any) -> Any:
    """Pose des marqueurs de maillage dans les briefs, comme le fait le pipeline.

    Le fixture de `test_regeneration` produit des briefs vides ; ici on veut des
    liens à résoudre.
    """
    slugs = [b.stub.slug for b in result.briefs]
    for brief in result.briefs:
        brief.internal_links_plan = [
            InternalLink(
                anchor_text=f"vers {other}",
                target_slug=other,
                target_h1=other,
                link_type=InternalLinkType.SISTER_TO_SISTER,
                context="Section test",
                justification="test",
            )
            for other in slugs
            if other != brief.stub.slug
        ]
    return result


# ============================================================
# 1. Conversion markdown → HTML
# ============================================================


def test_rendu() -> bool:
    print("\n[1] Conversion markdown → HTML")
    ok = True

    html = markdown_to_html("## Titre\n\nUn **gras** et une liste :\n\n- a\n- b\n")
    ok &= _check("les titres deviennent des <h2>", "<h2>Titre</h2>" in html)
    ok &= _check("le gras est converti", "<strong>gras</strong>" in html)
    ok &= _check("les listes sont converties", "<li>a</li>" in html)

    table = markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |\n")
    ok &= _check("les tableaux sont convertis (extension `extra`)", "<table>" in table)

    lien = markdown_to_html(
        "Voir [[INTERNAL_LINK:fille-1|mon ancre]].", {"fille-1": "https://x.fr/f1/"}
    )
    ok &= _check(
        "un marqueur résolu devient un <a href>",
        '<a href="https://x.fr/f1/">mon ancre</a>' in lien,
        lien,
    )

    orphelin = markdown_to_html("Voir [[INTERNAL_LINK:inconnu|mon ancre]].", {})
    ok &= _check(
        "un marqueur non résolu devient du texte brut, pas un lien mort",
        "mon ancre" in orphelin and "<a " not in orphelin,
        orphelin,
    )

    echappe = markdown_to_html(
        'Voir [[INTERNAL_LINK:x|ancre]].', {"x": 'https://x.fr/?a=1&b="2"'}
    )
    ok &= _check("l'URL est échappée dans l'attribut", '&amp;b=&quot;2&quot;' in echappe, echappe)
    return ok


# ============================================================
# 2. Les deux passes — le cœur du chantier
# ============================================================


async def test_deux_passes() -> bool:
    print("\n[2] Deux passes : chaque page porte les URL réelles de ses voisines")
    ok = True

    result = _with_markers(_finished_run(mode=GenerationMode.BRIEF))
    wp = FakeWordPress()
    report = await export_cocoons_to_wordpress(result, client=wp, status="draft")

    ok &= _check("6 articles exportés", len(report.posts) == 6, str(len(report.posts)))
    ok &= _check("6 créations", wp.creates == 6, str(wp.creates))
    ok &= _check("aucune erreur", not report.errors, str(report.errors))
    ok &= _check("statut draft par défaut", all(p.status == "draft" for p in report.posts))
    ok &= _check("toutes les pages ont été re-maillées", all(p.linked for p in report.posts))

    # LE contrôle : chaque page contient les 5 URL de ses voisines. En une seule
    # passe, la première page publiée n'en aurait aucune.
    slugs = [p.slug for p in report.posts]
    manquants: list[str] = []
    for post in report.posts:
        contenu = wp.posts[post.post_id]["content"]
        for autre in slugs:
            if autre == post.slug:
                continue
            url = f"https://client.fr/blog/{autre}/"
            if f'href="{url}"' not in contenu:
                manquants.append(f"{post.slug} → {autre}")
    ok &= _check(
        "chaque page pointe vers ses 5 voisines par URL réelle",
        not manquants,
        f"{len(manquants)} lien(s) manquant(s) : {manquants[:4]}",
    )
    ok &= _check(
        "30 liens internes résolus au total",
        report.internal_links_resolved == 30,
        str(report.internal_links_resolved),
    )
    ok &= _check(
        "aucun marqueur brut ne subsiste dans le HTML publié",
        not any("[[INTERNAL_LINK" in p["content"] for p in wp.posts.values()),
    )

    # La mère est le cas qui démontre l'impossibilité de la passe unique :
    # publiée en premier, elle pointe vers cinq pages pas encore créées.
    mere = next(p for p in report.posts if p.is_mother)
    ok &= _check("la mère est bien exportée en premier", report.posts[0].slug == mere.slug)
    ok &= _check("… et porte quand même ses 5 liens", mere.internal_links == 5, str(mere.internal_links))
    return ok


# ============================================================
# 3. Rejouabilité, erreurs partielles, statut
# ============================================================


async def test_rejouable() -> bool:
    print("\n[3] Rejouabilité et échecs partiels")
    ok = True

    # Un article existe déjà dans le WordPress du client, avec ce slug.
    result = _with_markers(_finished_run(mode=GenerationMode.BRIEF))
    deja = result.briefs[2].stub.slug
    wp = FakeWordPress(existing={deja: 42})
    report = await export_cocoons_to_wordpress(result, client=wp)

    ok &= _check("5 créations, pas 6", wp.creates == 5, str(wp.creates))
    reutilise = next(p for p in report.posts if p.slug == deja)
    ok &= _check("l'article existant est mis à jour, pas dupliqué", not reutilise.created)
    ok &= _check("… et conserve son identifiant WordPress", reutilise.post_id == 42)
    ok &= _check("aucun doublon de slug", len({p.slug for p in report.posts}) == 6)

    # Un second export ne doit rien créer du tout.
    wp2 = FakeWordPress(existing={p.slug: p.post_id for p in report.posts})
    report2 = await export_cocoons_to_wordpress(result, client=wp2)
    ok &= _check("réexporter ne crée aucun article", wp2.creates == 0, str(wp2.creates))
    ok &= _check("… et remet quand même le maillage", report2.internal_links_resolved == 30)

    # Une page qui échoue ne doit pas emporter les cinq autres.
    result3 = _with_markers(_finished_run(mode=GenerationMode.BRIEF))
    casse = result3.briefs[3].stub.slug
    wp3 = FakeWordPress(fail_on={casse})
    report3 = await export_cocoons_to_wordpress(result3, client=wp3)
    ok &= _check("les 5 autres articles passent", len(report3.posts) == 5, str(len(report3.posts)))
    ok &= _check("l'échec est rapporté", any(casse in e for e in report3.errors), str(report3.errors))
    ok &= _check(
        "les liens vers la page manquante retombent en texte brut",
        all(
            f'href="https://client.fr/blog/{casse}/"' not in p["content"]
            for p in wp3.posts.values()
        ),
    )
    ok &= _check(
        "… et les autres liens sont quand même posés",
        report3.internal_links_resolved == 20,
        str(report3.internal_links_resolved),
    )

    # Statut explicite.
    result4 = _with_markers(_finished_run(mode=GenerationMode.BRIEF))
    wp4 = FakeWordPress()
    report4 = await export_cocoons_to_wordpress(result4, client=wp4, status="publish")
    ok &= _check("le statut demandé est appliqué", all(p.status == "publish" for p in report4.posts))
    return ok


# ============================================================
# 4. Mode FULL, briefs, et normalisation d'URL
# ============================================================


async def test_modes_et_urls() -> bool:
    print("\n[4] Mode FULL, squelette de brief, normalisation d'URL")
    ok = True

    full = _finished_run(mode=GenerationMode.FULL)
    # Le fixture FULL a un markdown sans marqueur : on en pose un vrai.
    autre = full.articles[1].stub.slug
    full.articles[0].content_markdown = (
        f"# Titre\n\nUn lien [[INTERNAL_LINK:{autre}|voir aussi]].\n"
    )
    wp = FakeWordPress()
    report = await export_cocoons_to_wordpress(full, client=wp)
    contenu = wp.posts[report.posts[0].post_id]["content"]
    ok &= _check(
        "mode FULL : le markdown de l'article est converti et maillé",
        f'<a href="https://client.fr/blog/{autre}/">voir aussi</a>' in contenu,
        contenu[:160],
    )

    brief = _with_markers(_finished_run(mode=GenerationMode.BRIEF)).briefs[0]
    html = brief_to_html(brief, {})
    ok &= _check("un brief produit un squelette publiable", "<h2>" in html or "Maillage" in html)
    ok &= _check("le brief est signalé comme tel", "Brief éditorial" in html)
    ok &= _check("le plan de maillage figure dans le squelette", "Maillage interne" in html)

    ok &= _check("URL nue → https + slash", normalize_site_url("client.fr") == "https://client.fr/")
    ok &= _check(
        "/wp-admin est retiré",
        normalize_site_url("https://client.fr/wp-admin") == "https://client.fr/",
    )
    ok &= _check(
        "un sous-dossier est conservé",
        normalize_site_url("https://client.fr/blog") == "https://client.fr/blog/",
    )
    for mauvais in ["", "   ", "ftp://client.fr"]:
        try:
            normalize_site_url(mauvais)
            ok &= _check(f"« {mauvais} » est refusé", False, "accepté")
        except WordPressError:
            ok &= _check(f"« {mauvais or 'vide'} » est refusé", True)
    return ok


# ============================================================


def main_() -> int:
    print("=" * 60)
    print("EXPORT WORDPRESS (chantier 9)")
    print("=" * 60)

    ok = test_rendu()
    ok &= asyncio.run(test_deux_passes())
    ok &= asyncio.run(test_rejouable())
    ok &= asyncio.run(test_modes_et_urls())

    print("\n" + "=" * 60)
    print("TOUS LES CONTRÔLES PASSENT" if ok else "ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_())
