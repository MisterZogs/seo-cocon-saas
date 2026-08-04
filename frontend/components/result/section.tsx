"use client";

import { cn } from "@/lib/utils";

/**
 * Le thème shadcn du projet est intégralement en niveaux de gris (toutes les
 * variables sont en `oklch(x 0 0)`). Sans accent, chaque titre de section
 * rendait le même gris pâle et la page se lisait comme un mur uniforme.
 *
 * On introduit donc une couleur par NATURE d'information, toujours la même
 * d'un écran à l'autre : le lecteur apprend le code une fois.
 */
export type Tone =
  | "cocon"
  | "article"
  | "maillage"
  | "serp"
  | "backlinks"
  | "faq"
  | "neutral";

export const TONE: Record<
  Tone,
  { text: string; bg: string; border: string; dot: string }
> = {
  cocon: {
    text: "text-violet-700 dark:text-violet-300",
    bg: "bg-violet-50 dark:bg-violet-950/40",
    border: "border-violet-200 dark:border-violet-900",
    dot: "bg-violet-500",
  },
  article: {
    text: "text-sky-700 dark:text-sky-300",
    bg: "bg-sky-50 dark:bg-sky-950/40",
    border: "border-sky-200 dark:border-sky-900",
    dot: "bg-sky-500",
  },
  maillage: {
    text: "text-indigo-700 dark:text-indigo-300",
    bg: "bg-indigo-50 dark:bg-indigo-950/40",
    border: "border-indigo-200 dark:border-indigo-900",
    dot: "bg-indigo-500",
  },
  serp: {
    text: "text-teal-700 dark:text-teal-300",
    bg: "bg-teal-50 dark:bg-teal-950/40",
    border: "border-teal-200 dark:border-teal-900",
    dot: "bg-teal-500",
  },
  backlinks: {
    text: "text-amber-700 dark:text-amber-300",
    bg: "bg-amber-50 dark:bg-amber-950/40",
    border: "border-amber-200 dark:border-amber-900",
    dot: "bg-amber-500",
  },
  faq: {
    text: "text-rose-700 dark:text-rose-300",
    bg: "bg-rose-50 dark:bg-rose-950/40",
    border: "border-rose-200 dark:border-rose-900",
    dot: "bg-rose-500",
  },
  neutral: {
    text: "text-foreground",
    bg: "bg-muted/50",
    border: "border-border",
    dot: "bg-muted-foreground",
  },
};

/** Titre de bloc à l'intérieur d'une carte — le niveau le plus courant. */
export function SectionHeading({
  tone = "neutral",
  children,
  count,
  hint,
  className,
}: {
  tone?: Tone;
  children: React.ReactNode;
  count?: number;
  hint?: string;
  className?: string;
}) {
  const t = TONE[tone];
  return (
    <div className={cn("flex items-baseline gap-2 mb-3", className)}>
      <span className={cn("size-2 rounded-full shrink-0", t.dot)} aria-hidden />
      <h4 className={cn("text-sm font-bold tracking-tight", t.text)}>
        {children}
        {count !== undefined && (
          <span className="ml-1.5 font-semibold opacity-70">({count})</span>
        )}
      </h4>
      {hint && (
        <span className="text-xs text-muted-foreground font-normal">{hint}</span>
      )}
    </div>
  );
}

/** Titre de groupe, au-dessus d'une liste de cartes. */
export function GroupHeading({
  tone = "neutral",
  children,
  count,
}: {
  tone?: Tone;
  children: React.ReactNode;
  count?: number;
}) {
  const t = TONE[tone];
  return (
    <div className="flex items-center gap-2.5">
      <span className={cn("h-5 w-1 rounded-full", t.dot)} aria-hidden />
      <h3 className={cn("text-base font-bold tracking-tight", t.text)}>
        {children}
        {count !== undefined && (
          <span className="ml-1.5 opacity-70">({count})</span>
        )}
      </h3>
    </div>
  );
}
