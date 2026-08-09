"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { jobStreamUrl, retryJob } from "@/lib/api";
import type { JobProgress, PipelineStep } from "@/lib/types";

const STEP_LABELS: Record<PipelineStep, string> = {
  keyword_research: "Analyse et expansion des mots-clés",
  awaiting_validation: "En attente de votre validation",
  cocon_design: "Design des cocons sémantiques",
  serp_analysis: "Analyse des top résultats Google par article",
  article_generation: "Rédaction des articles",
  maillage: "Assemblage du maillage interne",
  backlinks: "Analyse concurrentielle backlinks",
  complete: "Terminé",
};

type StreamState =
  | { kind: "connecting" }
  | { kind: "running"; progress: JobProgress }
  | { kind: "done" }
  | { kind: "error"; message: string; traceback?: string };

export default function JobProgressPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: jobId } = use(params);
  const router = useRouter();
  const [state, setState] = useState<StreamState>({ kind: "connecting" });
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  async function handleRetry() {
    setRetrying(true);
    setRetryError(null);
    try {
      const { job_id } = await retryJob(jobId);
      router.push(`/jobs/${job_id}`);
    } catch (e) {
      setRetryError(e instanceof Error ? e.message : "Reprise impossible");
      setRetrying(false);
    }
  }

  useEffect(() => {
    const url = jobStreamUrl(jobId);
    const es = new EventSource(url);

    es.addEventListener("progress", (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data);
        if (data && typeof data === "object" && "step" in data) {
          setState({ kind: "running", progress: data as JobProgress });
        }
      } catch {
        // ignore parse errors
      }
    });

    es.addEventListener("done", () => {
      setState({ kind: "done" });
      es.close();
      // Petit délai pour laisser lire "Terminé" avant redirection
      setTimeout(() => router.push(`/jobs/${jobId}/result`), 800);
    });

    // Le run n'est pas fini : il attend un arbitrage humain sur la sélection
    // de mots-clés. On envoie directement sur l'écran de validation.
    es.addEventListener("awaiting_validation", (event) => {
      es.close();
      try {
        const { run_id } = JSON.parse((event as MessageEvent).data);
        if (run_id) {
          router.push(`/jobs/${jobId}/validation?run=${run_id}`);
          return;
        }
      } catch {
        // se rabat sur l'état ci-dessous
      }
      setState({
        kind: "error",
        message:
          "Le run attend une validation mais son identifiant est introuvable. " +
          "Reprenez depuis l'historique.",
      });
    });

    es.addEventListener("error", (event) => {
      const raw = (event as MessageEvent).data;
      if (!raw) {
        // Coupure réseau / nginx timeout — EventSource reconnecte automatiquement, on ne fait rien
        return;
      }
      try {
        const data = JSON.parse(raw);
        setState({
          kind: "error",
          message: data.error || "Erreur lors de la génération",
          traceback: data.error_traceback || undefined,
        });
      } catch {
        setState({
          kind: "error",
          message: "Erreur lors de la génération",
        });
      }
      es.close();
    });

    return () => es.close();
  }, [jobId, router]);

  const percent =
    state.kind === "running" ? state.progress.percent : state.kind === "done" ? 100 : 0;
  const currentStep = state.kind === "running" ? state.progress.step : "keyword_research";
  const message =
    state.kind === "running"
      ? state.progress.message
      : state.kind === "connecting"
        ? "Connexion au worker..."
        : state.kind === "done"
          ? "Redirection vers le résultat..."
          : state.message;

  return (
    <div className="min-h-screen">
      <header className="border-b">
        <div className="max-w-4xl mx-auto flex items-center justify-between px-6 py-4">
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
            ← Accueil
          </Link>
          <span className="text-xs text-muted-foreground font-mono">job {jobId}</span>
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-6 py-16">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-3">
              Génération en cours
              {state.kind === "done" && (
                <Badge className="bg-success text-white">Terminé</Badge>
              )}
              {state.kind === "error" && <Badge variant="destructive">Échec</Badge>}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">{message}</span>
                <span className="font-mono text-xs">{percent}%</span>
              </div>
              <Progress value={percent} />
            </div>

            <ol className="space-y-2 text-sm">
              {(Object.keys(STEP_LABELS) as PipelineStep[]).map((s) => {
                const isCurrent = s === currentStep && state.kind === "running";
                const stepOrder = Object.keys(STEP_LABELS) as PipelineStep[];
                const currentIndex = stepOrder.indexOf(currentStep);
                const stepIndex = stepOrder.indexOf(s);
                const done =
                  state.kind === "done" ||
                  (state.kind === "running" && stepIndex < currentIndex);
                return (
                  <li key={s} className="flex items-center gap-3">
                    <span
                      className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs ${
                        done
                          ? "bg-success text-white"
                          : isCurrent
                            ? "bg-primary text-primary-foreground animate-pulse"
                            : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {done ? "✓" : isCurrent ? "…" : ""}
                    </span>
                    <span
                      className={
                        done || isCurrent ? "text-foreground" : "text-muted-foreground"
                      }
                    >
                      {STEP_LABELS[s]}
                    </span>
                  </li>
                );
              })}
            </ol>

            {state.kind === "error" && (
              <Alert variant="destructive">
                <AlertDescription className="space-y-3">
                  <p>{state.message}</p>
                  {state.traceback && (
                    <details className="text-xs">
                      <summary className="cursor-pointer opacity-80 hover:opacity-100">
                        Détail technique
                      </summary>
                      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-black/10 p-2 font-mono text-[11px] leading-snug">
                        {state.traceback}
                      </pre>
                    </details>
                  )}
                </AlertDescription>
              </Alert>
            )}

            {state.kind === "error" && (
              <div className="space-y-2">
                <div className="flex flex-wrap gap-2">
                  <Button onClick={handleRetry} disabled={retrying}>
                    {retrying ? "Reprise en cours..." : "Reprendre la génération"}
                  </Button>
                  <Link
                    href="/new"
                    className={buttonVariants({ variant: "outline" })}
                  >
                    Repartir de zéro
                  </Link>
                </div>
                <p className="text-xs text-muted-foreground">
                  La reprise repart de l&apos;étape qui a échoué : les mots-clés,
                  cocons et articles déjà générés sont réutilisés, pas
                  regénérés.
                </p>
                {retryError && (
                  <p className="text-xs text-destructive">{retryError}</p>
                )}
              </div>
            )}

            {state.kind !== "error" && (
              <p className="text-xs text-muted-foreground">
                Cette page reste ouverte le temps de la génération (3-10 min).
                Tu peux fermer l&apos;onglet — le job continue en background,
                récupère l&apos;URL et reviens plus tard.
              </p>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
