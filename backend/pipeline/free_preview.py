"""Générateur public de structure de cocon — l'aimant à prospects (chantier 12).

Un mot-clé, aucune inscription, et en retour la **structure complète d'un cocon
avec sa map de maillage**. Rien n'est simulé : la structure passe par le même
`CoconBuilder` que le produit payant, et le maillage par le même
`assemble_maillage`. La démonstration ne vaut que si c'est le vrai code.

**Ce qu'on donne, et pourquoi.** On donne exactement ce qu'aucun concurrent ne
peut montrer : une promesse *vérifiable*. « Chaque page reçoit exactement 5
liens entrants, comptez-les » se démontre en dix secondes sur une page publique,
là où « contenu optimisé » et « maillage intelligent » ne se démontrent jamais.
Un visiteur repart avec la preuve arithmétique en main.

**Ce qu'on retient, et pourquoi.** Tout ce qui coûte de l'argent ou constitue le
travail facturé : volumes de recherche réels (DataForSEO), analyse du top 10,
briefs éditoriaux, articles rédigés, rapport backlinks. Le gratuit montre
l'*architecture* ; le payant produit le *contenu*. C'est aussi la ligne de
partage du positionnement — on ne vend pas des articles, on vend une structure.

Un seul appel Haiku, ~$0,01. Voir `ratelimit.py` pour ce qui borne la dépense.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from clients.anthropic_client import AnthropicClient
from models import CoconStructure, InterCoconPolicy, MaillageMap
from pipeline.cocon_builder import CoconBuilder
from pipeline.maillage import assemble_maillage, audit_maillage

logger = logging.getLogger(__name__)

DAUGHTERS = 5

_SYSTEM = """Tu es un consultant SEO français expert de la méthode du cocon
sémantique (Laurent Bourrelly).

On te donne un mot-clé principal. Tu produis la structure d'UN cocon :
- 1 article MÈRE qui traite le sujet principal en profondeur ;
- exactement {daughters} articles FILLES, chacun couvrant un sous-sujet distinct.

Règles impératives :
- Les filles ne se recouvrent pas. Deux filles qui traitent le même angle se
  cannibalisent : c'est l'erreur classique, ne la commets pas.
- Ensemble, les filles couvrent le champ sémantique de la mère. Chacune répond à
  une intention de recherche différente et identifiable.
- Les mots-clés sont en français, tels qu'une vraie personne les taperait.
- Aucun mot-clé n'apparaît deux fois dans le cocon.
- Les titres H1 sont rédigés, pas des étiquettes : ils donnent envie de lire.

Réponds en JSON strict :
{{
  "theme": "le thème du cocon en une phrase",
  "main_keyword": "<mot-clé de la mère>",
  "rationale": "pourquoi ce découpage tient en 1-2 phrases",
  "mother": {{
    "target_keyword": "...", "h1_title": "...", "meta_title": "max 70 car.",
    "meta_description": "max 155 car.", "slug": "en-minuscules-avec-tirets",
    "intent": "informational|commercial|transactional|navigational"
  }},
  "daughters": [ {{ … même forme … }} ]
}}"""


@dataclass
class CoconPreview:
    """Le livrable gratuit : une structure, sa map, et sa preuve arithmétique."""

    cocon: CoconStructure
    maillage: MaillageMap
    total_links: int
    inbound_per_page: dict[str, int]
    outbound_per_page: dict[str, int]
    orphans: list[str]
    cost_usd: float


def _proof(maillage: MaillageMap) -> tuple[int, dict[str, int], dict[str, int]]:
    """Compte les liens réellement présents dans la map.

    Recompté depuis la map plutôt qu'affirmé depuis la formule : une promesse
    vérifiable qu'on afficherait sans la vérifier serait exactement le travers
    qu'on reproche au reste du marché.
    """
    outbound = {slug: len(links) for slug, links in maillage.links.items()}
    inbound: dict[str, int] = {slug: 0 for slug in maillage.links}
    total = 0
    for links in maillage.links.values():
        for link in links:
            inbound[link.target_slug] = inbound.get(link.target_slug, 0) + 1
            total += 1
    return total, inbound, outbound


async def generate_preview(
    keyword: str,
    *,
    context: str | None = None,
    anthropic: AnthropicClient | None = None,
) -> CoconPreview:
    """Construit un cocon de démonstration à partir d'un seul mot-clé."""
    anthropic = anthropic or AnthropicClient()

    prompt = f"Mot-clé principal : « {keyword.strip()} »"
    if context and context.strip():
        prompt += f"\nContexte de l'activité : {context.strip()}"
    prompt += (
        f"\n\nProduis la structure du cocon : 1 mère et exactement {DAUGHTERS} filles."
    )

    parsed, _ = await anthropic.complete_json(
        model="haiku",
        system=_SYSTEM.format(daughters=DAUGHTERS),
        user_prompt=prompt,
        max_tokens=3000,
    )
    if not isinstance(parsed, dict):
        raise ValueError("Réponse inattendue du modèle.")

    cocoons = CoconBuilder().build([parsed])
    if not cocoons:
        # `CoconBuilder` avale les propositions invalides et rend une liste vide.
        # Sans ce contrôle, l'utilisateur verrait une page vide sans explication.
        raise ValueError(
            "La structure produite était incomplète. Reformulez le mot-clé — "
            "un sujet trop étroit ne se découpe pas en six articles distincts."
        )

    cocon = cocoons[0]
    maillage = assemble_maillage(
        briefs=[], articles=[], cocoons=[cocon], policy=InterCoconPolicy.STRICT
    )
    total, inbound, outbound = _proof(maillage)
    audit = audit_maillage(maillage, [cocon])

    logger.info(
        "Aperçu gratuit « %s » — %d articles, %d liens, %d orphelin(s), $%.4f",
        keyword,
        1 + len(cocon.daughters),
        total,
        len(audit["orphans"]),
        anthropic.usage.cost_usd,
    )

    return CoconPreview(
        cocon=cocon,
        maillage=maillage,
        total_links=total,
        inbound_per_page=inbound,
        outbound_per_page=outbound,
        orphans=list(audit["orphans"]),
        cost_usd=anthropic.usage.cost_usd,
    )
