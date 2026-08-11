"""Export d'un cocon vers WordPress (chantier 9).

Le pipeline produit du markdown truffé de marqueurs `[[INTERNAL_LINK:slug|ancre]]`.
Publier, c'est les résoudre en vraies URL — et c'est là qu'est toute la
difficulté, pas dans l'appel HTTP.

**L'URL d'un article n'existe qu'une fois l'article créé.** Publier la mère en
premier ne sert à rien : elle pointe vers cinq filles qui n'existent pas encore.
Publier les filles d'abord ne sert pas davantage : chacune pointe vers la mère
*et* vers ses quatre sœurs. Aucun ordre ne résout le problème, parce que le
graphe du cocon est **cyclique par construction** — c'est même sa définition.

D'où deux passes, systématiquement :

  1. créer (ou retrouver) les six articles, ancres en texte brut ;
  2. relire la correspondance slug → URL réelle et réécrire les six contenus.

Conséquence pour le chantier 10 : **le backfill de liens n'est pas une
contrepartie de l'étalement dans le temps, il est obligatoire même en publiant
tout d'un coup.** Un calendrier de publication ne fait que rendre nécessaire
une troisième passe, il n'introduit pas le problème.

L'export est **rejouable** : on cherche d'abord un article portant le slug, et
on le met à jour au lieu d'en créer un second. Rien n'est stocké chez nous pour
cela — la correspondance vit dans WordPress, qui est la source de vérité de ce
qui y est publié.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

import markdown as md

from clients.wordpress_client import WordPressClient, WordPressError
from models import (
    ArticleBrief,
    ArticleType,
    CoconStructure,
    ExportedPost,
    GeneratedArticle,
    PipelineResult,
    WordPressExportReport,
)

logger = logging.getLogger(__name__)

_LINK_MARKER = re.compile(r"\[\[INTERNAL_LINK:([^|\]]+)\|([^\]]+)\]\]")

# `extra` apporte les tableaux (les articles en contiennent souvent) et les
# listes de définition ; `sane_lists` évite qu'une liste collée à un paragraphe
# avale le paragraphe.
_MD_EXTENSIONS = ["extra", "sane_lists"]


def markdown_to_html(text: str, links: dict[str, str] | None = None) -> str:
    """Convertit le markdown en HTML, marqueurs de maillage résolus.

    Un slug absent de `links` devient du texte brut plutôt qu'un lien mort :
    livrer un `<a href="">` cassé au client d'une agence est pire que livrer une
    phrase sans lien, parce que personne ne le remarque avant l'audit.
    """
    resolved = links or {}

    def _replace(match: re.Match) -> str:
        slug, anchor = match.group(1).strip(), match.group(2).strip()
        url = resolved.get(slug)
        if not url:
            return anchor
        return f'<a href="{html.escape(url, quote=True)}">{anchor}</a>'

    return md.markdown(_LINK_MARKER.sub(_replace, text or ""), extensions=_MD_EXTENSIONS)


def brief_to_html(brief: ArticleBrief, links: dict[str, str] | None = None) -> str:
    """Rend un brief en squelette d'article publiable.

    Le Mode Brief ne produit pas de texte rédigé : on publie donc un plan — les
    H2 attendus, les points à couvrir, les questions de la FAQ et le plan de
    maillage. C'est ce que le rédacteur de l'agence ouvre dans WordPress pour
    écrire par-dessus, plutôt que de recopier un JSON à la main.
    """
    parts: list[str] = [
        "<!-- Brief éditorial généré — à remplacer par l'article rédigé. -->",
        f"<p><em>Angle : {html.escape(brief.unique_angle)}</em></p>",
    ]
    if brief.tone_guidance:
        parts.append(f"<p><em>Ton : {html.escape(brief.tone_guidance)}</em></p>")

    for section in brief.sections:
        parts.append(f"<h2>{html.escape(section.h2)}</h2>")
        for h3 in section.h3s:
            parts.append(f"<h3>{html.escape(h3)}</h3>")
        if section.key_points:
            points = "".join(f"<li>{html.escape(p)}</li>" for p in section.key_points)
            parts.append(f"<ul>{points}</ul>")
        if section.word_count_target:
            parts.append(
                f"<p><em>≈ {section.word_count_target} mots.</em></p>"
            )

    if brief.faq_questions:
        parts.append("<h2>FAQ</h2><ul>")
        parts += [f"<li>{html.escape(q)}</li>" for q in brief.faq_questions]
        parts.append("</ul>")

    if brief.internal_links_plan:
        parts.append("<h2>Maillage interne à poser</h2><ul>")
        for link in brief.internal_links_plan:
            url = (links or {}).get(link.target_slug)
            ancre = html.escape(link.anchor_text)
            cible = (
                f'<a href="{html.escape(url, quote=True)}">{ancre}</a>' if url else ancre
            )
            parts.append(
                f"<li>{cible} → <code>/{html.escape(link.target_slug)}</code>"
                f" <em>({html.escape(link.context)})</em></li>"
            )
        parts.append("</ul>")

    if brief.editorial_notes:
        parts.append(
            f"<h2>Notes au rédacteur</h2><p>{html.escape(brief.editorial_notes)}</p>"
        )
    return "\n".join(parts)


def _payload(
    stub: Any, content_html: str, status: str, *, with_slug: bool
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": stub.h1_title,
        "content": content_html,
        "status": status,
        # `excerpt` est le seul champ de méta natif de WordPress. La meta
        # description SEO appartient à Yoast / Rank Math, qui exposent des
        # champs propres : on ne peut pas la poser sans savoir lequel est
        # installé, donc on la met là où elle est au moins lisible.
        "excerpt": stub.meta_description,
    }
    if with_slug:
        payload["slug"] = stub.slug
    return payload


def _items_of(result: PipelineResult, cocon: CoconStructure) -> list[tuple[Any, Any]]:
    """(stub, brief|article) pour chaque page du cocon, dans l'ordre mère → filles."""
    briefs = {b.stub.slug: b for b in result.briefs}
    articles = {a.stub.slug: a for a in result.articles}
    out: list[tuple[Any, Any]] = []
    for stub in [cocon.mother, *cocon.daughters]:
        item = articles.get(stub.slug) or briefs.get(stub.slug)
        if item is not None:
            out.append((stub, item))
    return out


def _render(item: Any, links: dict[str, str]) -> str:
    if isinstance(item, GeneratedArticle):
        return markdown_to_html(item.content_markdown, links)
    return brief_to_html(item, links)


async def export_cocoons_to_wordpress(
    result: PipelineResult,
    *,
    client: WordPressClient,
    cocon_ids: list[str] | None = None,
    status: str = "draft",
) -> WordPressExportReport:
    """Publie les articles demandés et rend le compte-rendu par page.

    `status` vaut `draft` par défaut : mettre en ligne six articles d'un coup
    sur le site d'un client est une décision d'agence, pas un défaut d'outil.
    """
    cocoons = [
        c for c in result.cocoons if cocon_ids is None or c.id in cocon_ids
    ]
    if not cocoons:
        raise WordPressError("Aucun cocon à exporter dans ce run.")

    account = await client.check_connection()
    logger.info(
        "Export WordPress vers %s — compte « %s », %d cocon(s)",
        client.site_url,
        account.get("name") or account.get("slug"),
        len(cocoons),
    )

    posts: list[ExportedPost] = []
    links: dict[str, str] = {}
    errors: list[str] = []

    # ---- Passe 1 : créer ou retrouver, sans les liens internes ----
    pending: list[tuple[Any, Any, ExportedPost]] = []
    for cocon in cocoons:
        for stub, item in _items_of(result, cocon):
            try:
                existing = await client.find_post_by_slug(stub.slug)
                body = _render(item, {})
                if existing:
                    data = await client.update_post(
                        int(existing["id"]),
                        _payload(stub, body, status, with_slug=False),
                    )
                    created = False
                else:
                    data = await client.create_post(
                        _payload(stub, body, status, with_slug=True)
                    )
                    created = True

                post = ExportedPost(
                    slug=stub.slug,
                    title=stub.h1_title,
                    post_id=int(data["id"]),
                    url=data.get("link") or "",
                    status=data.get("status") or status,
                    created=created,
                    is_mother=stub.article_type == ArticleType.MOTHER,
                )
                posts.append(post)
                # `link` est l'URL réelle telle que WordPress la calcule, une
                # fois sa structure de permaliens appliquée. On ne la devine pas.
                if post.url:
                    links[stub.slug] = post.url
                pending.append((stub, item, post))
            except WordPressError as e:
                errors.append(f"{stub.slug} : {e}")
                logger.error("Export de %s en échec : %s", stub.slug, e)

    # ---- Passe 2 : réécrire les contenus avec les vraies URL ----
    # Indispensable même quand tout est publié d'un bloc : le graphe du cocon
    # est cyclique, aucun ordre de création ne peut résoudre les liens seul.
    relinked = 0
    for stub, item, post in pending:
        try:
            body = _render(item, links)
            await client.update_post(post.post_id, {"content": body})
            post.linked = True
            relinked += 1
        except WordPressError as e:
            errors.append(f"{stub.slug} (maillage) : {e}")
            logger.error("Maillage de %s non posé : %s", stub.slug, e)

    report = WordPressExportReport(
        site_url=client.site_url,
        account=str(account.get("name") or account.get("slug") or ""),
        status=status,
        posts=posts,
        internal_links_resolved=relinked * 5,
        errors=errors,
    )
    logger.info(
        "Export terminé — %d article(s), %d créé(s), %d maillé(s), %d erreur(s)",
        len(posts),
        sum(1 for p in posts if p.created),
        relinked,
        len(errors),
    )
    return report
