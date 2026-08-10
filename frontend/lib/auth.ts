/**
 * Session de l'agence côté navigateur.
 *
 * Le jeton est un JWT porté en `Authorization: Bearer`, stocké dans
 * `localStorage`.
 *
 * **Pourquoi pas un cookie httpOnly**, qui serait plus sûr : en production le
 * front et l'API partagent l'origine (`NEXT_PUBLIC_API_URL=/api`, Caddy fait le
 * routage), donc un cookie marcherait — et réglerait au passage le cas SSE. Mais
 * en développement le front est sur :3000 et l'API sur :8000, ce qui imposerait
 * `SameSite=None; Secure` sur du http local. Le compromis retenu : bearer +
 * localStorage, acceptable ici parce que le front ne rend jamais de HTML brut
 * (aucun `dangerouslySetInnerHTML`, le markdown des articles passe par un
 * renderer maison), donc la surface XSS qui rendrait `localStorage` dangereux
 * n'existe pas. À rebasculer sur cookie le jour où on sert du HTML tiers.
 */

const TOKEN_KEY = "cocon.token";
const AGENCY_KEY = "cocon.agency";
const EXPIRY_KEY = "cocon.expires_at";

export type Agency = {
  id: string;
  email: string;
  name: string;
};

export type Session = {
  access_token: string;
  token_type: string;
  expires_at: string;
  agency: Agency;
};

/** `localStorage` n'existe pas pendant le rendu serveur. */
function store(): Storage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

export function saveSession(session: Session): void {
  const s = store();
  if (!s) return;
  s.setItem(TOKEN_KEY, session.access_token);
  s.setItem(EXPIRY_KEY, session.expires_at);
  s.setItem(AGENCY_KEY, JSON.stringify(session.agency));
}

export function clearSession(): void {
  const s = store();
  if (!s) return;
  s.removeItem(TOKEN_KEY);
  s.removeItem(EXPIRY_KEY);
  s.removeItem(AGENCY_KEY);
}

/**
 * Jeton courant, ou `null` s'il est absent **ou expiré**.
 *
 * L'expiration est vérifiée ici pour ne pas envoyer un appel qu'on sait voué à
 * un 401 : ça évite un aller-retour et, surtout, une redirection qui arriverait
 * après l'affichage d'une page vide.
 */
export function getToken(): string | null {
  const s = store();
  if (!s) return null;

  const token = s.getItem(TOKEN_KEY);
  if (!token) return null;

  const expiresAt = s.getItem(EXPIRY_KEY);
  if (expiresAt && new Date(expiresAt).getTime() <= Date.now()) {
    clearSession();
    return null;
  }
  return token;
}

export function getAgency(): Agency | null {
  const s = store();
  const raw = s?.getItem(AGENCY_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Agency;
  } catch {
    return null;
  }
}

export function isAuthenticated(): boolean {
  return getToken() !== null;
}

/** En-têtes à fusionner dans chaque appel API authentifié. */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Session invalide → on repart de la page de connexion, en gardant en mémoire
 * la page demandée pour y revenir après.
 */
export function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  clearSession();
  const here = window.location.pathname + window.location.search;
  const next = here && here !== "/login" ? `?next=${encodeURIComponent(here)}` : "";
  window.location.href = `/login${next}`;
}
