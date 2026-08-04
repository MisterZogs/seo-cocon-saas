import type { ClientForm, JobStatusResponse, RunSummary } from "./types";

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
