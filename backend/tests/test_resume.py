"""Test de reprise sur checkpoint — vérifie qu'un run échoué ne se repaye pas.

Le scénario reproduit l'incident réel : le pipeline passe les étapes 1 à 3,
puis casse au milieu de la génération d'articles (crédit Anthropic épuisé).
La reprise doit repartir de l'article fautif, sans refaire un seul appel LLM
pour ce qui a déjà été produit.

Aucun appel réseau : le client Anthropic et DataForSEO sont simulés.

Usage :
    cd backend && .venv/bin/python -m tests.test_resume
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from clients.anthropic_client import MODELS, CompletionResult, ModelTier, UsageTotals
from db.checkpoints import CheckpointStore
from models import ClientForm, GenerationMode
from pipeline.article_generator import _BRIEF_SYSTEM, _FULL_SYSTEM
from pipeline.backlink_analyzer import _ANALYSIS_SYSTEM as _BACKLINK_SYSTEM
from pipeline.keyword_research import _EXPANSION_SYSTEM, _SELECTION_SYSTEM
from pipeline.orchestrator import run_pipeline
from pipeline.serp_analyzer import _ANALYSIS_SYSTEM as _SERP_SYSTEM

_SYSTEM_PROMPTS = {
    _EXPANSION_SYSTEM: "expansion",
    _SELECTION_SYSTEM: "selection",
    _SERP_SYSTEM: "serp",
    _BRIEF_SYSTEM: "brief",
    _FULL_SYSTEM: "article",
    _BACKLINK_SYSTEM: "backlinks",
}


# ============================================================
# Doublures
# ============================================================


class InMemoryStore:
    """CheckpointStore en mémoire — même contrat que Redis/Postgres."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, step: str) -> Any | None:
        return self.data.get(step)

    async def set(self, step: str, payload: Any) -> None:
        self.data[step] = payload


class FakeAnthropic:
    """Compte les appels et renvoie des payloads valides par type de prompt.

    `fail_on_article_index` simule la panne : le Nième appel de génération
    d'article lève, comme le ferait un 400 crédit épuisé.

    Le cumul `usage` est le vrai `UsageTotals` du client, alimenté par de vrais
    `CompletionResult` : l'orchestrateur le lit en fin de run pour construire
    `RunUsage`. Une doublure qui ne l'exposait pas faisait planter la passe 2
    juste avant la ligne d'arrivée, donc le test ne vérifiait jamais rien de ce
    qu'il annonçait vérifier.
    """

    def __init__(self, fail_on_article_index: int | None = None) -> None:
        self.calls: list[str] = []
        self.article_calls = 0
        self.fail_on_article_index = fail_on_article_index
        self.usage = UsageTotals()

    async def complete_json(
        self, *, model: str, system: str, user_prompt: str, max_tokens: int = 4096,
        cached_context: str | None = None,
    ) -> tuple[dict, Any]:
        kind = self._classify(system)
        self.calls.append(kind)

        if kind == "article":
            self.article_calls += 1
            if (
                self.fail_on_article_index is not None
                and self.article_calls == self.fail_on_article_index
            ):
                raise RuntimeError(
                    "Error code: 400 - Your credit balance is too low"
                )

        result = self._result(model, cached_context)
        self.usage.add(model, result)
        return self._payload(kind, user_prompt), result

    @staticmethod
    def _result(model: ModelTier, cached_context: str | None) -> CompletionResult:
        """Jetons plausibles — le contexte partagé passe en lecture de cache."""
        cached = len(cached_context.split()) if cached_context else 0
        return CompletionResult(
            text="{}",
            model=MODELS[model],
            input_tokens=800,
            output_tokens=1000,
            cache_read_tokens=cached,
            stop_reason="end_turn",
        )

    @staticmethod
    def _classify(system: str) -> str:
        # Comparaison au prompt système réel plutôt qu'à des mots-clés : un
        # reformulage de prompt ne doit pas faire silencieusement dériver le test.
        kind = _SYSTEM_PROMPTS.get(system)
        if kind is None:
            raise AssertionError(
                "Prompt système inconnu — la doublure doit être mise à jour :\n"
                f"{system[:200]}..."
            )
        return kind

    @staticmethod
    def _payload(kind: str, user_prompt: str) -> dict:
        if kind == "expansion":
            return {
                "keywords": [
                    {
                        "keyword": f"mot cle {i}",
                        "intent": "informational",
                        "cluster": f"cluster {i % 2}",
                        "relative_volume": "medium",
                        "difficulty_estimate": "medium",
                    }
                    for i in range(12)
                ]
            }
        if kind == "selection":
            return {
                "cocoons": [
                    {
                        "theme": "Thème test",
                        "main_keyword": "mot cle 0",
                        "rationale": "Cohérence sémantique.",
                        "mother": {
                            "target_keyword": "mot cle 0",
                            "h1_title": "Guide mère",
                            "meta_title": "Guide mère",
                            "meta_description": "Description mère.",
                            "slug": "guide-mere",
                            "intent": "informational",
                        },
                        "daughters": [
                            {
                                "target_keyword": f"mot cle {i}",
                                "h1_title": f"Fille {i}",
                                "meta_title": f"Fille {i}",
                                "meta_description": f"Description fille {i}.",
                                "slug": f"fille-{i}",
                                "intent": "informational",
                            }
                            for i in range(1, 5)
                        ],
                    }
                ]
            }
        if kind == "article":
            return {
                "sections": [{"h2": "Section", "h3s": [], "key_points": ["a"]}],
                "faq": [{"question": "Q ?", "answer": "R."}],
                "internal_links": [],
                "external_links": [],
                "content_markdown": "# Article\n\nContenu.",
                "schema_jsonld": {"@type": "Article"},
                "word_count": 1200,
                "eeat_score": {
                    "experience": 60, "expertise": 70,
                    "authoritativeness": 65, "trustworthiness": 70,
                    "overall": 66, "warnings": [],
                },
            }
        if kind == "brief":
            return {
                "sections": [{"h2": "Section", "h3s": [], "key_points": ["a"]}],
                "faq_questions": ["Q ?"],
                "internal_links_plan": [],
                "external_links_suggestions": [],
                "editorial_notes": "Notes.",
                "tone_guidance": "Pro.",
                "unique_angle": "Angle.",
            }
        if kind == "backlinks":
            return {
                "opportunities": [],
                "recommended_anchor_ratio": {
                    "exact": 0.1, "partial": 0.3, "branded": 0.4,
                    "naked_url": 0.1, "generic": 0.1,
                },
                "outreach_templates": {"guest_post": "Bonjour..."},
            }
        return {
            "key_entities": ["entité"],
            "key_topics": ["sujet"],
            "common_questions": ["Question ?"],
            "content_gaps": [],
            "recommended_word_count": 1800,
            "recommended_h2_count": 6,
            "competitive_angle": "Angle unique",
            "top_result_format": "guide long",
        }


class FakeDataForSEO:
    """SERP sans résultats organiques → aucun scraping réseau."""

    is_mock = True

    async def get_search_volume(self, keywords: list[str]) -> list[dict]:
        return [
            {
                "keyword": k,
                "search_volume": 500,
                "cpc": 1.2,
                "competition": 0.4,
                "keyword_difficulty": 35,
            }
            for k in keywords
        ]

    async def get_serp(self, keyword: str, depth: int = 10) -> dict:
        return {"organic_results": [], "paa": ["PAA ?"], "features": {}}

    async def get_backlinks_summary(self, url: str) -> dict:
        return {}

    async def get_referring_domains(self, url: str, limit: int = 30) -> list:
        return []


FORM = ClientForm(
    product="Produit test",
    description="Description du produit test pour le pipeline.",
    seed_keywords=["mot cle 0", "mot cle 1"],
    audience="Audience de test",
    niche="Niche de test",
    num_cocoons=1,
    mode=GenerationMode.FULL,
)


# ============================================================
# Scénario
# ============================================================


async def main() -> int:
    store: CheckpointStore = InMemoryStore()

    # --- Passe 1 : crash au 3e article ---
    crash_client = FakeAnthropic(fail_on_article_index=3)
    try:
        await run_pipeline(
            FORM,
            anthropic=crash_client,
            dataforseo=FakeDataForSEO(),
            store=store,
        )
    except RuntimeError as e:
        print(f"✓ Passe 1 : échec attendu au 3e article — {e}")
    else:
        print("✗ Passe 1 aurait dû échouer")
        return 1

    pass1 = list(crash_client.calls)
    checkpointed_articles = [k for k in store.data if k.startswith("article:")]
    print(f"  appels LLM passe 1        : {len(pass1)} {_counts(pass1)}")
    print(f"  checkpoints étapes        : {sorted(k for k in store.data if ':' not in k)}")
    print(f"  articles sauvegardés      : {len(checkpointed_articles)}")

    assert "keyword_research" in store.data, "étape 1 non checkpointée"
    assert "cocon_design" in store.data, "étape 2 non checkpointée"
    assert "serp_analysis" in store.data, "étape 3 non checkpointée"
    assert len(checkpointed_articles) == 2, (
        f"2 articles devaient survivre au crash, {len(checkpointed_articles)} trouvés"
    )

    cocon_ids_pass1 = {c["id"] for c in store.data["cocon_design"]}

    # --- Passe 2 : reprise, plus de panne ---
    resume_client = FakeAnthropic()
    result = await run_pipeline(
        FORM,
        anthropic=resume_client,
        dataforseo=FakeDataForSEO(),
        store=store,
    )
    pass2 = list(resume_client.calls)
    print(f"\n✓ Passe 2 : run terminé — {len(result.articles)} articles")
    print(f"  appels LLM passe 2        : {len(pass2)} {_counts(pass2)}")

    # Ce qui compte vraiment : rien de déjà payé n'est repayé.
    assert "expansion" not in pass2, "l'expansion KW a été refaite"
    assert "selection" not in pass2, "la sélection de cocons a été refaite"
    assert "serp" not in pass2, "l'analyse SERP a été refaite"

    regenerated = pass2.count("article")
    assert regenerated == 3, (
        f"3 articles restaient à générer (5 - 2 sauvés), {regenerated} appels observés"
    )
    assert len(result.articles) == 5, f"5 articles attendus, {len(result.articles)}"

    # Les cocon_id doivent survivre : sinon les articles repris pointeraient
    # vers des cocons fantômes et tout le maillage basculerait en cross_cocon.
    cocon_ids_pass2 = {c.id for c in result.cocoons}
    assert cocon_ids_pass1 == cocon_ids_pass2, (
        f"cocon_id instables entre les deux passes : {cocon_ids_pass1} vs {cocon_ids_pass2}"
    )
    assert not result.maillage_map.inter_cocon_links, (
        "un seul cocon : aucun lien inter-cocon ne devrait exister"
    )

    saved = len(pass1) - regenerated
    print(f"\n✓ Étapes 1-3 et 2 articles repris du checkpoint")
    print(f"✓ cocon_id stables entre les passes : {cocon_ids_pass2}")
    print(f"✓ {saved} appels LLM économisés sur la reprise")
    return 0


def _counts(calls: list[str]) -> dict:
    return {k: calls.count(k) for k in sorted(set(calls))}


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
