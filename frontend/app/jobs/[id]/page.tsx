"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { jobStreamUrl } from "@/lib/api";
import type { JobProgress, PipelineStep } from "@/lib/types";

const STEP_LABELS: Record<PipelineStep, string> = {
  keyword_research: "Analyse et expansion des mots-clés",
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
  | { kind: "error"; message: string };

export default function JobProgressPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: jobId } = use(params);
  const router = useRouter();
  const [state, setState] = useState<StreamState>({ kind: "connecting" });

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

    es.addEventListener("error", (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data ?? "{}");
        setState({
          kind: "error",
          message: data.error || "Erreur lors de la génération",
        });
      } catch {
        setState({
          kind: "error",
          message: "Connexion au backend perdue",
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
                <Badge className="bg-green-600">Terminé</Badge>
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
                          ? "bg-green-600 text-white"
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
                <AlertDescription>{state.message}</AlertDescription>
              </Alert>
            )}

            {state.kind === "error" && (
              <div className="flex gap-2">
                <Link
                  href="/new"
                  className={buttonVariants({ variant: "outline" })}
                >
                  Recommencer
                </Link>
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
