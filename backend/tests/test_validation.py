"""Porte de validation humaine entre la recherche de mots-clés et la génération.

Ce qui est vérifié ici, dans l'ordre du flux réel :
1. le pipeline s'arrête bien AVANT de payer la moindre génération d'article
2. l'écran reçoit les justifications du modèle et le pool complet
3. la décision de l'agence est respectée au mot-clé près
4. le run relancé passe la porte au lieu de la refermer

Aucun appel réseau : Anthropic et DataForSEO sont simulés.

Usage :
    cd backend && .venv/bin/python -m tests.test_validation
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from models import (
    MAX_ARTICLES_PER_COCON,
    ClientForm,
    GenerationMode,
    KeywordWithData,
    PipelineStep,
    SearchIntent,
    ValidatedCocon,
    ValidationDecision,
)
from pipeline.cocon_builder import CoconBuilder
from pipeline.orchestrator import AwaitingValidation, run_pipeline
from pipeline.validation import apply_decision, build_snapshot
from tests.test_resume import FakeAnthropic, FakeDataForSEO, InMemoryStore

FORM = ClientForm(
    product="Désinsectisation",
    description="Destruction de nids de frelons et de guêpes.",
    seed_keywords=["nid de frelons"],
    audience="Particuliers",
    niche="Services à domicile",
    num_cocoons=1,
    mode=GenerationMode.FULL,
)

# Ce que le modèle rend à l'étape de sélection, justifications comprises.
PROPOSALS = [
    {
        "theme": "Nids de frelons",
        "main_keyword": "nid de frelons",
        "rationale": "Cluster le plus recherché.",
        "mother": {
            "target_keyword": "nid de frelons",
            "h1_title": "Nid de frelons : que faire",
            "meta_title": "Nid de frelons",
            "meta_description": "Guide complet.",
            "slug": "nid-de-frelons",
            "intent": "informational",
            "reason": "Volume le plus large et intent générique : couvre les 5 filles.",
        },
        "daughters": [
            {
                "target_keyword": f"kw fille {i}",
                "h1_title": f"Fille {i}",
                "meta_title": f"Fille {i}",
                "meta_description": f"Description {i}.",
                "slug": f"fille-{i}",
                "intent": "informational",
                "reason": f"Sous-sujet {i}, volume mesuré.",
            }
            for i in range(1, 6)
        ],
    }
]

POOL = [
    KeywordWithData(
        keyword=kw,
        intent=SearchIntent.INFORMATIONAL,
        cluster="frelons",
        monthly_volume=vol,
        cpc=1.1,
        competition_score=0.3,
        difficulty=30,
    )
    for kw, vol in [
        ("nid de frelons", 1900),
        ("kw fille 1", 480),
        ("kw fille 2", 390),
        ("kw fille 3", 320),
        ("kw fille 4", 210),
        ("kw fille 5", 90),
        ("nid de guepes au sol", 720),
        ("exterminateur frelons", 260),
    ]
]


def _check(condition: bool, label: str) -> bool:
    print(f"  {'✓' if condition else '✗'} {label}")
    return condition


class RecordingAnthropic(FakeAnthropic):
    """FakeAnthropic + réponse aux stubs des mots-clés ajoutés à la main."""

    async def complete_json(self, *, system: str, **kwargs: Any):
        from pipeline.validation import _STUB_SYSTEM

        if system == _STUB_SYSTEM:
            self.calls.append("stubs")
            prompt = kwargs.get("user_prompt", "")
            added = [
                line[2:].strip()
                for line in prompt.splitlines()
                if line.startswith("- ") and "Product:" not in line
            ]
            return {
                "stubs": [
                    {
                        "target_keyword": kw,
                        "h1_title": f"Titre pour {kw}",
                        "meta_title": kw[:60],
                        "meta_description": f"Meta de {kw}.",
                        "slug": kw.replace(" ", "-"),
                        "intent": "informational",
                        "secondary_keywords": [],
                    }
                    for kw in added
                ]
            }, self._result("haiku", None)
        return await super().complete_json(system=system, **kwargs)


# ============================================================


def test_snapshot() -> bool:
    print("\n[1] L'écran de validation reçoit de quoi arbitrer")
    snap = build_snapshot("run-1", POOL, PROPOSALS)

    ok = _check(len(snap.proposals) == 1, "1 cocon proposé")
    cocon = snap.proposals[0]
    ok &= _check(len(cocon.picks) == 6, "6 mots-clés (1 mère + 5 filles)")

    mother = cocon.mother
    ok &= _check(mother is not None and mother.keyword == "nid de frelons", "mère identifiée")
    ok &= _check(
        mother is not None and "couvre les 5 filles" in mother.reason,
        "justification du choix de la mère transmise",
    )
    ok &= _check(
        mother is not None and mother.monthly_volume == 1900,
        "volume DataForSEO joint au pick (et non inventé par le modèle)",
    )
    ok &= _check(all(p.reason for p in cocon.picks), "chaque fille porte sa justification")
    ok &= _check(len(snap.pool) == len(POOL), "pool complet disponible pour recocher")
    ok &= _check(snap.max_per_cocon == MAX_ARTICLES_PER_COCON == 6, "plafond de 6 annoncé au front")

    # Un mot-clé proposé mais absent du pool ne doit pas hériter d'un volume 0 :
    # « Google n'a pas la donnée » et « personne ne cherche » sont deux choses.
    orphelin = build_snapshot("run-1", [], PROPOSALS).proposals[0]
    ok &= _check(
        all(p.monthly_volume is None for p in orphelin.picks),
        "volume inconnu reste None, jamais 0",
    )
    return ok


async def test_pause() -> bool:
    print("\n[2] Le run s'arrête avant de payer la génération")
    store = InMemoryStore()
    client = RecordingAnthropic()

    snapshot = None
    try:
        await run_pipeline(
            FORM, anthropic=client, dataforseo=FakeDataForSEO(),
            store=store, run_id="run-1",
        )
        return _check(False, "le pipeline aurait dû suspendre")
    except AwaitingValidation as pause:
        # `pause` est effacé à la sortie du bloc except : on retient le snapshot.
        snapshot = pause.snapshot
        ok = _check(True, "AwaitingValidation levée")

    ok &= _check("article" not in client.calls, "aucun article généré avant validation")
    ok &= _check("serp" not in client.calls, "aucune analyse SERP payée avant validation")
    ok &= _check("keyword_research" in store.data, "recherche de mots-clés checkpointée")
    ok &= _check("cocon_design" not in store.data, "design des cocons pas encore figé")
    ok &= _check(len(snapshot.proposals) >= 1, "snapshot porté par l'exception")

    # Sans la case cochée, aucun arrêt.
    libre = FORM.model_copy(update={"validate_keywords": False})
    direct = await run_pipeline(
        libre, anthropic=RecordingAnthropic(), dataforseo=FakeDataForSEO(),
        store=InMemoryStore(), run_id="run-2",
    )
    ok &= _check(len(direct.articles) > 0, "case décochée : le run va au bout sans pause")
    return ok


async def test_decision_respectee() -> bool:
    print("\n[3] La décision de l'agence est appliquée au mot-clé près")
    # L'agence décoche « kw fille 5 », ajoute un KW du pool, et promeut
    # « kw fille 1 » en mère à la place de la proposition du modèle.
    decision = ValidationDecision(
        cocoons=[
            ValidatedCocon(
                index=0,
                mother_keyword="kw fille 1",
                daughter_keywords=[
                    "nid de frelons",
                    "kw fille 2",
                    "kw fille 3",
                    "nid de guepes au sol",
                ],
            )
        ]
    )
    client = RecordingAnthropic()
    rebuilt = await apply_decision(decision, PROPOSALS, POOL, FORM, client)
    cocoons = CoconBuilder().build(rebuilt)

    ok = _check(len(cocoons) == 1, "1 cocon construit")
    c = cocoons[0]
    ok &= _check(c.mother.target_keyword == "kw fille 1", "la fille promue est bien la mère")
    ok &= _check(
        c.mother.h1_title == "Fille 1",
        "métadonnées du modèle réutilisées, pas régénérées inutilement",
    )
    filles = [d.target_keyword for d in c.daughters]
    ok &= _check(filles == ["nid de frelons", "kw fille 2", "kw fille 3", "nid de guepes au sol"],
                 "filles exactement celles cochées, dans l'ordre")
    ok &= _check("kw fille 5" not in filles, "le mot-clé décoché a disparu")
    ok &= _check(
        client.calls.count("stubs") == 1,
        "un seul appel Haiku, groupé, pour les KW ajoutés à la main",
    )
    ajoute = next(d for d in c.daughters if d.target_keyword == "nid de guepes au sol")
    ok &= _check(ajoute.h1_title.startswith("Titre pour"), "stub généré pour le KW ajouté")
    ok &= _check(ajoute.slug == "nid-de-guepes-au-sol", "slug propre sur le KW ajouté")

    # Un mot-clé qui n'existe nulle part doit être refusé, pas généré.
    inconnu = ValidationDecision(cocoons=[
        ValidatedCocon(index=0, mother_keyword="inventé de toutes pièces",
                       daughter_keywords=["kw fille 1", "kw fille 2", "kw fille 3"])
    ])
    try:
        await apply_decision(inconnu, PROPOSALS, POOL, FORM, RecordingAnthropic())
        ok &= _check(False, "un KW hors run aurait dû être refusé")
    except ValueError as e:
        ok &= _check("inventé de toutes pièces" in str(e), "KW hors run refusé nommément")
    return ok


async def test_reprise_apres_validation() -> bool:
    print("\n[4] Le run relancé passe la porte au lieu de la refermer")
    store = InMemoryStore()
    client = RecordingAnthropic()

    try:
        await run_pipeline(FORM, anthropic=client, dataforseo=FakeDataForSEO(),
                           store=store, run_id="run-3")
    except AwaitingValidation:
        pass
    appels_avant = list(client.calls)

    # Ce que fait la route POST /runs/{id}/validation.
    research = store.data["keyword_research"]
    decision = ValidationDecision(cocoons=[
        ValidatedCocon(
            index=0,
            mother_keyword=research["proposals"][0]["mother"]["target_keyword"],
            daughter_keywords=[
                d["target_keyword"] for d in research["proposals"][0]["daughters"][:4]
            ],
        )
    ])
    keywords = [KeywordWithData.model_validate(k) for k in research["keywords"]]
    rebuilt = await apply_decision(
        decision, research["proposals"], keywords, FORM, RecordingAnthropic()
    )
    cocoons = CoconBuilder().build(rebuilt)
    store.data["cocon_design"] = [c.model_dump(mode="json") for c in cocoons]

    reprise = RecordingAnthropic()
    result = await run_pipeline(FORM, anthropic=reprise, dataforseo=FakeDataForSEO(),
                                store=store, run_id="run-3")

    ok = _check(len(result.articles) == 5, "5 articles générés (1 mère + 4 filles)")
    ok &= _check("expansion" not in reprise.calls and "selection" not in reprise.calls,
                 "recherche de mots-clés non repayée")
    ok &= _check(
        result.cocoons[0].mother.target_keyword == "nid de frelons",
        "la sélection validée est celle qui a été générée",
    )
    ok &= _check(len(appels_avant) > 0, "la 1re passe avait bien travaillé avant de suspendre")
    ok &= _check(
        PipelineStep.AWAITING_VALIDATION.value == "awaiting_validation",
        "étape exposée au front",
    )
    return ok


async def main() -> int:
    print("=" * 62)
    print("VALIDATION HUMAINE DE LA SÉLECTION DE MOTS-CLÉS")
    print("=" * 62)

    results = [
        test_snapshot(),
        await test_pause(),
        await test_decision_respectee(),
        await test_reprise_apres_validation(),
    ]

    print("\n" + "=" * 62)
    if all(results):
        print(f"✓ {len(results)}/{len(results)} groupes OK")
        return 0
    print(f"✗ {results.count(False)}/{len(results)} groupe(s) en échec")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
