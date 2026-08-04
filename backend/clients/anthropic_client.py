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
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


ModelTier = Literal["opus", "sonnet", "haiku"]

MODELS: dict[ModelTier, str] = {
    "opus": "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


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

    @retry(
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIStatusError)),
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

    raise ValueError(f"Impossible d'extraire du JSON valide de : {text[:200]}...")
