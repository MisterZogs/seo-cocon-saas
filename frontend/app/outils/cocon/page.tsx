"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRightIcon, TriangleAlertIcon } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { previewCocon } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CoconPreviewResponse } from "@/lib/types";

/**
 * Générateur public — sans compte, sans carte, sans email.
 *
 * L'écran est construit autour d'une seule idée : rendre la promesse
 * **vérifiable sur place**. Le marché du SEO ne vend que des promesses
 * invérifiables (« contenu optimisé », « maillage intelligent »). Ici le
 * visiteur compte les liens lui-même, page par page. C'est le seul argument du
 * produit qui se démontre en dix secondes, donc c'est lui qu'on met en vitrine.
 *
 * Aucun `AuthGuard` : cette page doit s'ouvrir depuis un lien LinkedIn sans
 * autre friction que le mot-clé à taper.
 */
export default function OutilCoconPage() {
  const [keyword, setKeyword] = useState("");
  const [context, setContext] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CoconPreviewResponse | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!keyword.trim() || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await previewCocon(keyword.trim(), context.trim() || undefined));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Génération impossible");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link href="/" className="font-serif text-xl font-semibold tracking-tight">
            Cocon<span className="text-primary">.</span>
          </Link>
          <Link
            href="/login"
            className={cn(buttonVariants({ size: "sm" }), "h-9 px-4 font-semibold")}
          >
            Essai gratuit — 3 cocons
          </Link>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-12 space-y-10">
        <section className="space-y-4 text-center">
          <Badge variant="secondary" className="font-medium">
            Gratuit · sans inscription
          </Badge>
          <h1 className="font-serif text-4xl font-semibold tracking-tight sm:text-5xl">
            Générateur de cocon sémantique
          </h1>
          <p className="mx-auto max-w-2xl text-muted-foreground">
            Un mot-clé, et vous obtenez l&apos;arborescence complète d&apos;un
            cocon avec son plan de maillage interne. Pas un schéma indicatif :
            les liens sont calculés par le même code que notre outil payant, et
            vous pouvez les compter.
          </p>
        </section>

        <Card>
          <CardContent className="pt-6">
            <form onSubmit={submit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="kw">Mot-clé principal</Label>
                <Input
                  id="kw"
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                  placeholder="composteur de jardin"
                  maxLength={120}
                  autoFocus
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ctx">
                  Votre activité{" "}
                  <span className="text-muted-foreground font-normal">
                    (optionnel — affine le découpage)
                  </span>
                </Label>
                <Input
                  id="ctx"
                  value={context}
                  onChange={(e) => setContext(e.target.value)}
                  placeholder="Vente en ligne de matériel de jardinage"
                  maxLength={300}
                />
              </div>
              <Button
                type="submit"
                disabled={!keyword.trim() || busy}
                className="w-full"
              >
                {busy ? "Construction du cocon…" : "Générer la structure"}
              </Button>
            </form>

            {error && (
              <Alert variant="destructive" className="mt-4">
                <TriangleAlertIcon className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
          </CardContent>
        </Card>

        {result && <PreviewResult result={result} />}

        {!result && !busy && <WhatYouGet />}
      </main>

      <footer className="border-t">
        <div className="mx-auto max-w-5xl px-6 py-8 text-sm text-muted-foreground">
          Méthode du cocon sémantique formalisée par Laurent Bourrelly. Nous en
          appliquons les règles ; nous ne revendiquons aucun lien avec lui.
        </div>
      </footer>
    </div>
  );
}

function PreviewResult({ result }: { result: CoconPreviewResponse }) {
  const pages = result.articles.length;
  const exact =
    result.total_links === result.expected_links &&
    result.every_page_balanced &&
    result.orphans.length === 0;

  return (
    <div className="space-y-6">
      {/* La preuve d'abord : c'est elle qu'on vient chercher. */}
      <Card className={cn("border-l-4", exact ? "border-l-primary" : "border-l-destructive")}>
        <CardContent className="pt-6 space-y-3">
          <h2 className="font-serif text-2xl font-semibold">
            {result.total_links} liens internes, et vous pouvez les compter
          </h2>
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="Articles" value={pages} />
            <Stat
              label="Liens attendus"
              value={result.expected_links}
              hint={`${pages} × ${pages - 1}`}
            />
            <Stat
              label="Liens produits"
              value={result.total_links}
              hint={exact ? "exact" : "écart détecté"}
            />
          </div>
          <p className="text-sm text-muted-foreground">
            Chaque page émet <strong>{pages - 1} liens</strong> et en reçoit
            exactement <strong>{pages - 1}</strong> : la mère vers toutes ses
            filles, chaque fille vers la mère et vers toutes ses sœurs. Aucune
            page orpheline n&apos;est possible — pas parce qu&apos;on les
            détecte, mais parce que la structure ne permet pas d&apos;en créer.
          </p>
        </CardContent>
      </Card>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
          Thème
        </p>
        <p className="font-medium">{result.theme}</p>
        <p className="text-sm text-muted-foreground mt-1">{result.rationale}</p>
      </div>

      <div className="space-y-3">
        {result.articles.map((article) => (
          <Card
            key={article.slug}
            className={cn("border-l-4", article.is_mother ? "border-l-primary" : "border-l-border")}
          >
            <CardContent className="pt-6 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={article.is_mother ? "default" : "secondary"}>
                  {article.is_mother ? "Mère" : "Fille"}
                </Badge>
                <Badge variant="outline" className="text-xs">
                  {article.intent}
                </Badge>
                <Badge variant="outline" className="text-xs">
                  {article.outbound} sortants · {article.inbound} entrants
                </Badge>
              </div>
              <div>
                <h3 className="font-semibold leading-snug">{article.h1_title}</h3>
                <p className="font-mono text-xs text-muted-foreground">
                  /{article.slug}
                </p>
              </div>
              <div className="rounded-lg border bg-muted/40 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
                  Liens à poser depuis cette page
                </p>
                <ul className="space-y-1 text-sm">
                  {article.links_to.map((link) => (
                    <li key={link.target_slug} className="flex flex-wrap gap-1.5">
                      <span className="italic">« {link.anchor_text} »</span>
                      <span className="text-muted-foreground">→</span>
                      <code className="text-xs">/{link.target_slug}</code>
                    </li>
                  ))}
                </ul>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="bg-muted/40">
        <CardContent className="pt-6 space-y-3">
          <h2 className="font-serif text-xl font-semibold">
            Ce que cette page ne vous donne pas
          </h2>
          <p className="text-sm text-muted-foreground">
            Les volumes de recherche réels, l&apos;analyse du top 10 de Google
            pour chaque mot-clé, les briefs éditoriaux calibrés, les articles
            rédigés et le rapport backlinks. C&apos;est le travail facturé —
            ici vous avez l&apos;architecture, qui est la partie qu&apos;on ne
            peut pas improviser.
          </p>
          <Link
            href="/login"
            className={cn(buttonVariants(), "gap-1.5 font-semibold")}
          >
            Essayer gratuitement — 3 cocons complets, sans carte
            <ArrowRightIcon className="size-4" />
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border bg-background p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function WhatYouGet() {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {[
        {
          title: "Une arborescence, pas une liste",
          body: "1 article mère et 5 filles, chacune sur une intention de recherche distincte. Pas de doublon : deux filles qui traitent le même angle se cannibalisent.",
        },
        {
          title: "Le plan de maillage complet",
          body: "Chaque lien à poser, avec son ancre et sa page cible. Vertical et transversal — les sœurs sont toutes reliées entre elles, ce que la plupart des outils omettent.",
        },
        {
          title: "Des silos étanches",
          body: "Aucun lien ne sort du cocon. C'est le principe central du siloing, et c'est ce qui distingue un cocon d'un simple groupe d'articles liés.",
        },
      ].map((item) => (
        <Card key={item.title}>
          <CardContent className="pt-6">
            <h3 className="font-semibold mb-1.5">{item.title}</h3>
            <p className="text-sm text-muted-foreground">{item.body}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
