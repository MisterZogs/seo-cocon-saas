import type {
  ClientForm,
  JobStatusResponse,
  RunSummary,
  ValidationDecision,
  ValidationSnapshot,
} from "./types";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function createGeneration(
  form: ClientForm,
): Promise<{ job_id: string; status: string }> {
  const res = await fetch(`${API_URL}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(form),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Erreur ${res.status}: ${text}`);
  }
  return res.json();
}

/**
 * Formulaire de la dernière demande soumise — sert à préremplir /new.
 *
 * `form: null` est un cas normal (base vide ou non configurée), pas une erreur :
 * le formulaire s'ouvre alors vierge.
 */
export async function fetchFormDefaults(): Promise<{
  enabled: boolean;
  form: ClientForm | null;
}> {
  const res = await fetch(`${API_URL}/form-defaults`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Valeurs par défaut indisponibles (${res.status})`);
  }
  return res.json();
}

export async function fetchJobStatus(jobId: string): Promise<JobStatusResponse> {
  const res = await fetch(`${API_URL}/jobs/${jobId}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Job introuvable (${res.status})`);
  }
  return res.json();
}

export function jobStreamUrl(jobId: string): string {
  return `${API_URL}/jobs/${jobId}/stream`;
}

/**
 * Relance un job échoué. Le backend réutilise les checkpoints du run :
 * les étapes déjà passées ne sont ni rejouées ni repayées.
 */
export async function retryJob(
  jobId: string,
): Promise<{ job_id: string; run_id: string; status: string }> {
  const res = await fetch(`${API_URL}/jobs/${jobId}/retry`, { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `Reprise impossible (${res.status})`);
  }
  return res.json();
}

/** Sélection proposée par Claude pour un run suspendu, avec le pool complet. */
export async function fetchValidation(
  runId: string,
): Promise<ValidationSnapshot> {
  const res = await fetch(`${API_URL}/runs/${runId}/validation`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `Sélection introuvable (${res.status})`);
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
  const res = await fetch(`${API_URL}/runs/${runId}/validation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    const message =
      typeof detail?.detail === "string"
        ? detail.detail
        : // FastAPI rend les erreurs de validation Pydantic sous forme de liste.
          detail?.detail?.[0]?.msg || `Validation refusée (${res.status})`;
    throw new Error(message);
  }
  return res.json();
}

export async function fetchRuns(): Promise<{
  enabled: boolean;
  runs: RunSummary[];
}> {
  const res = await fetch(`${API_URL}/runs`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Historique indisponible (${res.status})`);
  }
  return res.json();
}
