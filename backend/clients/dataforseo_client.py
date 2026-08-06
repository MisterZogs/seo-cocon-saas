"""Client DataForSEO — mode réel si credentials, mock sinon.

Endpoints utilisés :
- Keywords search volume : /v3/keywords_data/google_ads/search_volume/live
- SERP organic : /v3/serp/google/organic/live/regular
- PAA : extraite de la SERP
- Backlinks summary : /v3/backlinks/summary/live
- Referring domains : /v3/backlinks/referring_domains/live
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dataforseo.com"


def _is_retryable(exc: BaseException) -> bool:
    """Ne rejoue que ce qui a une chance d'aboutir au 2e essai.

    `httpx.HTTPError` est la classe parente de `HTTPStatusError` : l'utiliser
    comme prédicat fait rejouer 3 fois les erreurs définitives. Constaté en réel
    sur un compte non vérifié — chaque appel produisait 3 requêtes 403 et 15 s
    de backoff pour rien. Même bug que celui corrigé sur le client Anthropic.

    401/403 (credentials, compte non vérifié) et 402 (solde épuisé) ne se
    résolvent pas en réessayant : il faut une action de l'agence.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    # Timeouts, coupures réseau, DNS : transitoires par nature.
    return isinstance(exc, httpx.TransportError)


class DataForSEOClient:
    """Client DataForSEO avec fallback mock automatique."""

    def __init__(
        self,
        login: str | None = None,
        password: str | None = None,
        location_code: int = 2250,  # France
        language_code: str = "fr",
    ) -> None:
        self._login = login or os.getenv("DATAFORSEO_LOGIN")
        self._password = password or os.getenv("DATAFORSEO_PASSWORD")
        self.location_code = location_code
        self.language_code = language_code
        self._mock = not (self._login and self._password)
        if self._mock:
            logger.warning("DataForSEO en MODE MOCK (credentials manquants).")

    @property
    def is_mock(self) -> bool:
        return self._mock

    # ============================================================
    # Public API
    # ============================================================

    async def get_search_volume(self, keywords: list[str]) -> list[dict[str, Any]]:
        """Retourne pour chaque KW : volume, cpc, competition (0-1), difficulty."""
        if self._mock:
            return [_mock_kw_data(kw) for kw in keywords]

        payload = [
            {
                "keywords": keywords,
                "location_code": self.location_code,
                "language_code": self.language_code,
            }
        ]
        data = await self._post(
            "/v3/keywords_data/google_ads/search_volume/live", payload
        )
        return self._parse_search_volume(data)

    async def get_serp(self, keyword: str, depth: int = 10) -> dict[str, Any]:
        """Retourne la SERP (top N URLs + features + PAA)."""
        if self._mock:
            return _mock_serp(keyword, depth)

        payload = [
            {
                "keyword": keyword,
                "location_code": self.location_code,
                "language_code": self.language_code,
                "depth": depth,
            }
        ]
        # `advanced` et non `regular` : au MÊME prix ($0.002), `regular` ne renvoie
        # que les items `organic`, tandis qu'`advanced` remonte aussi people_also_ask,
        # featured_snippet, video, local_pack… Mesuré sur « destruction nid de frelons » :
        # regular → 9 organic et rien d'autre ; advanced → les mêmes 9 organic + 4
        # questions PAA + local_pack + video + related_searches. Avec `regular`, les
        # champs `paa` et `features` du parser restaient vides en permanence, donc les
        # FAQ perdaient leur source de questions réelles.
        data = await self._post("/v3/serp/google/organic/live/advanced", payload)
        return self._parse_serp(data)

    async def get_backlinks_summary(self, target_url: str) -> dict[str, Any]:
        """Résumé backlinks : total, referring domains, DR estimé."""
        if self._mock:
            return _mock_backlinks_summary(target_url)

        # `rank_scale` : sans ce paramètre, DataForSEO renvoie son rank sur 0-1000
        # (rentokil.com sortait à 440). Les agences lisent un DR sur 0-100 — un
        # « DR 440 » dans le livrable passe pour un bug. L'échelle étant
        # logarithmique, on ne peut pas diviser par 10 : c'est l'API qui convertit.
        payload = [{"target": target_url, "internal_list_limit": 10, "rank_scale": "one_hundred"}]
        data = await self._post("/v3/backlinks/summary/live", payload)
        return self._parse_backlinks_summary(data)

    async def get_referring_domains(
        self, target_url: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Liste des domaines référents pour un URL cible."""
        if self._mock:
            return _mock_referring_domains(target_url, limit)

        payload = [
            {"target": target_url, "limit": limit, "order_by": ["rank,desc"],
             "rank_scale": "one_hundred"}  # voir get_backlinks_summary
        ]
        data = await self._post("/v3/backlinks/referring_domains/live", payload)
        return self._parse_referring_domains(data)

    # ============================================================
    # HTTP
    # ============================================================

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=2, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _post(self, path: str, payload: list[dict[str, Any]]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{BASE_URL}{path}",
                auth=(self._login, self._password),  # type: ignore[arg-type]
                json=payload,
            )
            if response.status_code >= 400:
                # DataForSEO explique la cause dans le corps ; `raise_for_status`
                # seul ne renvoie que « 403 Forbidden », inexploitable en support.
                logger.error(
                    "DataForSEO %s → HTTP %d : %s",
                    path,
                    response.status_code,
                    response.text[:300],
                )
            response.raise_for_status()

            data = response.json()
            # Une réponse 200 peut porter une erreur applicative (quota, paramètre
            # invalide). Sans ce log, les parsers renvoient une liste vide et le
            # pipeline continue silencieusement sur des données absentes.
            if data.get("status_code") != 20000:
                logger.error(
                    "DataForSEO %s → status %s : %s",
                    path,
                    data.get("status_code"),
                    data.get("status_message"),
                )
            return data

    # ============================================================
    # Parsers réponses réelles DataForSEO
    # ============================================================

    @staticmethod
    def _parse_search_volume(data: dict[str, Any]) -> list[dict[str, Any]]:
        tasks = data.get("tasks", [])
        if not tasks or not tasks[0].get("result"):
            return []
        results = []
        for item in tasks[0]["result"]:
            results.append(
                {
                    "keyword": item.get("keyword"),
                    "monthly_volume": item.get("search_volume") or 0,
                    "cpc": item.get("cpc") or 0.0,
                    "competition_score": item.get("competition_index", 0) / 100
                    if item.get("competition_index") is not None
                    else None,
                    "difficulty": item.get("competition_index"),
                }
            )
        return results

    @staticmethod
    def _parse_serp(data: dict[str, Any]) -> dict[str, Any]:
        tasks = data.get("tasks", [])
        if not tasks or not tasks[0].get("result"):
            return {"organic_results": [], "features": {}, "paa": []}

        result = tasks[0]["result"][0]
        items = result.get("items", [])

        organic_results = []
        paa = []
        features = {
            "featured_snippet": False,
            "video_carousel": False,
            "image_pack": False,
            "local_pack": False,
            "knowledge_panel": False,
        }

        for item in items:
            item_type = item.get("type")
            if item_type == "organic":
                organic_results.append(
                    {
                        "url": item.get("url"),
                        "title": item.get("title"),
                        "description": item.get("description"),
                        # `rank_group` = rang parmi les résultats organiques (1,2,3…).
                        # `rank_absolute` compte TOUS les éléments de la SERP : le 1er
                        # organique y ressortait en position 3 dès qu'un local_pack le
                        # précédait. C'est la position organique qui intéresse l'agence.
                        "position": item.get("rank_group"),
                    }
                )
            elif item_type == "people_also_ask":
                for expanded in item.get("items", []):
                    paa.append(expanded.get("title", ""))
            elif item_type == "featured_snippet":
                features["featured_snippet"] = True
            elif item_type == "video":
                features["video_carousel"] = True
            elif item_type == "images":
                features["image_pack"] = True
            elif item_type == "local_pack":
                features["local_pack"] = True
            elif item_type == "knowledge_graph":
                features["knowledge_panel"] = True

        return {
            "organic_results": organic_results,
            "features": features,
            "paa": paa,
        }

    @staticmethod
    def _parse_backlinks_summary(data: dict[str, Any]) -> dict[str, Any]:
        tasks = data.get("tasks", [])
        if not tasks or not tasks[0].get("result"):
            return {}
        r = tasks[0]["result"][0]
        return {
            "target": r.get("target"),
            "total_backlinks": r.get("backlinks", 0),
            "referring_domains": r.get("referring_domains", 0),
            # 0-100 garanti par `rank_scale: one_hundred` dans la requête.
            "domain_rating": r.get("rank"),
        }

    @staticmethod
    def _parse_referring_domains(data: dict[str, Any]) -> list[dict[str, Any]]:
        tasks = data.get("tasks", [])
        if not tasks or not tasks[0].get("result"):
            return []
        items = tasks[0]["result"][0].get("items", [])
        return [
            {
                "domain": item.get("domain"),
                "domain_rating": item.get("rank"),
                "backlinks_count": item.get("backlinks"),
                "first_seen": item.get("first_seen"),
            }
            for item in items
        ]


# ============================================================
# MOCK DATA (déterministe par hash du keyword)
# ============================================================


def _seeded_random(seed_str: str) -> random.Random:
    h = hashlib.md5(seed_str.encode()).hexdigest()
    return random.Random(int(h, 16))


def _mock_kw_data(keyword: str) -> dict[str, Any]:
    rng = _seeded_random(keyword)
    # Volume basé sur longueur (long tail = moins de volume)
    base = max(50, 5000 - len(keyword) * 100)
    volume = int(base * rng.uniform(0.3, 2.0))
    cpc = round(rng.uniform(0.2, 4.5), 2)
    competition = round(rng.uniform(0.1, 0.9), 2)
    return {
        "keyword": keyword,
        "monthly_volume": volume,
        "cpc": cpc,
        "competition_score": competition,
        "difficulty": int(competition * 100),
    }


def _mock_serp(keyword: str, depth: int) -> dict[str, Any]:
    rng = _seeded_random(f"serp:{keyword}")
    domains = [
        "wikipedia.org",
        "medium.com",
        "hubspot.com",
        "moz.com",
        "semrush.com",
        "ahrefs.com",
        "backlinko.com",
        "searchenginejournal.com",
        "wordstream.com",
        "neilpatel.com",
        "abondance.com",
        "webrankinfo.com",
        "journaldunet.com",
    ]
    rng.shuffle(domains)

    organic_results = [
        {
            "url": f"https://{domains[i % len(domains)]}/blog/{keyword.replace(' ', '-')}-guide-{i}",
            "title": f"{keyword.title()} — {['guide complet', 'tutoriel', 'meilleures pratiques', 'comparatif 2026', 'stratégie'][i % 5]}",
            "description": f"Découvrez tout ce qu'il faut savoir sur {keyword}. Guide {['débutant', 'avancé', 'expert'][i % 3]}...",
            "position": i + 1,
        }
        for i in range(depth)
    ]

    paa = [
        f"Comment optimiser {keyword} ?",
        f"Quel est le meilleur outil pour {keyword} ?",
        f"Pourquoi utiliser {keyword} ?",
        f"Combien coûte {keyword} ?",
    ]

    return {
        "organic_results": organic_results,
        "features": {
            "featured_snippet": rng.random() > 0.6,
            "video_carousel": rng.random() > 0.7,
            "image_pack": rng.random() > 0.5,
            "local_pack": False,
            "knowledge_panel": rng.random() > 0.8,
        },
        "paa": paa,
    }


def _mock_backlinks_summary(target_url: str) -> dict[str, Any]:
    rng = _seeded_random(f"bl:{target_url}")
    total = rng.randint(100, 50000)
    return {
        "target": target_url,
        "total_backlinks": total,
        "referring_domains": rng.randint(20, total // 5),
        "domain_rating": rng.randint(20, 80),
    }


def _mock_referring_domains(target_url: str, limit: int) -> list[dict[str, Any]]:
    rng = _seeded_random(f"rd:{target_url}")
    domain_pool = [
        "forbes.com",
        "techcrunch.com",
        "medium.com",
        "reddit.com",
        "quora.com",
        "hubspot.com",
        "linkedin.com",
        "wikipedia.org",
        "producthunt.com",
        "github.com",
        "stackoverflow.com",
        "dev.to",
        "abondance.com",
        "webrankinfo.com",
        "journaldunet.com",
        "codeur.com",
    ]
    rng.shuffle(domain_pool)
    return [
        {
            "domain": domain_pool[i % len(domain_pool)],
            "domain_rating": rng.randint(30, 92),
            "backlinks_count": rng.randint(1, 15),
            "first_seen": "2024-01-01",
        }
        for i in range(min(limit, len(domain_pool)))
    ]
