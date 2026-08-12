"""Chantier 14 : audit du maillage interne d'un site EXISTANT.

Le pont vers les clients déjà en portefeuille des agences. Au lieu de construire
un cocon neuf, on mesure ce qu'un site en ligne fait réellement de ses liens
internes : pages orphelines, culs-de-sac, profondeur de clic, réciprocité.

**Entièrement déterministe, aucun appel LLM, aucun coût marginal.** C'est le
même argument que le maillage généré : chaque chiffre du rapport se recompte à
la main sur le site du client. Un audit sémantique « voici les cocons qui vous
manquent » demanderait un modèle et resterait discutable ; le graphe de liens,
lui, est un fait.

⚠️ La justesse de tout le rapport repose sur la **normalisation des URL**. Si
`/page` et `/page/` comptent pour deux nœuds, la moitié du site devient orpheline
et le rapport est bon à jeter. C'est la partie la plus testée du module.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
from collections import Counter, defaultdict, deque
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from models import PageLinkStats, SiteAuditReport, SiteAuditRequest

logger = logging.getLogger(__name__)

# Réutilise la politesse déjà réglée pour le scrape SERP : mêmes en-têtes, même
# ordre de grandeur de timeout. Pas de raison d'avoir deux comportements réseau.
from pipeline.serp_analyzer import SCRAPE_HEADERS, SCRAPE_TIMEOUT  # noqa: E402

MAX_CONCURRENT = 5
MAX_BYTES = 800_000
SITEMAP_MAX_BYTES = 10_000_000

# Extensions qui ne sont pas des pages : les suivre gonflerait le graphe de
# nœuds qui n'ont ni titre ni lien sortant, donc autant de faux culs-de-sac.
_NON_PAGE_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".avif",
    ".pdf", ".zip", ".gz", ".rar", ".7z", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv", ".mp3", ".mp4", ".avi", ".mov", ".webm", ".wav",
    ".css", ".js", ".json", ".xml", ".rss", ".atom", ".woff", ".woff2", ".ttf",
)

_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "sms:", "ftp:")

# Paramètres de suivi : ils ne changent pas la page servie, mais font compter
# deux fois la même URL. `?page=2` en revanche est une vraie page distincte —
# on ne retire donc que ce dont on est sûr.
_TRACKING_PARAMS = re.compile(
    r"^(utm_[a-z_]+|gclid|fbclid|msclkid|mc_[a-z]+|_hs[a-z]+|ref|referrer|igshid)$",
    re.IGNORECASE,
)


# ============================================================
# NORMALISATION D'URL — la base de tout le reste
# ============================================================


def normalize_url(url: str, *, base: str | None = None) -> str | None:
    """Forme canonique d'une URL, ou None si ce n'est pas une page du web.

    Les règles appliquées, et pourquoi :
    - **schéma et hôte en minuscules** : la casse n'y est pas signifiante,
      contrairement au chemin (un serveur Unix distingue `/A` de `/a`).
    - **fragment retiré** : `/p#section` et `/p` sont la même page. Ne pas le
      faire multiplierait les nœuds par le nombre d'ancres de sommaire.
    - **`www.` retiré** : beaucoup de sites lient les deux formes ; les garder
      distinctes casserait le graphe en deux moitiés qui ne se lient jamais.
    - **paramètres de suivi retirés**, les autres conservés et **triés** :
      `?a=1&b=2` et `?b=2&a=1` sont la même page, mais `?page=2` n'est pas
      `?page=3`. On ne jette que ce dont on est certain.
    - **slash final retiré**, sauf à la racine : `/blog` et `/blog/` sont la
      même page pour tout CMS courant. C'est LE piège qui fabrique des
      orphelines fantômes.
    - **`/index.html` et consorts retirés** : même page que le répertoire.
    """
    if not url:
        return None
    raw = url.strip()
    if not raw or raw.startswith("#"):
        return None
    if raw.lower().startswith(_SKIP_SCHEMES):
        return None

    if base:
        raw = urljoin(base, raw)

    raw, _ = urldefrag(raw)
    parts = urlparse(raw)

    if parts.scheme not in ("http", "https"):
        return None
    if not parts.netloc:
        return None

    host = parts.netloc.lower()
    # Le port par défaut est redondant et casserait la comparaison d'hôtes.
    if host.endswith(":80") and parts.scheme == "http":
        host = host[:-3]
    elif host.endswith(":443") and parts.scheme == "https":
        host = host[:-4]
    if host.startswith("www."):
        host = host[4:]

    path = parts.path or "/"
    for index in ("index.html", "index.htm", "index.php", "default.html"):
        if path.endswith("/" + index):
            path = path[: -len(index)]
            break
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    if not path.startswith("/"):
        path = "/" + path

    if path.lower().endswith(_NON_PAGE_SUFFIXES):
        return None

    query = ""
    if parts.query:
        kept = [
            kv
            for kv in parts.query.split("&")
            if kv and not _TRACKING_PARAMS.match(kv.split("=", 1)[0])
        ]
        query = "&".join(sorted(kept))

    return urlunparse((parts.scheme, host, path, "", query, ""))


def same_site(url: str, root: str) -> bool:
    """Même hôte que la racine (normalisation faite, donc `www.` déjà retiré).

    Les sous-domaines sont exclus : `blog.exemple.fr` est un autre site aux yeux
    du maillage interne, et l'inclure ferait exploser le périmètre du crawl.
    """
    a, b = urlparse(url), urlparse(root)
    return a.netloc == b.netloc


# ============================================================
# SITEMAP
# ============================================================


def _decode_sitemap(content: bytes) -> str:
    """Un sitemap peut être servi en .xml.gz, y compris sans en-tête gzip."""
    if content[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(content).decode("utf-8", errors="replace")
        except OSError:
            pass
    return content.decode("utf-8", errors="replace")


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """(URLs de pages, URLs de sous-sitemaps).

    Un `<sitemapindex>` ne contient pas de pages mais d'autres sitemaps : ne pas
    les distinguer donnerait un site à 3 URLs, toutes des fichiers XML.
    """
    soup = BeautifulSoup(xml, "xml")
    nested = [
        loc.get_text(strip=True)
        for sm in soup.find_all("sitemap")
        if (loc := sm.find("loc")) is not None
    ]
    pages = [
        loc.get_text(strip=True)
        for u in soup.find_all("url")
        if (loc := u.find("loc")) is not None
    ]
    # Repli : certains sitemaps mal formés n'utilisent ni <url> ni <sitemap>.
    if not pages and not nested:
        pages = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
    return pages, nested


async def discover_sitemap_urls(
    client: httpx.AsyncClient, root: str, *, max_pages: int
) -> tuple[list[str], str | None]:
    """Trouve le sitemap et en extrait les URLs. Suit les index imbriqués.

    Rend `([], None)` si aucun sitemap n'est exploitable — l'appelant se
    rabattra alors sur un crawl depuis la page d'accueil.
    """
    candidates: list[str] = []
    start = normalize_url(root)
    if start and start.lower().endswith((".xml", ".xml.gz")):
        candidates.append(start)
    else:
        # robots.txt fait autorité sur l'emplacement réel du sitemap ; les
        # chemins conventionnels ne sont qu'un repli.
        try:
            r = await client.get(urljoin(root, "/robots.txt"))
            if r.status_code < 400:
                candidates += re.findall(
                    r"(?im)^\s*sitemap:\s*(\S+)\s*$", r.text
                )
        except httpx.HTTPError:
            pass
        candidates += [
            urljoin(root, "/sitemap.xml"),
            urljoin(root, "/sitemap_index.xml"),
            urljoin(root, "/wp-sitemap.xml"),
        ]

    seen_sitemaps: set[str] = set()
    queue = deque(candidates)
    found: list[str] = []
    used: str | None = None

    while queue and len(found) < max_pages:
        sm_url = queue.popleft()
        norm = normalize_url(sm_url)
        if not norm or norm in seen_sitemaps:
            continue
        seen_sitemaps.add(norm)
        if len(seen_sitemaps) > 50:  # garde-fou : index circulaire
            break
        try:
            r = await client.get(sm_url)
            if r.status_code >= 400:
                continue
            xml = _decode_sitemap(r.content[:SITEMAP_MAX_BYTES])
        except (httpx.HTTPError, UnicodeDecodeError):
            continue

        pages, nested = parse_sitemap(xml)
        if pages or nested:
            used = used or sm_url
        found += pages
        queue.extend(nested)

    return found, used


# ============================================================
# CRAWL
# ============================================================


class _Crawler:
    def __init__(self, root: str, *, respect_robots: bool) -> None:
        self.root = root
        self.respect_robots = respect_robots
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._robots: RobotFileParser | None = None

    async def load_robots(self, client: httpx.AsyncClient) -> None:
        if not self.respect_robots:
            return
        parser = RobotFileParser()
        try:
            r = await client.get(urljoin(self.root, "/robots.txt"))
            parser.parse(r.text.splitlines() if r.status_code < 400 else [])
        except httpx.HTTPError:
            # Pas de robots.txt lisible = pas d'interdiction connue. On ne
            # bloque pas l'audit pour ça, mais on le note.
            parser.parse([])
        self._robots = parser

    def allowed(self, url: str) -> bool:
        if self._robots is None:
            return True
        return self._robots.can_fetch(SCRAPE_HEADERS["User-Agent"], url)

    async def fetch(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[str | None, list[str], str | None, int, str | None]:
        """(titre, liens internes normalisés, motif d'échec, nb de mots, url finale)."""
        async with self._semaphore:
            try:
                r = await client.get(url)
            except httpx.TimeoutException:
                return None, [], "timeout", 0, None
            except httpx.HTTPError as e:
                return None, [], f"réseau {type(e).__name__}", 0, None

            if r.status_code >= 400:
                return None, [], f"HTTP {r.status_code}", 0, None
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype.lower():
                return None, [], f"type {ctype.split(';')[0] or 'inconnu'}", 0, None
            html = r.text[:MAX_BYTES]
            # Une redirection change l'identité du nœud : compter l'URL demandée
            # ferait deux nœuds pour une seule page, dont un orphelin.
            final = normalize_url(str(r.url))

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception as e:
            return None, [], f"parse {type(e).__name__}", 0, None

        title = soup.title.get_text(strip=True) if soup.title else None

        links: list[str] = []
        for a in soup.find_all("a", href=True):
            target = normalize_url(a["href"], base=final or url)
            if target and same_site(target, self.root):
                links.append(target)

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        words = len(soup.get_text(separator=" ", strip=True).split())

        return (title[:300] if title else None), links, None, words, final


# ============================================================
# AUDIT
# ============================================================


def build_report(
    *,
    start_url: str,
    sitemap_url: str | None,
    pages_discovered: int,
    truncated: bool,
    titles: dict[str, str | None],
    words: dict[str, int],
    outgoing: dict[str, set[str]],
    failed: dict[str, int],
    failed_urls: dict[str, str] | None = None,
) -> SiteAuditReport:
    """Calcule les métriques du graphe. Fonction pure — testable sans réseau.

    `outgoing` ne contient que les liens dont la CIBLE a été crawlée : un lien
    vers une page hors périmètre (ou en échec) n'est pas un lien interne
    mesurable, et le compter fausserait la réciprocité.
    """
    known = set(outgoing)
    inbound: Counter[str] = Counter()
    for source, targets in outgoing.items():
        for t in targets:
            inbound[t] += 1

    total_links = sum(len(t) for t in outgoing.values())

    # Réciprocité : part des liens A→B pour lesquels B→A existe aussi. Sur un
    # cocon conforme elle vaut 1 entre sœurs ; sur un site ordinaire elle est
    # basse, et c'est justement le contraste qui se vend.
    reciprocal = sum(
        1
        for source, targets in outgoing.items()
        for t in targets
        if source in outgoing.get(t, ())
    )
    reciprocity = reciprocal / total_links if total_links else 0.0

    # Profondeur de clic depuis la page d'entrée, en largeur d'abord.
    root = normalize_url(start_url)
    entry = root if root in known else None
    if entry is None:
        # Le sitemap peut ne pas contenir la racine. On repart de la page la
        # moins profonde disponible plutôt que de rendre tout inatteignable.
        entry = min(known, key=lambda u: (len(urlparse(u).path), u), default=None)

    depth: dict[str, int] = {}
    if entry is not None:
        depth[entry] = 0
        queue = deque([entry])
        while queue:
            current = queue.popleft()
            for nxt in outgoing.get(current, ()):  # noqa: SIM118
                if nxt not in depth:
                    depth[nxt] = depth[current] + 1
                    queue.append(nxt)

    orphans = sorted(u for u in known if inbound[u] == 0 and u != entry)
    dead_ends = sorted(u for u in known if not outgoing[u])
    unreachable = sorted(u for u in known if u not in depth)

    distribution: dict[str, int] = defaultdict(int)
    for d in depth.values():
        distribution[str(d)] += 1

    pages = sorted(
        (
            PageLinkStats(
                url=u,
                title=titles.get(u),
                inbound=inbound[u],
                outbound=len(outgoing[u]),
                depth=depth.get(u),
                word_count=words.get(u),
            )
            for u in known
        ),
        key=lambda p: (p.inbound, p.outbound),
    )

    n = len(known)
    report = SiteAuditReport(
        start_url=start_url,
        sitemap_url=sitemap_url,
        pages_discovered=pages_discovered,
        pages_crawled=n,
        pages_failed=failed,
        truncated=truncated,
        total_internal_links=total_links,
        orphans=orphans,
        dead_ends=dead_ends,
        unreachable=unreachable,
        reciprocity_rate=round(reciprocity, 4),
        avg_inbound=round(sum(inbound[u] for u in known) / n, 2) if n else 0.0,
        avg_outbound=round(total_links / n, 2) if n else 0.0,
        depth_distribution=dict(sorted(distribution.items(), key=lambda kv: int(kv[0]))),
        pages=pages,
    )
    report.findings = _findings(report)
    return report


def _findings(r: SiteAuditReport) -> list[str]:
    """Ce qui cloche, dit à l'agence — chiffré, jamais qualitatif."""
    out: list[str] = []
    n = r.pages_crawled or 1

    if r.orphans:
        pct = round(len(r.orphans) * 100 / n)
        out.append(
            f"{len(r.orphans)} page(s) orpheline(s) ({pct} % du site) : aucun lien "
            "interne ne pointe vers elles. Google les découvre par le sitemap au "
            "mieux, et ne leur transmet aucune autorité."
        )
    if r.unreachable:
        extra = len(set(r.unreachable) - set(r.orphans))
        if extra:
            out.append(
                f"{len(r.unreachable)} page(s) inatteignable(s) en suivant les liens "
                f"depuis l'accueil, dont {extra} qui reçoivent pourtant un lien — "
                "leur seul référent est lui-même inatteignable."
            )
    if r.dead_ends:
        out.append(
            f"{len(r.dead_ends)} page(s) sans aucun lien interne sortant : elles "
            "reçoivent de l'autorité et n'en redistribuent aucune."
        )
    if r.reciprocity_rate < 0.2 and r.total_internal_links:
        out.append(
            f"Réciprocité des liens à {r.reciprocity_rate:.0%} : le maillage est "
            "presque entièrement descendant. Dans un cocon, les pages sœurs se "
            "lient mutuellement — c'est ce qui répartit l'autorité au lieu de la "
            "faire ruisseler dans un seul sens."
        )
    profond = sum(v for k, v in r.depth_distribution.items() if int(k) >= 4)
    if profond:
        out.append(
            f"{profond} page(s) à 4 clics ou plus de l'accueil. Au-delà de 3, la "
            "fréquence de crawl chute nettement."
        )
    if r.avg_outbound < 3 and r.pages_crawled >= 10:
        out.append(
            f"Moyenne de {r.avg_outbound} lien(s) interne(s) sortant(s) par page : "
            "trop peu pour construire une structure thématique."
        )
    if r.truncated:
        out.append(
            f"Audit arrêté au plafond de {r.pages_crawled} pages : les compteurs "
            "portent sur cet échantillon, pas sur le site entier."
        )
    if not out:
        out.append(
            "Aucun défaut structurel majeur détecté sur le périmètre audité : "
            "ni orpheline, ni cul-de-sac, ni page trop profonde."
        )
    return out


async def audit_site(
    request: SiteAuditRequest,
    *,
    on_progress=None,
) -> SiteAuditReport:
    """Point d'entrée : découvre, crawle, mesure. Aucun appel LLM."""
    root = normalize_url(request.start_url)
    if root is None:
        raise ValueError(f"URL invalide : {request.start_url!r}")

    crawler = _Crawler(root, respect_robots=request.respect_robots)

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=SCRAPE_TIMEOUT, headers=SCRAPE_HEADERS
    ) as client:
        await crawler.load_robots(client)

        discovered, sitemap_url = await discover_sitemap_urls(
            client, root, max_pages=request.max_pages
        )
        normalized = []
        seen: set[str] = set()
        for u in discovered:
            nu = normalize_url(u)
            if nu and same_site(nu, root) and nu not in seen:
                seen.add(nu)
                normalized.append(nu)

        pages_discovered = len(normalized)
        # Sans sitemap exploitable, on part de l'accueil : le crawl découvrira le
        # site par les liens. C'est plus lent mais ça reste un audit valide.
        targets = normalized[: request.max_pages] or [root]
        truncated = pages_discovered > request.max_pages

        titles: dict[str, str | None] = {}
        words: dict[str, int] = {}
        raw_links: dict[str, list[str]] = {}
        failed: Counter[str] = Counter()

        queue = deque(targets)
        queued: set[str] = set(targets)
        done = 0

        while queue and len(titles) < request.max_pages:
            batch = [queue.popleft() for _ in range(min(MAX_CONCURRENT, len(queue)))]
            batch = [u for u in batch if crawler.allowed(u)]
            if not batch:
                failed["interdit par robots.txt"] += 1
                continue

            results = await asyncio.gather(
                *(crawler.fetch(client, u) for u in batch), return_exceptions=True
            )
            for url, res in zip(batch, results):
                done += 1
                if isinstance(res, Exception):
                    failed[f"exception {type(res).__name__}"] += 1
                    continue
                title, links, reason, wc, final = res
                if reason:
                    failed[reason] += 1
                    continue
                key = final or url
                titles[key] = title
                words[key] = wc
                raw_links[key] = links

                # Sans sitemap, le crawl s'étend par les liens découverts.
                if not normalized:
                    for l in links:
                        if l not in queued and len(queued) < request.max_pages:
                            queued.add(l)
                            queue.append(l)

            if on_progress:
                await on_progress(done, len(queued))

    crawled = set(titles)
    outgoing: dict[str, set[str]] = {
        u: {t for t in raw_links.get(u, ()) if t in crawled and t != u} for u in crawled
    }

    logger.info(
        "Audit %s — %d pages crawlées, %d liens internes, %d orphelines",
        root,
        len(crawled),
        sum(len(v) for v in outgoing.values()),
        sum(1 for u in crawled if not any(u in t for t in outgoing.values())),
    )

    return build_report(
        start_url=root,
        sitemap_url=sitemap_url,
        pages_discovered=pages_discovered or len(crawled),
        truncated=truncated,
        titles=titles,
        words=words,
        outgoing=outgoing,
        failed=dict(failed),
    )
