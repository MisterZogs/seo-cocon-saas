"use client";

import { useEffect, useRef, useState } from "react";
import { RotateCcwIcon, TriangleAlertIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { fetchJobStatus, regenerateArticle } from "@/lib/api";

type Phase = "idle" | "editing" | "running" | "error";

/**
 * Réécriture d'UN article après lecture — second sens du Mode Brief.
 *
 * Le prix est annoncé *avant* le clic, pas après : c'est la seule action du
 * produit qui débite en dehors d'une génération, et une agence qui découvre le
 * débit après coup ouvre un ticket. Le libellé « 1/6 de cocon » vient du
 * backend, qui est seul à connaître la grille.
 *
 * À la fin, on recharge la page entière plutôt que de recoller le nouvel
 * article dans l'état local : la régénération réécrit aussi la map de maillage
 * et le coût du run, tous deux affichés ailleurs sur cet écran. Un rafraîchi-
 * ssement partiel les laisserait mentir.
 */
export function RegenerateArticle({
  runId,
  slug,
  currentDirectives,
}: {
  runId: string | null;
  slug: string;
  currentDirectives: string | null;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [directives, setDirectives] = useState(currentDirectives ?? "");
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  if (!runId) {
    // Sans run_id il n'y a rien à adresser : c'est le cas d'un job trop ancien
    // pour être rattaché à un run persisté. Mieux vaut ne rien proposer que
    // proposer un bouton qui échouera.
    return null;
  }

  async function poll(id: string) {
    try {
      const status = await fetchJobStatus(id);
      if (status.status === "finished") {
        window.location.reload();
        return;
      }
      if (status.status === "failed") {
        setError(
          status.error ??
            "La régénération a échoué. Le débit a été annulé automatiquement.",
        );
        setPhase("error");
        return;
      }
      timer.current = setTimeout(() => poll(id), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Suivi impossible");
      setPhase("error");
    }
  }

  async function start() {
    setError(null);
    setPhase("running");
    try {
      const started = await regenerateArticle(
        runId!,
        slug,
        directives.trim() || null,
      );
      setJobId(started.job_id);
      timer.current = setTimeout(() => poll(started.job_id), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Régénération impossible");
      setPhase("error");
    }
  }

  if (phase === "running") {
    return (
      <div className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">
        Réécriture en cours… L&apos;article, son maillage et le coût du run
        seront rechargés automatiquement.
        {jobId && (
          <span className="block font-mono text-xs mt-1 opacity-60">
            job {jobId.slice(0, 8)}
          </span>
        )}
      </div>
    );
  }

  if (phase === "idle" || phase === "error") {
    return (
      <div className="space-y-2">
        {error && (
          <Alert variant="destructive">
            <TriangleAlertIcon className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <Button
          size="sm"
          variant="outline"
          onClick={() => setPhase("editing")}
          className="gap-1.5"
        >
          <RotateCcwIcon className="h-3.5 w-3.5" />
          Régénérer avec de nouvelles consignes
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Consignes pour cet article
        </p>
        <Badge variant="outline" className="text-xs">
          débité 1/6 de cocon
        </Badge>
      </div>

      <Textarea
        value={directives}
        onChange={(e) => setDirectives(e.target.value)}
        maxLength={2000}
        rows={4}
        placeholder="Ex. : insister sur le coût réel d'une intervention, retirer la partie réglementaire, adopter un ton plus direct."
        className="text-sm"
      />

      <p className="text-xs text-muted-foreground">
        Éditorial uniquement — angle, emphase, ton, ce qu&apos;il faut retirer.
        Le maillage interne reste imposé par l&apos;outil : les 5 liens entrants
        et sortants de cette page seront reconstruits à l&apos;identique.
        {currentDirectives
          ? " Ces consignes remplacent les précédentes."
          : null}
      </p>

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={start}>
          Régénérer
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setDirectives(currentDirectives ?? "");
            setPhase("idle");
          }}
        >
          Annuler
        </Button>
      </div>
    </div>
  );
}
