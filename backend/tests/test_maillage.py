"""Vérifie la mise en conformité du maillage.

Le LLM propose des liens incomplets et arbitraires ; ce test part exactement
de ce qu'un run réel avait produit (maillage transversal à 60 %, une page sans
aucun lien entrant de ses sœurs, liens inter-cocons entre filles) et vérifie
que la normalisation redresse tout.

Usage :
    cd backend && .venv/bin/python -m tests.test_maillage
"""

from __future__ import annotations

import sys

from models import (
    ArticleStub,
    ArticleType,
    CoconStructure,
    GeneratedArticle,
    InterCoconPolicy,
    InternalLink,
    InternalLinkType,
    SearchIntent,
    SerpAnalysis,
)
from pipeline.maillage import assemble_maillage, audit_maillage


def _stub(cocon_id: str, slug: str, kind: ArticleType) -> ArticleStub:
    return ArticleStub(
        cocon_id=cocon_id,
        article_type=kind,
        target_keyword=slug.replace("-", " "),
        h1_title=f"H1 {slug}",
        meta_title=f"Meta {slug}"[:70],
        meta_description=f"Description de {slug}."[:160],
        slug=slug,
        intent=SearchIntent.INFORMATIONAL,
    )


def _cocon(cid: str, prefix: str) -> CoconStructure:
    return CoconStructure(
        id=cid,
        theme=f"Thème {prefix}",
        main_keyword=f"{prefix} mere",
        mother=_stub(cid, f"{prefix}-mere", ArticleType.MOTHER),
        daughters=[
            _stub(cid, f"{prefix}-fille-{i}", ArticleType.DAUGHTER) for i in range(1, 6)
        ],
        rationale="Test",
    )


_SERP = SerpAnalysis(
    keyword="k", scraped_pages_count=0, avg_word_count=1500,
    recommended_word_count=1800, avg_h2_count=6, recommended_h2_count=6,
    avg_h3_count=4, key_entities=[], key_topics=[], common_questions=[],
    competitive_angle="angle", top_result_format="guide",
)


def _article(stub: ArticleStub, links: list[tuple[str, str]]) -> GeneratedArticle:
    """`links` = [(slug cible, ancre)] — le markdown porte les marqueurs."""
    body = "# Titre\n\nIntro.\n\n" + "\n".join(
        f"Texte avec [[INTERNAL_LINK:{slug}|{anchor}]] au milieu." for slug, anchor in links
    )
    return GeneratedArticle(
        stub=stub,
        serp_analysis=_SERP,
        sections=[],
        faq=[],
        internal_links=[
            InternalLink(
                anchor_text=anchor,
                target_slug=slug,
                target_h1=f"H1 {slug}",
                link_type=InternalLinkType.SISTER_TO_SISTER,
                context="ctx",
                justification="Lien cross-cocon recommandé : ...",
            )
            for slug, anchor in links
        ],
        external_links=[],
        schema_jsonld={},
        content_markdown=body,
        word_count=500,
    )


def _build_case():
    """Deux cocons, maillage volontairement bancal comme dans le run réel."""
    a, b = _cocon("cocon_a", "a"), _cocon("cocon_b", "b")

    articles = [
        # mère A : n'annonce que 3 de ses 5 filles, + 1 lien vers la mère de B
        _article(a.mother, [("a-fille-1", "fille 1"), ("a-fille-2", "fille 2"),
                            ("a-fille-3", "fille 3"), ("b-mere", "b mere")]),
        # fille 1 : mère + 1 sœur + 1 lien inter-cocon vers une FILLE de B
        _article(a.daughters[0], [("a-mere", "a mere"), ("a-fille-2", "fille 2"),
                                  ("b-fille-1", "b fille 1")]),
        # fille 2 : mère seulement
        _article(a.daughters[1], [("a-mere", "a mere")]),
        # fille 3 : mère + doublon + auto-lien + cible inexistante
        _article(a.daughters[2], [("a-mere", "a mere"), ("a-mere", "doublon"),
                                  ("a-fille-3", "moi-meme"), ("slug-fantome", "cassé")]),
        # fille 4 : rien du tout
        _article(a.daughters[3], []),
        # fille 5 : lien inter-cocon vers la MÈRE de B
        _article(a.daughters[4], [("b-mere", "b mere")]),
    ]
    for d in b.daughters:
        articles.append(_article(d, [("b-mere", "b mere")]))
    articles.append(_article(b.mother, [(f"b-fille-{i}", f"b fille {i}") for i in range(1, 6)]))
    return [a, b], articles


def _check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}{(' — ' + detail) if detail and not cond else ''}")
    return cond


def main() -> int:
    ok = True

    # Le fixture contient 3 liens inter-cocons : a-mere→b-mere (mère↔mère),
    # a-fille-1→b-fille-1 et a-fille-5→b-mere (les deux partant d'une fille).
    for policy, expect_cross in [
        (InterCoconPolicy.STRICT, 0),        # étanchéité totale
        (InterCoconPolicy.MOTHERS_ONLY, 1),  # seul a-mere→b-mere survit
        (InterCoconPolicy.LIBRE, 3),         # tout est conservé
    ]:
        cocoons, articles = _build_case()
        print(f"\n=== politique inter-cocons : {policy.value} ===")
        m = assemble_maillage(
            briefs=[], articles=articles, cocoons=cocoons, policy=policy
        )
        report = audit_maillage(m, cocoons)

        ok &= _check(
            "maillage intra-cocon complet",
            not report["missing_required"],
            f"manquants: {report['missing_required'][:4]}",
        )
        ok &= _check("aucun orphelin", not report["orphans"], str(report["orphans"]))
        ok &= _check(
            f"liens inter-cocons = {expect_cross}",
            report["cross_cocon_count"] == expect_cross,
            f"obtenu {report['cross_cocon_count']}",
        )

        # chaque fille doit recevoir 4 liens de ses sœurs + 1 de la mère
        for c in cocoons:
            for d in c.daughters:
                n = report["inbound"][d.slug]
                ok &= _check(
                    f"{d.slug} reçoit ≥5 liens intra-cocon",
                    n >= 5,
                    f"seulement {n}",
                )

        # cohérence corps ↔ liste de liens
        for art in articles:
            declared = {l.target_slug for l in art.internal_links}
            in_body = set(
                __import__("re").findall(
                    r"\[\[INTERNAL_LINK:([^|\]]+)\|", art.content_markdown
                )
            )
            ok &= _check(
                f"{art.stub.slug} : corps == liste de liens",
                declared == in_body,
                f"liste-corps={declared - in_body} corps-liste={in_body - declared}",
            )
            ok &= _check(
                f"{art.stub.slug} : aucun marqueur vers slug inexistant",
                "slug-fantome" not in art.content_markdown,
            )

    print("\n" + "=" * 60)
    print("TOUS LES CONTRÔLES PASSENT" if ok else "ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
