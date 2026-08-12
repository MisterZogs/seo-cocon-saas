"use client";

import { cn } from "@/lib/utils";
import type { GEOScore } from "@/lib/types";

/**
 * Score GEO par article — cinq axes, calculés en code (`pipeline/geo_score.py`).
 *
 * Deux partis pris d'affichage, tous deux tenus par le backend :
 * 1. Un axe non mesurable s'affiche « non mesuré » et est retiré de la moyenne,
 *    jamais crédité à 100. Un score gonflé par un scrape SERP raté serait
 *    invendable le jour où l'agence recompte.
 * 2. Les corrections sont affichées en entier. C'est la partie utile du score :
 *    une note sans « quoi corriger » ne sert qu'à se rassurer.
 */
export function GeoScoreBlock({ score }: { score: GEOScore }) {
  const axes: { label: string; value: number | null; hint: string }[] = [
    {
      label: "Réponse directe",
      value: score.direct_answer,
      hint: "Le sujet est traité dans les 100 premiers mots",
    },
    {
      label: "Structure extractible",
      value: score.extractable_structure,
      hint: "Listes, tableaux, FAQ, découpage en H2",
    },
    {
      label: "Entités couvertes",
      value: score.entity_coverage,
      hint: "Entités du top 10 réellement nommées",
    },
    {
      label: "Questions reprises",
      value: score.question_coverage,
      hint: "Questions fréquentes de la SERP posées en titre",
    },
    {
      label: "Sources et chiffres",
      value: score.citations_and_data,
      hint: "Sources externes liées et données chiffrées",
    },
  ];

  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Score GEO
          </p>
          <p className="text-xs text-muted-foreground mt-0.5">
            Optimisation pour être cité par les moteurs génératifs
          </p>
        </div>
        <span
          className={cn(
            "shrink-0 rounded-md px-2 py-1 text-xs font-semibold tabular-nums",
            toneFor(score.overall),
          )}
        >
          {score.overall}/100
        </span>
      </div>

      <div className="space-y-1.5">
        {axes.map((axis) => (
          <div key={axis.label} className="flex items-center gap-2 text-xs">
            <span className="w-40 shrink-0 text-muted-foreground" title={axis.hint}>
              {axis.label}
            </span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              {axis.value !== null && (
                <div
                  className={cn("h-full rounded-full", barFor(axis.value))}
                  style={{ width: `${axis.value}%` }}
                />
              )}
            </div>
            <span
              className={cn(
                "w-16 shrink-0 text-right tabular-nums",
                axis.value === null ? "text-muted-foreground italic" : "font-medium",
              )}
            >
              {axis.value === null ? "non mesuré" : axis.value}
            </span>
          </div>
        ))}
      </div>

      {score.findings.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-muted-foreground mb-1.5">
            À corriger pour améliorer la citabilité
          </p>
          <ul className="text-xs space-y-1">
            {score.findings.map((f, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-muted-foreground">•</span>
                <span>{f}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {score.unmeasured.length > 0 && (
        <div className="border-t pt-2 space-y-1">
          {score.unmeasured.map((u, i) => (
            <p key={i} className="text-xs text-muted-foreground">
              {u}
            </p>
          ))}
        </div>
      )}

      <p className="text-xs text-muted-foreground border-t pt-2">
        Aucun moteur génératif ne publie son critère de sélection : ces cinq axes sont
        les signaux documentés, pas une formule officielle. Un score élevé est une
        optimisation vérifiée, jamais une garantie de citation.
      </p>
    </div>
  );
}

function toneFor(score: number): string {
  if (score >= 80) return "bg-success-soft text-success-strong";
  if (score >= 60) return "bg-warning-soft text-warning-strong";
  return "bg-destructive-soft text-destructive-strong";
}

function barFor(score: number): string {
  if (score >= 80) return "bg-success";
  if (score >= 60) return "bg-warning";
  return "bg-destructive";
}
