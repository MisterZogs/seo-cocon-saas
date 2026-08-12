"""Limitation de débit pour les routes publiques (chantier 12).

Une route publique qui appelle un modèle dépense de l'argent réel à chaque
requête, sans compte à débiter en face. C'est le seul endroit du produit où un
inconnu peut nous coûter quelque chose, donc le seul qui a besoin de ça.

Trois garde-fous superposés, parce qu'ils arrêtent des choses différentes :

  · **par IP et par heure** — arrête l'usage humain excessif ;
  · **par IP et par jour** — arrête l'acharnement patient ;
  · **plafond global journalier** — arrête ce que les deux premiers ne voient
    pas : un botnet, ou simplement un succès inattendu. C'est le disjoncteur
    qui borne la dépense, et il est le seul à le faire vraiment.

**Échec fermé.** Si Redis ne répond pas, on refuse au lieu de laisser passer :
le compteur est la seule chose qui borne la facture. Redis étant par ailleurs
indispensable au reste du produit (file RQ), son indisponibilité n'est pas un
mode dégradé qu'on chercherait à traverser.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from fastapi import Request

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Quota:
    """Un plafond : tant de requêtes par fenêtre de tant de secondes."""

    limit: int
    window_seconds: int
    scope: str


class RateLimitExceeded(Exception):
    """429 — avec le délai avant nouvelle tentative, en secondes."""

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def client_ip(request: Request) -> str:
    """IP réelle de l'appelant, derrière le nginx du VPS.

    `X-Real-IP` d'abord : notre nginx le pose avec `$remote_addr` et **écrase**
    ce que le client aurait pu envoyer, il est donc non usurpable. Ce n'est pas
    le cas de `X-Forwarded-For`, que nginx *ajoute* (`$proxy_add_x_forwarded_for`)
    — un client peut en préfixer autant d'entrées qu'il veut, seule la dernière
    est vraie. D'où la lecture par la droite quand on doit s'en contenter.
    """
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()

    return request.client.host if request.client else "inconnu"


def _consume(redis, key: str, quota: Quota) -> None:
    """Incrémente un compteur à fenêtre fixe et lève si le plafond est franchi.

    Fenêtre fixe et non glissante : c'est moins précis aux bordures, mais ça
    tient en deux commandes Redis. Sur un aimant à prospects, la précision du
    comptage n'est pas l'enjeu — la borne de dépense l'est.
    """
    count = redis.incr(key)
    if count == 1:
        redis.expire(key, quota.window_seconds)
    if count > quota.limit:
        ttl = redis.ttl(key)
        retry_after = int(ttl) if ttl and ttl > 0 else quota.window_seconds
        raise RateLimitExceeded(_message(quota, retry_after), retry_after)


def _message(quota: Quota, retry_after: int) -> str:
    if quota.scope == "global":
        return (
            "L'outil gratuit a atteint son quota du jour — il tourne à nos frais. "
            "Réessayez demain, ou créez un compte : l'essai offre 3 cocons complets, "
            "avec volumes de recherche réels et briefs éditoriaux."
        )
    if quota.window_seconds >= 86400:
        return (
            "Vous avez atteint la limite quotidienne de l'outil gratuit. "
            "Créez un compte pour continuer — l'essai offre 3 cocons complets, "
            "sans carte bancaire."
        )
    minutes = max(1, retry_after // 60)
    return (
        f"Trop de générations d'affilée. Réessayez dans {minutes} minute(s), "
        "ou créez un compte pour ne plus être limité."
    )


def enforce_public_quota(request: Request, redis, *, bucket: str = "cocon-preview") -> str:
    """Applique les trois plafonds. Retourne l'IP retenue, pour les logs.

    Les compteurs sont incrémentés **avant** l'appel au modèle : compter après
    laisserait passer toutes les requêtes concurrentes d'une rafale, qui est
    exactement la forme que prend un abus.
    """
    ip = client_ip(request)
    quotas = [
        Quota(_int_env("PUBLIC_HOURLY_LIMIT", 3), 3600, "ip"),
        Quota(_int_env("PUBLIC_DAILY_LIMIT", 10), 86400, "ip"),
        Quota(_int_env("PUBLIC_GLOBAL_DAILY_LIMIT", 300), 86400, "global"),
    ]

    try:
        for quota in quotas:
            key = (
                f"rl:{bucket}:global:{quota.window_seconds}"
                if quota.scope == "global"
                else f"rl:{bucket}:{ip}:{quota.window_seconds}"
            )
            _consume(redis, key, quota)
    except RateLimitExceeded:
        raise
    except Exception as e:
        # Échec fermé : sans compteur fiable, il n'y a plus rien qui borne la
        # dépense de cette route.
        logger.error("Limiteur indisponible (%s) — requête publique refusée.", e)
        raise RateLimitExceeded(
            "Le service est momentanément indisponible. Réessayez dans un instant.",
            60,
        ) from e

    return ip


def enforce_agency_quota(agency_id: str, redis, *, bucket: str = "site-audit") -> None:
    """Plafonne une route gratuite mais coûteuse en TEMPS, pour une agence connectée.

    Différent d'`enforce_public_quota` sur deux points, et la distinction est
    voulue :

    · **La ressource protégée n'est pas l'argent, c'est le worker.** L'audit
      n'appelle ni Claude ni DataForSEO ; ce qu'il consomme, c'est la file RQ,
      qui est unique. Trois crawls de 200 pages lancés d'affilée retardent
      toutes les générations en cours — celles-là facturées.
    · **La clé est l'agence, pas l'IP.** L'appelant est authentifié : brider par
      IP pénaliserait une agence entière derrière un NAT et n'arrêterait pas un
      compte qui change de réseau.

    Volontairement large : ce n'est pas un anti-abus, c'est un garde-fou contre
    la boucle involontaire.
    """
    quotas = [
        Quota(_int_env("AUDIT_HOURLY_LIMIT", 5), 3600, "agency"),
        Quota(_int_env("AUDIT_DAILY_LIMIT", 20), 86400, "agency"),
    ]
    try:
        for quota in quotas:
            _consume(redis, f"rl:{bucket}:{agency_id}:{quota.window_seconds}", quota)
    except RateLimitExceeded:
        raise
    except Exception as e:
        # Échec fermé, comme pour la route publique : sans compteur, plus rien
        # ne protège la file de jobs.
        logger.error("Limiteur indisponible (%s) — audit refusé.", e)
        raise RateLimitExceeded(
            "Le service est momentanément indisponible. Réessayez dans un instant.",
            60,
        ) from e
