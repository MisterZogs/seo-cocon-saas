"""Client Claude avec prompt caching, routing multi-modèles et retry."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Literal

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    RateLimitError,
)
from anthropic.types import Message
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


def _should_retry(exc: BaseException) -> bool:
    """Ne retry que ce qui a une chance de passer au coup d'après.

    Retryable : rate limit (429), timeouts, coupures réseau, erreurs serveur
    (5xx dont 529 overloaded).
    Non retryable : les 4xx côté client (400 crédit insuffisant, 401 clé
    invalide, 413 payload trop gros) — insister coûte 4 tentatives et ~1 min
    d'attente pour la même erreur.
    """
    if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500
    return False


_is_retryable = retry_if_exception(_should_retry)


ModelTier = Literal["opus", "sonnet", "haiku"]

MODELS: dict[ModelTier, str] = {
    "opus": "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


# Tarifs Anthropic en USD par million de tokens, au 2026-08-07.
# `cache_write` = 1,25 × input (TTL 5 min, le défaut de `{"type": "ephemeral"}`).
# `cache_read`  = 0,1 × input.
# À revérifier à chaque changement de modèle dans MODELS ci-dessus.
PRICING_USD_PER_MTOK: dict[ModelTier, dict[str, float]] = {
    "opus": {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "sonnet": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "haiku": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
}


class UsageTotals(BaseModel):
    """Consommation cumulée d'une run, par palier de modèle et en coût.

    Le coût d'une génération était jusqu'ici une chaîne codée en dur dans le
    formulaire (« ~€15-25 par cocon »), inventée et jamais mesurée : le relevé
    réel sur un cocon de 6 articles donne ~$2-3. Une agence qui revend ces cocons
    a besoin du coût réel par run, donc on l'agrège ici plutôt que de l'estimer.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    by_tier: dict[str, float] = Field(default_factory=dict)

    def add(self, tier: ModelTier, result: "CompletionResult") -> None:
        p = PRICING_USD_PER_MTOK[tier]
        cost = (
            result.input_tokens * p["input"]
            + result.output_tokens * p["output"]
            + result.cache_creation_tokens * p["cache_write"]
            + result.cache_read_tokens * p["cache_read"]
        ) / 1_000_000
        self.calls += 1
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.cache_creation_tokens += result.cache_creation_tokens
        self.cache_read_tokens += result.cache_read_tokens
        self.cost_usd = round(self.cost_usd + cost, 6)
        self.by_tier[tier] = round(self.by_tier.get(tier, 0.0) + cost, 6)
        if result.cache_read_tokens:
            self.cache_read_by_tier[tier] = (
                self.cache_read_by_tier.get(tier, 0) + result.cache_read_tokens
            )

    cache_read_by_tier: dict[str, int] = Field(default_factory=dict)

    @property
    def cache_savings_usd(self) -> float:
        """Ce que les lectures de cache ont économisé face au plein tarif d'entrée.

        Sert à vérifier que le prompt caching sert réellement à quelque chose :
        sur un cocon, le contexte partagé fait ~20k tokens relus 5 fois.
        """
        return round(
            sum(
                toks * (PRICING_USD_PER_MTOK[tier]["input"] - PRICING_USD_PER_MTOK[tier]["cache_read"])  # type: ignore[index]
                for tier, toks in self.cache_read_by_tier.items()
            )
            / 1_000_000,
            6,
        )


class ResponseTruncated(ValueError):
    """La réponse a été coupée par max_tokens : le JSON est syntaxiquement incomplet.

    Type dédié pour que l'appelant puisse réessayer avec un budget plus large, au
    lieu de distinguer ce cas d'une vraie erreur de parsing en cherchant une chaîne
    dans le message.
    """

    def __init__(self, output_tokens: int, max_tokens: int) -> None:
        self.output_tokens = output_tokens
        self.max_tokens = max_tokens
        super().__init__(
            f"Réponse coupée à {output_tokens} tokens (plafond {max_tokens}) : "
            f"le JSON est incomplet."
        )


class CompletionResult(BaseModel):
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    stop_reason: str | None = None

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens


class AnthropicClient:
    """Wrapper autour du SDK Anthropic avec caching + retry + JSON parsing."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY manquant (env ou paramètre).")
        self._client = AsyncAnthropic(api_key=key)
        # Un client par run (l'orchestrateur en instancie un), donc ce cumul est
        # bien le total de la run et pas un compteur global de process.
        self.usage = UsageTotals()

    @retry(
        retry=_is_retryable,
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _create_message(self, **kwargs: Any) -> Message:
        return await self._client.messages.create(**kwargs)

    async def complete(
        self,
        *,
        model: ModelTier,
        system: str,
        user_prompt: str,
        cached_context: str | None = None,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        """Complétion générique avec option de caching du contexte stable.

        Le pattern optimal :
        - `system` : instructions courtes, changent peu → CACHÉ automatiquement
        - `cached_context` : gros bloc de contexte partagé entre plusieurs appels
          (ex: analyse SERP du cocon, stubs des 12 articles, brand voice)
          → premier bloc user CACHÉ (breakpoint)
        - `user_prompt` : la tâche spécifique de l'appel → non caché
        """
        model_id = MODELS[model]

        # System bloc — cache si non trivial (>1024 tokens ~= ~4000 chars)
        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
        if len(system) > 4000:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        # User content — contexte + prompt
        user_content: list[dict[str, Any]] = []
        if cached_context:
            user_content.append(
                {
                    "type": "text",
                    "text": cached_context,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        user_content.append({"type": "text", "text": user_prompt})

        response = await self._create_message(
            model=model_id,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user_content}],
        )

        # Extraction texte
        text = "".join(
            block.text for block in response.content if block.type == "text"
        )

        usage = response.usage
        result = CompletionResult(
            text=text,
            model=model_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            stop_reason=response.stop_reason,
        )

        logger.info(
            "claude.call",
            extra={
                "model": model_id,
                "in": result.input_tokens,
                "out": result.output_tokens,
                "cache_write": result.cache_creation_tokens,
                "cache_read": result.cache_read_tokens,
            },
        )
        return result

    async def complete_json(
        self,
        *,
        model: ModelTier,
        system: str,
        user_prompt: str,
        cached_context: str | None = None,
        max_tokens: int = 4096,
    ) -> tuple[dict | list, CompletionResult]:
        """Complétion + parsing JSON robuste (extrait du markdown si besoin)."""
        result = await self.complete(
            model=model,
            system=system + "\n\nIMPORTANT: Return ONLY valid JSON. No markdown, no explanation, no text before or after.",
            user_prompt=user_prompt,
            cached_context=cached_context,
            max_tokens=max_tokens,
        )
        # Une réponse coupée par max_tokens produit du JSON tronqué : inutile
        # d'essayer de le parser, et le ValueError qui en résultait pointait vers
        # « JSON invalide » alors que le JSON était bon, seulement incomplet.
        # Constaté au premier run sur données réelles : les SERP réelles remontent
        # bien plus d'entités et de questions que les mocks, donc des briefs plus
        # longs, qui débordaient les 4096 tokens.
        if result.stop_reason == "max_tokens":
            raise ResponseTruncated(result.output_tokens, max_tokens)

        parsed = _extract_json(result.text)
        return parsed, result


def _extract_json(text: str) -> dict | list:
    """Extrait du JSON même si Claude entoure de ```json ... ```."""
    text = text.strip()

    # Cas 1 : JSON direct
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Cas 2 : markdown code block
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Cas 3 : trouver le premier { ou [ jusqu'au dernier } ou ]
    for open_char, close_char in [("{", "}"), ("[", "]")]:
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    raise ValueError(
        f"Impossible d'extraire du JSON valide de : {text[:200]}..."
        f" (longueur totale {len(text)} caractères)"
    )
