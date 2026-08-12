"""Étape 6 du pipeline : assemblage et mise en conformité du maillage interne.

Le LLM propose des liens ; ce module les rend conformes. C'est un choix
délibéré : les règles de maillage sont déterministes, donc mieux vaut les faire
respecter en code que d'espérer que le modèle obéisse au prompt. Sur un run
mesuré, le LLM ne produisait que 27 des 40 liens transversaux attendus, sans
réciprocité, et laissait une page sans aucun lien entrant de ses sœurs.

Règles appliquées (méthode du cocon sémantique) :

  Fille → Mère           systématique
  Mère → toutes ses filles   systématique
  Sœur ↔ Sœur            systématique et réciproque — le maillage transversal
                         fait partie de la méthode, il n'est pas optionnel
  Inter-cocons           régi par InterCoconPolicy, strict par défaut :
                         l'étanchéité entre silos est le principe central, et
                         les cocons sont déjà reliés par le haut de l'arbre

En mode FULL, le corps des articles porte des marqueurs
`[[INTERNAL_LINK:slug|ancre]]`. Toute modification de la liste de liens est
donc répercutée dans le markdown, sinon l'export produirait des marqueurs
orphelins ou perdrait des liens.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from models import (
    ArticleBrief,
    ArticleType,
    CoconStructure,
    GeneratedArticle,
    InterCoconLink,
    InterCoconPolicy,
    InternalLink,
    InternalLinkType,
    MaillageMap,
)

logger = logging.getLogger(__name__)

_LINK_MARKER = re.compile(r"\[\[INTERNAL_LINK:([^|\]]+)\|([^\]]+)\]\]")

_RELATED_HEADING = "Sur le même sujet"


@dataclass
class MaillageAudit:
    """Ce que la normalisation a dû corriger — utile au livrable et aux logs."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{len(self.added)} lien(s) ajouté(s), {len(self.removed)} retiré(s), "
            f"{len(self.broken)} cible(s) inexistante(s) nettoyée(s)"
        )


class _Index:
    """Vues sur la structure des cocons, construites une fois."""

    def __init__(self, cocoons: list[CoconStructure]) -> None:
        self.cocon_of: dict[str, str] = {}
        self.mother_of: dict[str, str] = {}
        self.daughters_of: dict[str, list[str]] = {}
        self.stub_of: dict[str, object] = {}

        for cocon in cocoons:
            self.cocon_of[cocon.mother.slug] = cocon.id
            self.mother_of[cocon.id] = cocon.mother.slug
            self.stub_of[cocon.mother.slug] = cocon.mother
            self.daughters_of[cocon.id] = []
            for d in cocon.daughters:
                self.cocon_of[d.slug] = cocon.id
                self.stub_of[d.slug] = d
                self.daughters_of[cocon.id].append(d.slug)

    def is_mother(self, slug: str) -> bool:
        return self.mother_of.get(self.cocon_of.get(slug, ""), None) == slug

    def expected_targets(self, slug: str) -> list[str]:
        """Les liens intra-cocon obligatoires depuis cette page."""
        cid = self.cocon_of.get(slug)
        if cid is None:
            return []
        if self.is_mother(slug):
            return list(self.daughters_of[cid])
        # Fille : la mère + toutes ses sœurs
        return [self.mother_of[cid]] + [
            s for s in self.daughters_of[cid] if s != slug
        ]

    def link_type(self, source: str, target: str) -> InternalLinkType:
        if self.cocon_of.get(source) != self.cocon_of.get(target):
            return InternalLinkType.CROSS_COCON
        if self.is_mother(source):
            return InternalLinkType.MOTHER_TO_DAUGHTER
        if self.is_mother(target):
            return InternalLinkType.DAUGHTER_TO_MOTHER
        return InternalLinkType.SISTER_TO_SISTER


def _anchor_for(index: _Index, target: str, used: set[str]) -> str:
    """Ancre d'un lien synthétisé : le KW cible, ou le H1 s'il est déjà pris.

    Évite que les quatre sœurs pointent vers la même page avec exactement la
    même chaîne, sans pour autant inventer de formulation hasardeuse.
    """
    stub = index.stub_of.get(target)
    if stub is None:
        return target.replace("-", " ")
    primary = stub.target_keyword
    if primary.lower() not in used:
        return primary
    return stub.h1_title


def _keep_cross_link(index: _Index, source: str, target: str, policy: InterCoconPolicy) -> bool:
    if policy is InterCoconPolicy.LIBRE:
        return True
    if policy is InterCoconPolicy.STRICT:
        return False
    # MOTHERS_ONLY : les deux extrémités doivent être des mères
    return index.is_mother(source) and index.is_mother(target)


def _normalize_links(
    *,
    index: _Index,
    source: str,
    links: list[InternalLink],
    policy: InterCoconPolicy,
    audit: MaillageAudit,
) -> tuple[list[InternalLink], list[InternalLink]]:
    """Retourne (liens conformes, liens ajoutés) pour une page.

    Les liens ajoutés sont retournés à part : en mode FULL ils doivent être
    injectés dans le markdown, qui ne les contient pas.
    """
    kept: list[InternalLink] = []
    seen: set[str] = set()

    for link in links:
        target = link.target_slug

        if target == source:
            audit.removed.append(f"{source} → lui-même")
            continue
        if target not in index.cocon_of:
            audit.broken.append(f"{source} → {target}")
            continue
        if target in seen:
            audit.removed.append(f"{source} → {target} (doublon)")
            continue

        is_cross = index.cocon_of[source] != index.cocon_of[target]
        if is_cross and not _keep_cross_link(index, source, target, policy):
            audit.removed.append(f"{source} → {target} (inter-cocon, politique {policy.value})")
            continue

        link.link_type = index.link_type(source, target)
        kept.append(link)
        seen.add(target)

    # Complète le maillage intra-cocon obligatoire
    added: list[InternalLink] = []
    used_anchors = {l.anchor_text.lower() for l in kept}
    for target in index.expected_targets(source):
        if target in seen:
            continue
        anchor = _anchor_for(index, target, used_anchors)
        used_anchors.add(anchor.lower())
        stub = index.stub_of[target]
        new_link = InternalLink(
            anchor_text=anchor,
            target_slug=target,
            target_h1=getattr(stub, "h1_title", target),
            link_type=index.link_type(source, target),
            context=f"Section « {_RELATED_HEADING} »",
            justification=(
                "Lien structurel du cocon ajouté à la normalisation : le maillage "
                "transversal doit être complet et réciproque."
            ),
        )
        kept.append(new_link)
        added.append(new_link)
        seen.add(target)
        audit.added.append(f"{source} → {target}")

    return kept, added


def _rewrite_markdown(
    markdown: str, kept: list[InternalLink], added: list[InternalLink]
) -> str:
    """Aligne le corps de l'article sur la liste de liens définitive.

    - marqueur d'un lien retiré → remplacé par son texte d'ancre, la phrase
      reste lisible
    - liens ajoutés → regroupés dans une section de fin, faute de pouvoir les
      insérer naturellement dans une prose déjà rédigée
    - 🔴 lien **déclaré mais jamais placé** dans le corps → traité exactement
      comme un lien ajouté

    Ce dernier cas vient d'une panne réelle (run de prod du 2026-08-12) : la
    mère avait déclaré ses 5 liens dans `internal_links`, avec les bonnes cibles
    et de bonnes ancres, mais n'avait posé **aucun** marqueur dans le markdown.
    Rien n'était donc à *ajouter*, la section de repli ne se déclenchait pas, et
    le trou passait tous les contrôles : la map annonçait 5 liens sortants et
    l'audit ne voyait ni manque ni orphelin, puisque tous deux raisonnent sur la
    liste `internal_links`, pas sur le corps.

    Or l'export WordPress dérive les liens des **marqueurs du corps** et de rien
    d'autre. La mère serait partie chez le client sans un seul lien vers ses
    filles — en contradiction directe avec la seule promesse vérifiable du
    produit. D'où la vérification ici : la liste de liens ne fait foi que si le
    corps la porte.
    """
    kept_targets = {l.target_slug for l in kept}

    def _replace(match: re.Match) -> str:
        slug, anchor = match.group(1).strip(), match.group(2).strip()
        return match.group(0) if slug in kept_targets else anchor

    body = _LINK_MARKER.sub(_replace, markdown)

    # Les liens `added` sont eux aussi absents du corps — c'est leur définition,
    # ils sont sur le point d'y être écrits. Les exclure, sinon chacun serait
    # ajouté deux fois à la section de fin.
    present = {m.group(1).strip() for m in _LINK_MARKER.finditer(body)}
    present |= {l.target_slug for l in added}
    declared_but_absent = [l for l in kept if l.target_slug not in present]
    if declared_but_absent:
        logger.warning(
            "Liens déclarés mais absents du corps, rattrapés en fin d'article : %s",
            ", ".join(l.target_slug for l in declared_but_absent),
        )

    to_append = [*added, *declared_but_absent]
    if to_append:
        lines = [f"\n\n## {_RELATED_HEADING}\n"]
        lines += [
            f"- [[INTERNAL_LINK:{l.target_slug}|{l.anchor_text}]]" for l in to_append
        ]
        body = body.rstrip() + "\n".join(lines) + "\n"

    return body


def build_maillage_from_structure(
    cocoons: list[CoconStructure],
    policy: InterCoconPolicy = InterCoconPolicy.STRICT,
) -> MaillageMap:
    """Le maillage qu'un cocon **doit** porter, déduit de sa seule structure.

    Aucun contenu, aucun LLM : les règles du cocon sont entièrement déterminées
    par la place de chaque page dans l'arbre. C'est la définition même de la
    méthode, et c'est ce qui rend la promesse vérifiable — le maillage n'est pas
    une sortie de modèle qu'on espère correcte, c'est une conséquence de la
    structure.

    Passe par `_normalize_links` avec une liste vide plutôt que de reconstruire
    la logique : ce qu'on obtient est exactement ce que la normalisation ajoute
    quand le modèle n'a rien produit. Le générateur public (`free_preview.py`)
    en dépend, et il doit montrer le vrai code, pas une imitation.
    """
    index = _Index(cocoons)
    audit = MaillageAudit()
    links_by_slug: dict[str, list[InternalLink]] = {}

    for cocon in cocoons:
        for stub in [cocon.mother, *cocon.daughters]:
            kept, _ = _normalize_links(
                index=index, source=stub.slug, links=[], policy=policy, audit=audit
            )
            links_by_slug[stub.slug] = kept

    # `expected_targets` ne rend que de l'intra-cocon : par construction, un
    # maillage déduit de la structure ne franchit jamais un silo.
    return MaillageMap(links=links_by_slug, inter_cocon_links=[])


def assemble_maillage(
    *,
    briefs: list[ArticleBrief],
    articles: list[GeneratedArticle],
    cocoons: list[CoconStructure],
    policy: InterCoconPolicy = InterCoconPolicy.STRICT,
) -> MaillageMap:
    """Met le maillage en conformité et retourne la map complète.

    Les objets `briefs` / `articles` sont modifiés sur place pour que le
    livrable reste cohérent avec la map.
    """
    index = _Index(cocoons)
    audit = MaillageAudit()

    links_by_slug: dict[str, list[InternalLink]] = {}
    inter_cocon: list[InterCoconLink] = []

    def _register(source: str, links: list[InternalLink]) -> None:
        links_by_slug[source] = links
        for link in links:
            if link.link_type is InternalLinkType.CROSS_COCON:
                inter_cocon.append(
                    InterCoconLink(
                        from_cocon_id=index.cocon_of[source],
                        from_slug=source,
                        to_cocon_id=index.cocon_of[link.target_slug],
                        to_slug=link.target_slug,
                        anchor_text=link.anchor_text,
                        justification=link.justification,
                    )
                )

    for brief in briefs:
        source = brief.stub.slug
        kept, _ = _normalize_links(
            index=index,
            source=source,
            links=brief.internal_links_plan,
            policy=policy,
            audit=audit,
        )
        brief.internal_links_plan = kept
        _register(source, kept)

    for article in articles:
        source = article.stub.slug
        kept, added = _normalize_links(
            index=index,
            source=source,
            links=article.internal_links,
            policy=policy,
            audit=audit,
        )
        article.internal_links = kept
        article.content_markdown = _rewrite_markdown(
            article.content_markdown, kept, added
        )
        _register(source, kept)

    logger.info(
        "Maillage normalisé (politique inter-cocons: %s) — %s",
        policy.value,
        audit.summary(),
    )
    if audit.broken:
        logger.warning("Cibles inexistantes nettoyées : %s", ", ".join(audit.broken))

    return MaillageMap(links=links_by_slug, inter_cocon_links=inter_cocon)


def audit_maillage(
    maillage: MaillageMap, cocoons: list[CoconStructure]
) -> dict[str, object]:
    """Contrôle a posteriori — sert aux tests et au diagnostic."""
    index = _Index(cocoons)
    targets_by_source = {
        s: {l.target_slug for l in links} for s, links in maillage.links.items()
    }
    missing: list[str] = []
    for source in index.cocon_of:
        expected = set(index.expected_targets(source))
        got = targets_by_source.get(source, set())
        missing += [f"{source} → {t}" for t in sorted(expected - got)]

    inbound: dict[str, int] = defaultdict(int)
    for links in maillage.links.values():
        for link in links:
            inbound[link.target_slug] += 1

    return {
        "missing_required": missing,
        "cross_cocon_count": len(maillage.inter_cocon_links),
        "orphans": sorted(s for s in index.cocon_of if inbound[s] == 0),
        "inbound": dict(inbound),
    }
