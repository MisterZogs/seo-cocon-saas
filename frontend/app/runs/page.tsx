"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AccountMenu, AuthGuard } from "@/components/auth-guard";
import { fetchRuns } from "@/lib/api";
import type { RunSummary } from "@/lib/types";

/** Où mène un run selon son état, et comment on le nomme. */
const STATUS: Record<
  RunSummary["status"],
  { label: string; badge: React.ReactNode; route: (jobId: string) => string }
> = {
  queued: {
    label: "En file d'attente",
    badge: <Badge variant="secondary">En attente</Badge>,
    route: (id) => `/jobs/${id}`,
  },
  running: {
    label: "Génération en cours",
    badge: <Badge variant="secondary">En cours</Badge>,
    route: (id) => `/jobs/${id}`,
  },
  awaiting_validation: {
    label: "En attente de votre validation",
    badge: <Badge>À valider</Badge>,
    route: (id) => `/jobs/${id}/validation`,
  },
  completed: {
    label: "Terminé",
    badge: <Badge className="bg-success text-white">Terminé</Badge>,
    route: (id) => `/jobs/${id}/result`,
  },
  failed: {
    label: "Échec",
    badge: <Badge variant="destructive">Échec</Badge>,
    route: (id) => `/jobs/${id}`,
  },
};

const DATE_FMT = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "medium",
  timeStyle: "short",
});

/** Durée d'un run, quand il est terminé. Sert à situer le coût d'un run. */
function duration(run: RunSummary): string | null {
  if (!run.ended_at) return null;
  const ms = new Date(run.ended_at).getTime() - new Date(run.created_at).getTime();
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}

type State =
  | { kind: "loading" }
  | { kind: "disabled" }
  | { kind: "error"; message: string }
  | { kind: "ready"; runs: RunSummary[] };

function RunsPage() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchRuns()
      .then((data) => {
        if (cancelled) return;
        // `enabled: false` = Postgres non configuré. Ce n'est pas une erreur et
        // ce n'est pas non plus « aucun run » : les deux méritent un message
        // distinct, sinon on cherche un bug de persistance là où il n'y en a pas.
        setState(
          data.enabled
            ? { kind: "ready", runs: data.runs }
            : { kind: "disabled" },
        );
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Erreur inconnue",
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-screen">
      <header className="border-b">
        <div className="max-w-4xl mx-auto flex items-center justify-between px-6 py-4">
          <Link
            href="/"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← Retour
          </Link>
          <div className="flex items-center gap-4">
            <span className="text-sm font-medium">Historique des générations</span>
            <AccountMenu />
          </div>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-10">
        {state.kind === "loading" && (
          <p className="text-sm text-muted-foreground">Chargement de l'historique…</p>
        )}

        {state.kind === "disabled" && (
          <Alert>
            <AlertDescription>
              La base de données n'est pas configurée sur ce serveur, donc aucun
              run n'est conservé. Renseignez <code>DATABASE_URL</code> pour
              activer l'historique.
            </AlertDescription>
          </Alert>
        )}

        {state.kind === "error" && (
          <Alert variant="destructive">
            <AlertDescription>{state.message}</AlertDescription>
          </Alert>
        )}

        {state.kind === "ready" && state.runs.length === 0 && (
          <div className="text-center py-16">
            <p className="text-muted-foreground">
              Aucune génération pour l'instant.
            </p>
            <Link href="/new" className={buttonVariants({ className: "mt-6" })}>
              Lancer une première génération
            </Link>
          </div>
        )}

        {state.kind === "ready" && state.runs.length > 0 && (
          <>
            <div className="mb-6 flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                {state.runs.length} génération
                {state.runs.length > 1 ? "s" : ""}
              </p>
              <Link href="/new" className={buttonVariants({ size: "sm" })}>
                Nouvelle génération
              </Link>
            </div>

            <ul className="space-y-3">
              {state.runs.map((run) => {
                const status = STATUS[run.status];
                const d = duration(run);
                // Un run sans job_id n'a aucune page de destination : on
                // l'affiche quand même (il a existé) mais sans lien mort.
                const href = run.job_id ? status.route(run.job_id) : null;

                const body = (
                  <Card className="transition-colors hover:border-foreground/20">
                    <CardContent className="flex flex-wrap items-center gap-x-4 gap-y-2 py-4">
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">
                          {run.project_name || "Sans nom"}
                        </p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {DATE_FMT.format(new Date(run.created_at))}
                          {d && ` · ${d}`}
                          {run.cocoons_count > 0 &&
                            ` · ${run.cocoons_count} cocon${
                              run.cocoons_count > 1 ? "s" : ""
                            }`}
                          {run.articles_count > 0 &&
                            ` · ${run.articles_count} article${
                              run.articles_count > 1 ? "s" : ""
                            }`}
                        </p>
                      </div>
                      <Badge variant="outline">
                        {run.mode === "brief" ? "Brief" : "Génération complète"}
                      </Badge>
                      {status.badge}
                    </CardContent>
                  </Card>
                );

                return (
                  <li key={run.id}>
                    {href ? (
                      <Link href={href} className="block">
                        {body}
                      </Link>
                    ) : (
                      body
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </main>
    </div>
  );
}

export default function Page() {
  return (
    <AuthGuard>
      <RunsPage />
    </AuthGuard>
  );
}
