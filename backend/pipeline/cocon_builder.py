"""Étape 3 du pipeline : Design des cocons.

Transforme les propositions brutes du LLM (sortie de keyword_research) en objets
`CoconStructure` validés avec ArticleStub complets pour chaque mère et fille.
"""

from __future__ import annotations

import logging
import uuid

from slugify import slugify

from models import (
    ArticleStub,
    ArticleType,
    CoconStructure,
    SearchIntent,
)

logger = logging.getLogger(__name__)


class CoconBuilder:
    """Construit les CoconStructure depuis les propositions LLM."""

    def build(self, raw_proposals: list[dict]) -> list[CoconStructure]:
        cocoons: list[CoconStructure] = []
        for proposal in raw_proposals:
            try:
                cocoons.append(self._build_one(proposal))
            except Exception as e:
                logger.error(
                    "Cocon proposal invalide, ignoré: %s (raison: %s)",
                    proposal.get("theme"),
                    e,
                )
        logger.info("Cocons construits: %d", len(cocoons))
        return cocoons

    def _build_one(self, proposal: dict) -> CoconStructure:
        cocon_id = f"cocon_{uuid.uuid4().hex[:8]}"
        theme = proposal["theme"]
        main_keyword = proposal["main_keyword"]
        rationale = proposal.get("rationale", "")

        mother = self._build_stub(
            cocon_id=cocon_id,
            article_type=ArticleType.MOTHER,
            data=proposal["mother"],
        )

        daughters = [
            self._build_stub(
                cocon_id=cocon_id,
                article_type=ArticleType.DAUGHTER,
                data=d,
            )
            for d in proposal.get("daughters", [])
        ]

        if len(daughters) < 3:
            raise ValueError(f"Cocon {cocon_id} n'a que {len(daughters)} filles (min 3)")

        return CoconStructure(
            id=cocon_id,
            theme=theme,
            main_keyword=main_keyword,
            mother=mother,
            daughters=daughters,
            rationale=rationale,
        )

    @staticmethod
    def _build_stub(
        cocon_id: str, article_type: ArticleType, data: dict
    ) -> ArticleStub:
        target_kw = data["target_keyword"].strip()
        raw_slug = data.get("slug") or target_kw
        slug = slugify(raw_slug, max_length=80)

        h1 = data["h1_title"].strip()
        meta_title = data.get("meta_title", h1)[:70]
        meta_description = data.get("meta_description", "")[:160]
        intent_str = data.get("intent", "informational")

        try:
            intent = SearchIntent(intent_str)
        except ValueError:
            intent = SearchIntent.INFORMATIONAL

        return ArticleStub(
            cocon_id=cocon_id,
            article_type=article_type,
            target_keyword=target_kw,
            secondary_keywords=data.get("secondary_keywords", []),
            h1_title=h1,
            meta_title=meta_title,
            meta_description=meta_description,
            slug=slug,
            intent=intent,
        )
