import type {
  BalanceResponse,
  BillingOffers,
  ClientForm,
  JobStatusResponse,
  LedgerEntry,
  RegenerationStarted,
  RunSummary,
  ValidationDecision,
  ValidationSnapshot,
  WordPressCredentials,
  WordPressExportReport,
} from "./types";
import {
  authHeaders,
  getToken,
  redirectToLogin,
  type Session,
} from "./auth";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * `fetch` + jeton d'agence + traitement uniforme du 401.
 *
 * Le 401 est le seul statut traité ici plutôt que remonté à l'appelant : une
 * session expirée n'est pas une erreur métier que la page saurait afficher
 * utilement, et la laisser passer produirait un message d'erreur technique là
 * où l'utilisateur doit simplement se reconnecter.
 */
async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: { ...authHeaders(), ...(init.headers || {}) },
  });

  if (res.status === 401) {
    redirectToLogin();
    throw new Error("Session expirée — reconnectez-vous.");
  }
  return res;
}

/** Message d'erreur du backend, quel que soit le format de la réponse. */
async function errorMessage(res: Response, fallback: string): Promise<string> {
  const detail = await res.json().catch(() => null);
  if (typeof detail?.detail === "string") return detail.detail;
  // FastAPI rend les erreurs de validation Pydantic sous forme de liste.
  return detail?.detail?.[0]?.msg || fallback;
}

// ============================================================
// Authentification
// ============================================================

export async function register(payload: {
  email: string;
  password: string;
  name: string;
}): Promise<Session> {
  const res = await fetch(`${API_URL}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Inscription refusée (${res.status})`));
  }
  return res.json();
}

export async function login(payload: {
  email: string;
  password: string;
}): Promise<Session> {
  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Connexion refusée (${res.status})`));
  }
  return res.json();
}

// ============================================================
// Pipeline
// ============================================================

/** Solde insuffisant (402) — distingué pour pouvoir proposer d'acheter. */
export class InsufficientBalanceError extends Error {
  readonly requiredUnits: number;
  readonly availableUnits: number;

  constructor(message: string, requiredUnits: number, availableUnits: number) {
    super(message);
    this.name = "InsufficientBalanceError";
    this.requiredUnits = requiredUnits;
    this.availableUnits = availableUnits;
  }
}

export async function createGeneration(
  form: ClientForm,
): Promise<{ job_id: string; run_id: string; status: string }> {
  const res = await apiFetch("/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(form),
  });
  if (res.status === 402) {
    const detail = await res.json().catch(() => null);
    throw new InsufficientBalanceError(
      detail?.detail || "Solde de cocons insuffisant.",
      detail?.required_units ?? 0,
      detail?.available_units ?? 0,
    );
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Erreur ${res.status}: ${text}`);
  }
  return res.json();
}

// ============================================================
// Facturation
// ============================================================

export async function fetchBalance(): Promise<BalanceResponse> {
  const res = await apiFetch("/billing/balance");
  if (!res.ok) {
    throw new Error(`Solde indisponible (${res.status})`);
  }
  return res.json();
}

export async function fetchLedger(): Promise<{
  entries: LedgerEntry[];
  units_per_cocoon: number;
}> {
  const res = await apiFetch("/billing/ledger");
  if (!res.ok) {
    throw new Error(`Journal indisponible (${res.status})`);
  }
  return res.json();
}

export async function fetchOffers(): Promise<BillingOffers> {
  const res = await apiFetch("/billing/offers");
  if (!res.ok) {
    throw new Error(`Formules indisponibles (${res.status})`);
  }
  return res.json();
}

/**
 * Ouvre le paiement Stripe. `plan` pour un abonnement, `cocoons` pour un achat
 * à l'unité — jamais les deux, le backend refuse (422).
 *
 * Renvoie l'URL au lieu de rediriger : c'est à l'appelant de décider quand
 * quitter la page, et ça reste testable.
 */
export async function startCheckout(
  target: { plan: string } | { cocoons: number },
): Promise<string> {
  const res = await apiFetch("/billing/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(target),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Paiement indisponible (${res.status})`));
  }
  return (await res.json()).url;
}

/** Portail Stripe : moyens de paiement, factures, résiliation. */
export async function openPortal(): Promise<string> {
  const res = await apiFetch("/billing/portal", { method: "POST" });
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Portail indisponible (${res.status})`));
  }
  return (await res.json()).url;
}

/**
 * Formulaire de la dernière demande soumise par l'agence — sert à préremplir /new.
 *
 * `form: null` est un cas normal (base vide ou non configurée), pas une erreur :
 * le formulaire s'ouvre alors vierge.
 */
export async function fetchFormDefaults(): Promise<{
  enabled: boolean;
  form: ClientForm | null;
}> {
  const res = await apiFetch("/form-defaults");
  if (!res.ok) {
    throw new Error(`Valeurs par défaut indisponibles (${res.status})`);
  }
  return res.json();
}

export async function fetchJobStatus(jobId: string): Promise<JobStatusResponse> {
  const res = await apiFetch(`/jobs/${jobId}`);
  if (!res.ok) {
    throw new Error(`Job introuvable (${res.status})`);
  }
  return res.json();
}

/**
 * URL du flux SSE, jeton compris.
 *
 * `EventSource` ne sait pas poser d'en-tête `Authorization` — c'est une limite
 * de l'API navigateur. Le backend n'accepte le jeton en paramètre d'URL que sur
 * cette route, et uniquement pour cette raison (cf. backend/auth.py).
 */
export function jobStreamUrl(jobId: string): string {
  const token = getToken();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${API_URL}/jobs/${jobId}/stream${query}`;
}

/**
 * Relance un job échoué. Le backend réutilise les checkpoints du run :
 * les étapes déjà passées ne sont ni rejouées ni repayées.
 */
export async function retryJob(
  jobId: string,
): Promise<{ job_id: string; run_id: string; status: string }> {
  const res = await apiFetch(`/jobs/${jobId}/retry`, { method: "POST" });
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Reprise impossible (${res.status})`));
  }
  return res.json();
}

/** Sélection proposée par Claude pour un run suspendu, avec le pool complet. */
export async function fetchValidation(
  runId: string,
): Promise<ValidationSnapshot> {
  const res = await apiFetch(`/runs/${runId}/validation`);
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Sélection introuvable (${res.status})`));
  }
  return res.json();
}

/**
 * Envoie la sélection arrêtée par l'agence. Le backend la fige en checkpoint
 * `cocon_design` et relance le run : la recherche de mots-clés n'est pas repayée.
 */
export async function submitValidation(
  runId: string,
  decision: ValidationDecision,
): Promise<{
  job_id: string;
  run_id: string;
  status: string;
  cocoons: number;
  articles: number;
}> {
  const res = await apiFetch(`/runs/${runId}/validation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Validation refusée (${res.status})`));
  }
  return res.json();
}

/**
 * Réécrit UN article du livrable avec de nouvelles consignes. Débité 1/6 de cocon.
 *
 * Ce n'est pas `retryJob` : une reprise répare un échec technique et ne se
 * facture pas, une régénération est un travail commandé après lecture. Le
 * backend refuse (402) si le solde ne couvre pas l'article.
 */
export async function regenerateArticle(
  runId: string,
  slug: string,
  directives: string | null,
): Promise<RegenerationStarted> {
  const res = await apiFetch(`/runs/${runId}/articles/${slug}/regenerate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ directives }),
  });
  if (!res.ok) {
    throw new Error(
      await errorMessage(res, `Régénération impossible (${res.status})`),
    );
  }
  return res.json();
}

/**
 * Générateur public — structure d'un cocon à partir d'un seul mot-clé.
 *
 * N'utilise **pas** `apiFetch` : cette route est la seule sans authentification,
 * et y envoyer un jeton n'aurait aucun sens. Surtout, `apiFetch` redirige vers
 * la connexion sur un 401 — comportement correct partout ailleurs, absurde sur
 * une page dont l'intérêt est justement qu'on y arrive sans compte.
 */
export async function previewCocon(
  keyword: string,
  context?: string,
): Promise<CoconPreviewResponse> {
  const res = await fetch(`${API_URL}/public/cocon-preview`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keyword, context: context ?? null }),
  });
  if (!res.ok) {
    throw new Error(
      await errorMessage(res, `Génération impossible (${res.status})`),
    );
  }
  return res.json();
}

/**
 * Publie le livrable dans le WordPress du client. Gratuit — l'agence a déjà
 * payé la génération.
 *
 * Synchrone côté serveur, donc l'appel peut durer quelques secondes : les
 * identifiants ne doivent transiter par aucune file d'attente, ils ne survivent
 * pas à la requête. Ne jamais les mettre en cache ici non plus.
 */
export async function exportToWordPress(
  runId: string,
  credentials: WordPressCredentials,
  options: { cocon_ids?: string[]; status?: string } = {},
): Promise<WordPressExportReport> {
  const res = await apiFetch(`/runs/${runId}/export/wordpress`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ credentials, ...options }),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Export impossible (${res.status})`));
  }
  return res.json();
}

export async function fetchRuns(): Promise<{
  enabled: boolean;
  runs: RunSummary[];
}> {
  const res = await apiFetch("/runs");
  if (!res.ok) {
    throw new Error(`Historique indisponible (${res.status})`);
  }
  return res.json();
}
