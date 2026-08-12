"use client";

import { useState } from "react";
import Link from "next/link";
import { TriangleAlertIcon } from "lucide-react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { publicSiteAudit } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PublicSiteAuditResponse } from "@/lib/types";

/**
 * Audit public — sans compte, sans carte, sans email.
 *
 * C'est le meilleur aimant du produit, et pour une raison précise : contrairement
 * au générateur de cocons, il travaille sur le site DU VISITEUR. « Vous avez 12
 * pages orphelines » se constate en trente secondes sur son propre site ; « voici
 * une structure de cocon » demande de nous croire.
 *
 * La ligne de partage : on donne le **diagnostic chiffré**, complet et exact.
 * On retient la **liste page par page**, qui est le livrable. L'écart entre
 * « 12 orphelines » et les 5 URL montrées est exactement ce qui fait créer un
 * compte.
 *
 * Aucun `AuthGuard` : la page doit s'ouvrir depuis un lien LinkedIn.
 */
export default function AuditSitePublicPage() {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PublicSiteAuditResponse | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim() || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await publicSiteAudit(url.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Audit impossible");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <Link href="/" className="font-serif text-xl font-semibold tracking-tight">
            Cocon<span className="text-primary">.</span>
          </Link>
          <Link
            href="/login"
            className={cn(buttonVariants({ size: "sm" }), "h-9 px-4 font-semibold")}
          >
            Essayer gratuitement
          </Link>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 px-6 py-12 space-y-8">
        <div className="space-y-3">
          <h1 className="font-serif text-4xl font-semibold tracking-tight">
            Combien de vos pages ne reçoivent aucun lien&nbsp;?
          </h1>
          <p className="text-lg text-muted-foreground">
            Entrez l&apos;adresse d&apos;un site. On compte les liens internes
            réellement présents et on vous dit ce qui manque. Sans inscription,
            en trente secondes — et vous pouvez tout recompter à la main.
          </p>
        </div>

        <Card>
          <CardContent className="pt-6">
            <form onSubmit={submit} className="space-y-3">
              <div className="flex flex-col gap-3 sm:flex-row">
                <Input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://votre-site.fr"
                  disabled={busy}
                  className="flex-1"
                  aria-label="Adresse du site à auditer"
                />
                <Button type="submit" disabled={busy || !url.trim()}>
                  {busy ? "Analyse en cours…" : "Analyser"}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Les 20 premières pages du site, gratuitement. Aucune donnée
                conservée.
              </p>
              {busy && (
                <p className="text-sm text-muted-foreground">
                  Lecture du sitemap puis des pages… cela prend une vingtaine de
                  secondes.
                </p>
              )}
              {error && (
                <Alert variant="destructive">
                  <TriangleAlertIcon className="h-4 w-4" />
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
            </form>
          </CardContent>
        </Card>

        {result && <PublicReport r={result} />}

        {!result && !busy && (
          <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
            <p className="font-semibold text-foreground mb-2">
              Pourquoi ça compte
            </p>
            <p>
              Une page qu&apos;aucun lien interne ne désigne ne reçoit aucune
              autorité de votre site. Google la découvre par le sitemap au mieux,
              et n&apos;a aucune raison de la considérer comme importante. C&apos;est
              le défaut le plus fréquent et le moins visible : rien ne le signale
              dans une interface de CMS.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}

function PublicReport({ r }: { r: PublicSiteAuditResponse }) {
  const sain =
    r.orphans_count === 0 && r.dead_ends_count === 0 && r.unreachable_count === 0;

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Pages analysées" value={String(r.pages_crawled)} />
        <Stat label="Liens internes" value={String(r.total_internal_links)} />
        <Stat
          label="Pages orphelines"
          value={String(r.orphans_count)}
          tone={r.orphans_count ? "bad" : "ok"}
        />
        <Stat
          label="Réciprocité"
          value={`${Math.round(r.reciprocity_rate * 100)} %`}
          tone={r.reciprocity_rate >= 0.4 ? "ok" : "warn"}
        />
      </div>

      {r.findings.length > 0 && (
        <Card>
          <CardContent className="pt-6 space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Ce que montre votre maillage
            </p>
            <ul className="space-y-1.5 text-sm">
              {r.findings.map((f, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-muted-foreground">•</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <SampleList
          title="Pages orphelines"
          count={r.orphans_count}
          sample={r.orphans_sample}
        />
        <SampleList
          title="Pages sans lien sortant"
          count={r.dead_ends_count}
          sample={r.dead_ends_sample}
        />
      </div>

      <Card className="border-primary/40 bg-primary/5">
        <CardContent className="pt-6 space-y-3">
          <p className="font-semibold">
            {sain
              ? "Votre maillage tient sur les 20 pages analysées."
              : "Voilà le diagnostic. Le détail, page par page, est dans l'outil complet."}
          </p>
          <p className="text-sm text-muted-foreground">
            {sain
              ? "L'outil complet va plus loin : la totalité du site, la profondeur de clic page par page, et la construction de cocons sémantiques dont le maillage est imposé en code — chaque page reçoit exactement le nombre de liens que la méthode prévoit."
              : `L'audit complet couvre tout le site (ici : ${r.pages_crawled} pages sur ${r.truncated ? "un site plus grand" : "le total"}), donne la liste exhaustive avec liens entrants et sortants par page, et la profondeur de clic. L'essai offre 3 cocons complets, sans carte bancaire.`}
          </p>
          <div className="flex flex-wrap gap-2">
            <Link href="/login" className={buttonVariants({ size: "sm" })}>
              Créer un compte — 3 cocons offerts
            </Link>
            <Link
              href="/outils/cocon"
              className={buttonVariants({ size: "sm", variant: "outline" })}
            >
              Voir un cocon généré
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function SampleList({
  title,
  count,
  sample,
}: {
  title: string;
  count: number;
  sample: string[];
}) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-baseline justify-between mb-3">
          <p className="text-sm font-semibold">{title}</p>
          <span
            className={cn(
              "font-serif text-xl font-semibold tabular-nums",
              count ? "text-destructive-strong" : "text-success-strong",
            )}
          >
            {count}
          </span>
        </div>
        {count === 0 ? (
          <p className="text-xs text-success-strong">Aucune. C&apos;est bon signe.</p>
        ) : (
          <>
            <ul className="space-y-1 text-xs">
              {sample.map((u) => (
                <li key={u} className="truncate text-muted-foreground" title={u}>
                  {u}
                </li>
              ))}
            </ul>
            {count > sample.length && (
              <p className="mt-2 text-xs font-medium">
                … et {count - sample.length} autre
                {count - sample.length > 1 ? "s" : ""}, visibles dans l&apos;outil
                complet.
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "ok" | "warn" | "bad";
}) {
  const cls = {
    neutral: "",
    ok: "text-success-strong",
    warn: "text-warning-strong",
    bad: "text-destructive-strong",
  }[tone];
  return (
    <Card>
      <CardContent className="pt-6">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className={cn("mt-1 font-serif text-2xl font-semibold tabular-nums", cls)}>
          {value}
        </p>
      </CardContent>
    </Card>
  );
}
