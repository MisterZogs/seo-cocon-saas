"""Checkpoints d'étapes — permettent de reprendre un run échoué sans tout refaire.

Le pipeline coûte cher : un run mode FULL, c'est ~12 appels Opus/Sonnet plus le
scraping des SERP. Quand l'étape 4 casse (crédit épuisé, rate limit, timeout),
les étapes 1 à 3 sont déjà payées — il serait absurde de les rejouer.

Chaque étape sérialise sa sortie sous une clé `(run_id, step)`. À la reprise,
l'orchestrateur relit ce qui existe et saute directement là où ça s'est arrêté.

Deux backends, choisis automatiquement :
- **Postgres** si `DATABASE_URL` est défini — durable, la reprise reste
  possible des semaines après.
- **Redis** sinon — déjà présent en prod, TTL 7 jours. Couvre le cas réel
  (on reprend un run quelques minutes après l'échec) sans aucun setup.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

CHECKPOINT_TTL_SECONDS = 7 * 24 * 3600


class CheckpointStore(Protocol):
    """Store scopé à un run : les implémentations connaissent déjà le run_id."""

    async def get(self, step: str) -> Any | None: ...

    async def set(self, step: str, payload: Any) -> None: ...


class NullCheckpointStore:
    """Aucun checkpoint — comportement historique (tout rejouer)."""

    async def get(self, step: str) -> Any | None:
        return None

    async def set(self, step: str, payload: Any) -> None:
        return None


class RedisCheckpointStore:
    """Checkpoints dans Redis, expirés au bout de `CHECKPOINT_TTL_SECONDS`."""

    def __init__(self, redis_url: str, run_id: str) -> None:
        self._url = redis_url
        self._run_id = run_id
        self._client: Any | None = None

    def _key(self, step: str) -> str:
        return f"checkpoint:{self._run_id}:{step}"

    async def _get_client(self) -> Any:
        if self._client is None:
            from redis import asyncio as aioredis

            self._client = aioredis.from_url(self._url)
        return self._client

    async def get(self, step: str) -> Any | None:
        try:
            client = await self._get_client()
            raw = await client.get(self._key(step))
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning("Lecture checkpoint %s impossible (ignoré) : %s", step, e)
            return None

    async def set(self, step: str, payload: Any) -> None:
        try:
            client = await self._get_client()
            await client.set(
                self._key(step),
                json.dumps(payload, ensure_ascii=False),
                ex=CHECKPOINT_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("Écriture checkpoint %s impossible (ignoré) : %s", step, e)


class SupabaseCheckpointStore:
    """Checkpoints dans la table `run_checkpoints` — durables."""

    def __init__(self, repository: Any, run_id: str) -> None:
        self._repo = repository
        self._run_id = run_id

    async def get(self, step: str) -> Any | None:
        return await self._repo.get_checkpoint(self._run_id, step)

    async def set(self, step: str, payload: Any) -> None:
        await self._repo.save_checkpoint(self._run_id, step, payload)


def make_checkpoint_store(
    run_id: str | None,
    *,
    redis_url: str,
    repository: Any | None = None,
) -> CheckpointStore:
    """Choisit le meilleur backend disponible pour ce run."""
    if not run_id:
        return NullCheckpointStore()
    if repository is not None and getattr(repository, "enabled", False):
        return SupabaseCheckpointStore(repository, run_id)
    return RedisCheckpointStore(redis_url, run_id)
