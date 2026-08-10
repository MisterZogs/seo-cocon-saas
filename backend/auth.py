"""Authentification des agences — JWT maison, sans service tiers.

Arbitrage du 2026-08-10 : Clerk/Auth0 écartés. Les faire entrer reviendrait sur
le choix d'auto-héberger (Supabase avait été abandonné pour la même raison) pour
un gain d'une journée de travail.

Trois briques, volontairement minimales :

1. **Hash de mot de passe : Argon2id** (`argon2-cffi`). Ni passlib (plus
   maintenu, casse avec bcrypt 4.x), ni bcrypt nu (tronque silencieusement à
   72 octets). Le coût est celui par défaut de la lib, calibré par ses auteurs.
2. **Jeton : JWT HS256** signé avec `JWT_SECRET`. Pas de refresh token au MVP —
   un jeton de 7 jours, et on se reconnecte. Un refresh n'a de valeur que face
   à un jeton court, et rien ici ne justifie un jeton court.
3. **Dépendance `current_agency`** posée sur toutes les routes métier.

⚠️ **Aucune dégradation gracieuse ici, contrairement à `db/postgres.py`.**
La persistance des runs avale ses erreurs parce que perdre l'historique coûte
moins cher que perdre une génération payée. Ce raisonnement ne s'applique pas à
l'auth : une erreur avalée y devient une autorisation accordée. Tout échec est
donc remonté, et l'absence de configuration empêche le démarrage.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"

# 7 jours. Assez long pour qu'une agence ne se reconnecte pas en plein milieu
# d'un run de 40 minutes, assez court pour qu'un jeton fuité expire.
DEFAULT_EXPIRE_HOURS = 168

# Argon2id, paramètres par défaut de la lib (~50-60 ms sur un VPS modeste).
_hasher = PasswordHasher()

# Longueur minimale du mot de passe. Pas de règle de composition (majuscule,
# chiffre, caractère spécial) : le NIST les déconseille depuis 2017, elles
# produisent des mots de passe plus courts et plus prévisibles.
MIN_PASSWORD_LENGTH = 10

# Un secret trop court rend HS256 brute-forçable hors ligne à partir d'un seul
# jeton observé. 32 caractères est le plancher, pas une recommandation.
MIN_SECRET_LENGTH = 32


class AuthNotConfigured(RuntimeError):
    """`JWT_SECRET` absent ou trop court — le backend refuse de démarrer."""


def get_secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if not secret:
        raise AuthNotConfigured(
            "JWT_SECRET est absent du .env du backend. Générez-en un avec :\n"
            "    python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
            "puis ajoutez JWT_SECRET=... au .env. Le changer invalide tous les "
            "jetons déjà émis (tout le monde doit se reconnecter)."
        )
    if len(secret) < MIN_SECRET_LENGTH:
        raise AuthNotConfigured(
            f"JWT_SECRET fait {len(secret)} caractères, il en faut au moins "
            f"{MIN_SECRET_LENGTH} : en dessous, la signature HS256 se casse hors "
            "ligne à partir d'un seul jeton intercepté."
        )
    return secret


def expire_hours() -> int:
    try:
        return max(1, int(os.getenv("JWT_EXPIRE_HOURS", DEFAULT_EXPIRE_HOURS)))
    except ValueError:
        return DEFAULT_EXPIRE_HOURS


# ============================================================
# Mots de passe
# ============================================================


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> tuple[bool, str | None]:
    """(mot de passe correct ?, hash à réenregistrer si les paramètres ont bougé).

    Le second membre est non nul quand `argon2-cffi` a durci ses réglages par
    défaut depuis la création du compte : on en profite pour remonter le coût
    sans demander quoi que ce soit à l'utilisateur.
    """
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None

    try:
        if _hasher.check_needs_rehash(password_hash):
            return True, _hasher.hash(password)
    except Exception:  # pragma: no cover — ne doit jamais bloquer une connexion
        logger.warning("check_needs_rehash a échoué, hash conservé tel quel")
    return True, None


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Le mot de passe doit faire au moins {MIN_PASSWORD_LENGTH} caractères.",
        )


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ============================================================
# Jetons
# ============================================================


@dataclass(frozen=True)
class Agency:
    """Agence authentifiée, reconstruite depuis le jeton (aucun accès base)."""

    id: str
    email: str
    name: str


def create_access_token(agency: Agency) -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=expire_hours())
    payload = {
        "sub": agency.id,
        "email": agency.email,
        "name": agency.name,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, get_secret(), algorithm=ALGORITHM), expires_at


_INVALID = HTTPException(
    status_code=401,
    detail="Session expirée ou invalide — reconnectez-vous.",
    headers={"WWW-Authenticate": "Bearer"},
)


def decode_access_token(token: str) -> Agency:
    try:
        claims = jwt.decode(token, get_secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise _INVALID

    agency_id = claims.get("sub")
    if not agency_id:
        raise _INVALID
    return Agency(
        id=str(agency_id),
        email=claims.get("email", ""),
        name=claims.get("name", ""),
    )


# `auto_error=False` : sans ce réglage, HTTPBearer renvoie un 403 sec sur une
# requête sans en-tête. On veut un 401 avec un message en français.
_bearer = HTTPBearer(auto_error=False)


async def current_agency(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Agency:
    if credentials is None or not credentials.credentials:
        raise _INVALID
    return decode_access_token(credentials.credentials)


async def current_agency_from_query(
    request: Request,
    token: str | None = Query(
        default=None,
        description="Jeton d'accès — repli pour les routes SSE",
    ),
) -> Agency:
    """Variante pour le flux SSE de progression.

    `EventSource`, côté navigateur, ne sait pas poser d'en-tête `Authorization`
    (c'est une limite de l'API, pas un choix d'implémentation du front). Le seul
    moyen d'authentifier `/jobs/{id}/stream` sans réécrire le suivi en fetch
    streaming est donc de passer le jeton en paramètre d'URL.

    Conséquence assumée : le jeton apparaît dans les journaux d'accès nginx. Le
    risque est borné — le jeton expire, la route est en lecture seule, et le
    serveur de logs est le nôtre. L'en-tête reste accepté et prioritaire pour
    tous les autres appelants (curl, tests).
    """
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return decode_access_token(header[7:].strip())
    if token:
        return decode_access_token(token)
    raise _INVALID
