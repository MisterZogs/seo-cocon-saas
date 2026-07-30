"""Smoke test : KW research + cocon builder sur un exemple concret.

Usage :
    cd backend
    python -m tests.test_pipeline_smoke

Nécessite ANTHROPIC_API_KEY dans .env (DataForSEO en mock si absent).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Charge .env depuis backend/
load_dotenv(Path(__file__).parent.parent / ".env")

from clients.anthropic_client import AnthropicClient  # noqa: E402
from clients.dataforseo_client import DataForSEOClient  # noqa: E402
from models import ClientForm  # noqa: E402
from pipeline.cocon_builder import CoconBuilder  # noqa: E402
from pipeline.keyword_research import KeywordResearcher  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)


# ============================================================
# EXEMPLE : agence SEO qui a un client Wall of Traders-like
# ============================================================

SAMPLE_FORM = ClientForm(
    product="Wall of Traders",
    description=(
        "Plateforme SaaS pour traders débutants et intermédiaires : copy-trading, "
        "signaux automatisés, formation trading crypto et forex, communauté active."
    ),
    language="fr",
    seed_keywords=[
        "copy trading crypto",
        "signaux trading",
        "trader débutant",
        "trading automatique",
    ],
    audience=(
        "Particuliers 25-45 ans intéressés par le trading crypto/forex, "
        "revenu moyen à élevé, cherchent à générer un complément de revenus "
        "sans avoir à devenir eux-mêmes des experts techniques."
    ),
    niche="Trading crypto et copy-trading pour particuliers",
    num_cocoons=2,
)


def _pretty_json(obj) -> str:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


async def main() -> None:
    print("=" * 70)
    print("SMOKE TEST : Keyword Research + Cocon Building")
    print("=" * 70)

    anthropic = AnthropicClient()
    dataforseo = DataForSEOClient()
    print(f"\n▶ DataForSEO mode: {'MOCK' if dataforseo.is_mock else 'RÉEL'}")

    researcher = KeywordResearcher(anthropic, dataforseo)
    builder = CoconBuilder()

    print("\n▶ Lancement KW research (peut prendre 30-60s)...")
    keywords, cocoon_proposals = await researcher.research(SAMPLE_FORM)

    print(f"\n✓ {len(keywords)} mots-clés enrichis")
    print(f"✓ {len(cocoon_proposals)} propositions de cocons")

    # Aperçu top 10 KW
    print("\n▶ Top 10 KW par volume :")
    top10 = sorted(keywords, key=lambda k: k.monthly_volume or 0, reverse=True)[:10]
    for k in top10:
        print(
            f"  · {k.keyword:<45} vol={k.monthly_volume:>6} "
            f"cpc={k.cpc:>5}€ comp={k.competition_score:.2f} "
            f"intent={k.intent.value}"
        )

    # Construction cocons
    print("\n▶ Construction des CoconStructure...")
    cocoons = builder.build(cocoon_proposals)
    print(f"✓ {len(cocoons)} cocons construits")

    for i, cocon in enumerate(cocoons, 1):
        print(f"\n{'─' * 70}")
        print(f"COCON {i} : {cocon.theme}")
        print(f"  KW principal: {cocon.main_keyword}")
        print(f"  Rationale:    {cocon.rationale[:150]}...")
        print(f"\n  MÈRE:")
        print(f"    • H1:    {cocon.mother.h1_title}")
        print(f"    • Slug:  /{cocon.mother.slug}")
        print(f"    • Meta:  {cocon.mother.meta_description}")
        print(f"    • KW:    {cocon.mother.target_keyword}")
        print(f"\n  FILLES ({len(cocon.daughters)}):")
        for j, d in enumerate(cocon.daughters, 1):
            print(f"    {j}. {d.h1_title}")
            print(f"       └─ /{d.slug} | KW: {d.target_keyword}")

    print("\n" + "=" * 70)
    print("✓ Smoke test terminé avec succès")
    print("=" * 70)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
