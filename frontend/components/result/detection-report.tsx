"use client";

import { useEffect, useState } from "react";
import { CheckIcon, CopyIcon, ShieldAlertIcon, ShieldCheckIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { AIDetectionReport } from "@/lib/types";

/**
 * Rapport de détection IA — un argumentaire à transmettre au client final, pas
 * une mesure. Aucun détecteur tiers (Pangram, Originality.ai) n'est appelé à
 * la génération : le verdict est prédit à partir de ce que le code contrôle,
 * la présence de blocs verbatim. Voir CLAUDE.md « Détection IA ».
 */
export function DetectionReportBlock({ report }: { report: AIDetectionReport }) {
  const mixed = report.expected_verdict === "mixed";

  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Détection IA — argumentaire client
        </p>
        <Badge
          variant="outline"
          className={cn(
            "gap-1 text-xs",
            mixed
              ? "border-success-line bg-success-soft text-success-strong"
              : "border-warning-line bg-warning-soft text-warning-strong",
          )}
        >
          {mixed ? (
            <ShieldCheckIcon className="h-3 w-3" />
          ) : (
            <ShieldAlertIcon className="h-3 w-3" />
          )}
          Verdict attendu : {mixed ? "Mixte" : "IA à 100 %"}
        </Badge>
      </div>

      <p className="text-sm leading-relaxed">{report.summary}</p>

      <div>
        <p className="text-xs font-semibold text-muted-foreground mb-1.5">
          À dire au client
        </p>
        <ul className="text-xs space-y-1">
          {report.talking_points.map((point, i) => (
            <li key={i} className="flex gap-2">
              <span className="text-muted-foreground">•</span>
              <span>{point}</span>
            </li>
          ))}
        </ul>
      </div>

      {report.caveats.length > 0 && (
        <div className="text-xs text-muted-foreground border-t pt-2 space-y-1">
          {report.caveats.map((c, i) => (
            <p key={i}>{c}</p>
          ))}
        </div>
      )}

      <CopyArgumentaireButton report={report} />
    </div>
  );
}

function CopyArgumentaireButton({ report }: { report: AIDetectionReport }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(t);
  }, [copied]);

  async function copy() {
    const text = [report.summary, "", ...report.talking_points.map((p) => `- ${p}`)].join(
      "\n",
    );
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      // Silencieux : le pire résultat est de devoir sélectionner le texte à la main.
    }
  }

  return (
    <Button size="sm" variant="ghost" className="text-xs h-7" onClick={copy}>
      {copied ? (
        <>
          <CheckIcon data-icon="inline-start" className="text-success" />
          Copié
        </>
      ) : (
        <>
          <CopyIcon data-icon="inline-start" />
          Copier l&apos;argumentaire
        </>
      )}
    </Button>
  );
}
