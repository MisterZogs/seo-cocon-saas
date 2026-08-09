"use client";

import { cn } from "@/lib/utils";

/**
 * Une couleur par NATURE d'information, toujours la même d'un écran à l'autre :
 * le lecteur apprend le code une fois. Sans ça, chaque titre de section rendait
 * pareil et la page se lisait comme un mur uniforme.
 *
 * Les six teintes sont définies dans `globals.css` (`--tone-*`) : désaturées et
 * ramenées vers le chaud pour tenir dans la palette crème/terracotta. Ne pas
 * revenir à des couleurs Tailwind brutes, elles trouent le fond.
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
    text: "text-tone-cocon",
    bg: "bg-tone-cocon-soft",
    border: "border-tone-cocon-line",
    dot: "bg-tone-cocon",
  },
  article: {
    text: "text-tone-article",
    bg: "bg-tone-article-soft",
    border: "border-tone-article-line",
    dot: "bg-tone-article",
  },
  maillage: {
    text: "text-tone-maillage",
    bg: "bg-tone-maillage-soft",
    border: "border-tone-maillage-line",
    dot: "bg-tone-maillage",
  },
  serp: {
    text: "text-tone-serp",
    bg: "bg-tone-serp-soft",
    border: "border-tone-serp-line",
    dot: "bg-tone-serp",
  },
  backlinks: {
    text: "text-tone-backlinks",
    bg: "bg-tone-backlinks-soft",
    border: "border-tone-backlinks-line",
    dot: "bg-tone-backlinks",
  },
  faq: {
    text: "text-tone-faq",
    bg: "bg-tone-faq-soft",
    border: "border-tone-faq-line",
    dot: "bg-tone-faq",
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
