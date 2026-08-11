"""Client WordPress REST API — publication des articles dans le site du client.

Authentification par **mot de passe d'application** (natif depuis WordPress 5.6,
`Utilisateurs → Profil → Mots de passe d'application`). Pas d'OAuth, pas de
plugin à installer chez le client : c'est la seule méthode qui marche sur un
WordPress auto-hébergé standard, et l'agence peut révoquer le mot de passe d'un
clic sans changer celui du compte.

⚠️ **Ces identifiants ne sont jamais persistés.** Ils arrivent dans le corps de
la requête d'export, servent le temps de l'appel, et disparaissent. C'est la
raison pour laquelle l'export est une route synchrone et non un job RQ : les
arguments d'un job dorment dans Redis 24 h (`failure_ttl`), et un mot de passe
d'application y donnerait un accès en écriture au site d'un client.
"""

from __future__ import annotations

import base64
import logging
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Un WordPress lent (mutualisé, plugins lourds) met plusieurs secondes à créer
# un article. Généreux à la lecture, patient à l'écriture.
TIMEOUT = httpx.Timeout(10.0, read=30.0, write=30.0)


class WordPressError(RuntimeError):
    """Erreur remontée par WordPress, formulée pour l'agence."""


def _is_retryable(exc: BaseException) -> bool:
    """Même règle que sur les autres clients : ne rejouer que le transitoire.

    401/403 (mot de passe d'application faux ou révoqué) et 404 (URL qui n'est
    pas un WordPress) ne se résolvent pas en réessayant.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, httpx.TransportError)


def normalize_site_url(raw: str) -> str:
    """`exemple.fr` → `https://exemple.fr/`. Refuse ce qui n'est pas du HTTP(S).

    Les agences collent l'URL telle qu'elles l'ont sous la main : avec ou sans
    protocole, avec ou sans `/wp-admin` au bout.
    """
    url = (raw or "").strip()
    if not url:
        raise WordPressError("L'URL du site WordPress est vide.")

    # Préfixer sans regarder produisait `https://ftp://client.fr`, que `urlparse`
    # accepte sans broncher (netloc = « ftp: »). On distingue donc « pas de
    # protocole » de « mauvais protocole » avant d'ajouter quoi que ce soit.
    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            raise WordPressError(
                f"« {raw} » n'est pas une URL de site valide : protocole « {scheme} » "
                "non supporté, seuls http et https le sont."
            )
    else:
        url = f"https://{url}"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise WordPressError(f"« {raw} » n'est pas une URL de site valide.")

    path = parsed.path
    for suffix in ("/wp-admin", "/wp-admin/", "/wp-login.php", "/wp-json", "/wp-json/"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if not path.endswith("/"):
        path += "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


class WordPressClient:
    """Publication d'articles via `/wp-json/wp/v2/`."""

    def __init__(self, site_url: str, username: str, app_password: str) -> None:
        self.site_url = normalize_site_url(site_url)
        # WordPress affiche le mot de passe d'application par groupes de quatre
        # caracteres separes par des espaces, parfois insecables selon le
        # navigateur. Recopie tel quel il echoue en 401, et l'agence en conclut
        # que ses identifiants sont faux. `split()` avale toutes les espaces.
        password = "".join((app_password or "").split())
        if not username or not password:
            raise WordPressError(
                "Identifiant et mot de passe d'application sont tous deux requis."
            )
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------

    async def __aenter__(self) -> "WordPressClient":
        self._client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _url(self, path: str) -> str:
        return urljoin(self.site_url, f"wp-json/wp/v2/{path.lstrip('/')}")

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        assert self._client is not None, "utiliser WordPressClient en context manager"
        response = await self._client.request(
            method, self._url(path), headers=self._headers, **kwargs
        )
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------

    async def check_connection(self) -> dict[str, Any]:
        """Vérifie les identifiants AVANT de publier quoi que ce soit.

        Publier six articles pour découvrir au quatrième que le compte n'a pas
        le droit d'écrire laisse le site du client à moitié rempli.
        """
        try:
            me = await self._request("GET", "users/me?context=edit")
        except httpx.HTTPStatusError as e:
            raise WordPressError(_explain(e, self.site_url)) from e
        except httpx.HTTPError as e:
            raise WordPressError(
                f"{self.site_url} est injoignable ({type(e).__name__}). "
                "Vérifiez l'URL et que le site répond."
            ) from e

        capabilities = me.get("capabilities") or {}
        if not capabilities.get("publish_posts") and not capabilities.get("edit_posts"):
            raise WordPressError(
                f"Le compte « {me.get('name') or me.get('slug')} » n'a pas le droit "
                "de créer des articles sur ce site. Un rôle Auteur au minimum est requis."
            )
        return me

    async def find_post_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Article existant portant ce slug, quel que soit son statut.

        C'est ce qui rend l'export **rejouable** : réexporter met à jour au lieu
        de créer un doublon, sans que nous ayons à stocker la moindre
        correspondance de notre côté.
        """
        try:
            found = await self._request(
                "GET", f"posts?slug={slug}&status=any&per_page=1&context=edit"
            )
        except httpx.HTTPStatusError:
            # `status=any` exige des droits de lecture élargis. Si le compte ne
            # les a pas, on retombe sur une recherche publique.
            found = await self._request("GET", f"posts?slug={slug}&per_page=1")
        return found[0] if isinstance(found, list) and found else None

    async def create_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._request("POST", "posts", json=payload)
        except httpx.HTTPStatusError as e:
            raise WordPressError(_explain(e, self.site_url)) from e

    async def update_post(self, post_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._request("POST", f"posts/{post_id}", json=payload)
        except httpx.HTTPStatusError as e:
            raise WordPressError(_explain(e, self.site_url)) from e


def _explain(e: httpx.HTTPStatusError, site_url: str) -> str:
    """Traduit une erreur HTTP en phrase actionnable pour l'agence."""
    status = e.response.status_code
    if status == 401:
        return (
            "WordPress refuse les identifiants (401). Vérifiez l'identifiant et "
            "regénérez un mot de passe d'application dans Utilisateurs → Profil."
        )
    if status == 403:
        return (
            "WordPress refuse l'accès (403). Le compte existe mais n'a pas les "
            "droits d'écriture, ou un plugin de sécurité bloque l'API REST."
        )
    if status == 404:
        return (
            f"L'API REST est introuvable sur {site_url} (404). Le site n'est "
            "peut-être pas un WordPress, ou `/wp-json/` est désactivé."
        )
    detail = ""
    try:
        body = e.response.json()
        detail = body.get("message") or ""
    except Exception:
        detail = (e.response.text or "")[:200]
    return f"WordPress a répondu {status}" + (f" : {detail}" if detail else ".")
