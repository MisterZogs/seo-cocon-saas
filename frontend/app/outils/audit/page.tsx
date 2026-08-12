"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { TriangleAlertIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { AccountMenu, AuthGuard } from "@/components/auth-guard";
import { ScrollArea } from "@/components/ui/scroll-area";
import { fetchJobStatus, startSiteAudit } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PageLinkStats, SiteAuditReport } from "@/lib/types";

/**
 * Audit du maillage d'un site EXISTANT — le pont vers les clients déjà en
 * portefeuille des agences.
 *
 * L'écran est construit autour du même principe que le générateur public :
 * tout ce qui est affiché se recompte à la main sur le site du client. Aucun
 * chiffre n'est une estimation, aucun n'est produit par un modèle.
 *
 * Le contraste est l'argument commercial : le même outil, passé sur un cocon
 * que nous générons, rend un rapport vierge.
 */
function AuditPage() {
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState(200);
  const [phase, setPhase] = useState<"idle" | "running" | "error">("idle");
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<SiteAuditReport | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function poll(jobId: string) {
    try {
      const status = await fetchJobStatus(jobId);
      setMessage(status.progress?.message ?? "Analyse en cours…");

      if (status.status === "finished") {
        const result = status.result;
        if (result && "site_audit" in result) {
          setReport(result.report);
          setPhase("idle");
        } else {
          setError("Ce job n'est pas un audit de maillage.");
          setPhase("error");
        }
        return;
      }
      if (status.status === "failed") {
        setError(status.error ?? "L'audit a échoué.");
        setPhase("error");
        return;
      }
      timer.current = setTimeout(() => poll(jobId), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Suivi impossible");
      setPhase("error");
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim() || phase === "running") return;
    setPhase("running");
    setError(null);
    setReport(null);
    setMessage("Découverte du sitemap…");
    try {
      const started = await startSiteAudit({
        start_url: url.trim(),
        max_pages: maxPages,
      });
      timer.current = setTimeout(() => poll(started.job_id), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Audit impossible");
      setPhase("error");
    }
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link href="/" className="font-serif text-xl font-semibold tracking-tight">
            Cocon<span className="text-primary">.</span>
          </Link>
          <div className="flex items-center gap-3">
            <Link href="/runs" className="text-sm text-muted-foreground hover:text-foreground">
              Mes générations
            </Link>
            <AccountMenu />
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10 space-y-8">
        <div className="space-y-2">
          <h1 className="font-serif text-3xl font-semibold tracking-tight">
            Audit de maillage interne
          </h1>
          <p className="text-muted-foreground max-w-2xl">
            Sur un site déjà en ligne : pages orphelines, culs-de-sac, profondeur
            de clic, réciprocité des liens. Tout est compté sur le site réel —
            aucune estimation, aucun modèle. Vous pouvez recompter.
          </p>
        </div>

        <Card>
          <CardContent className="pt-6">
            <form onSubmit={submit} className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
                <div className="space-y-1.5">
                  <Label htmlFor="url">Adresse du site ou du sitemap</Label>
                  <Input
                    id="url"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://site-du-client.fr"
                    disabled={phase === "running"}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="max">Pages max</Label>
                  <Input
                    id="max"
                    type="number"
                    min={5}
                    max={1000}
                    value={maxPages}
                    onChange={(e) => setMaxPages(Number(e.target.value))}
                    disabled={phase === "running"}
                    className="w-28"
                  />
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Button type="submit" disabled={phase === "running" || !url.trim()}>
                  {phase === "running" ? "Analyse en cours…" : "Lancer l'audit"}
                </Button>
                <span className="text-xs text-muted-foreground">
                  Gratuit, non décompté de vos cocons.
                </span>
              </div>

              {phase === "running" && (
                <p className="text-sm text-muted-foreground">{message}</p>
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

        {report && <Report report={report} />}
      </main>
    </div>
  );
}

function Report({ report }: { report: SiteAuditReport }) {
  const problemes =
    report.orphans.length + report.dead_ends.length + report.unreachable.length;

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Pages analysées" value={String(report.pages_crawled)} />
        <Stat label="Liens internes" value={String(report.total_internal_links)} />
        <Stat
          label="Réciprocité"
          value={`${Math.round(report.reciprocity_rate * 100)} %`}
          tone={report.reciprocity_rate >= 0.4 ? "ok" : "warn"}
        />
        <Stat
          label="Pages en défaut"
          value={String(problemes)}
          tone={problemes === 0 ? "ok" : "bad"}
        />
      </div>

      {report.findings.length > 0 && (
        <Card>
          <CardContent className="pt-6 space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Ce que montre le maillage
            </p>
            <ul className="space-y-1.5 text-sm">
              {report.findings.map((f, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-muted-foreground">•</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <UrlList
          title="Pages orphelines"
          hint="aucun lien interne ne pointe vers elles"
          urls={report.orphans}
        />
        <UrlList
          title="Pages inatteignables"
          hint="aucun chemin de liens depuis l'accueil"
          urls={report.unreachable}
        />
        <UrlList
          title="Culs-de-sac"
          hint="ne redistribuent aucune autorité"
          urls={report.dead_ends}
        />
      </div>

      {Object.keys(report.depth_distribution).length > 0 && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
              Profondeur de clic depuis l&apos;accueil
            </p>
            <div className="space-y-1.5">
              {Object.entries(report.depth_distribution).map(([depth, count]) => (
                <div key={depth} className="flex items-center gap-3 text-xs">
                  <span className="w-16 shrink-0 text-muted-foreground">
                    {depth} clic{Number(depth) > 1 ? "s" : ""}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn(
                        "h-full rounded-full",
                        Number(depth) >= 4 ? "bg-warning" : "bg-primary",
                      )}
                      style={{
                        width: `${(count * 100) / report.pages_crawled}%`,
                      }}
                    />
                  </div>
                  <span className="w-10 shrink-0 text-right tabular-nums">{count}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="pt-6">
          <div className="flex items-baseline justify-between mb-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Toutes les pages
            </p>
            <span className="text-xs text-muted-foreground">
              triées par liens entrants croissants
            </span>
          </div>
          <ScrollArea className="h-96 rounded-lg border">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                <tr className="text-left">
                  <th className="px-3 py-2 font-semibold">Page</th>
                  <th className="px-3 py-2 font-semibold text-right">Entrants</th>
                  <th className="px-3 py-2 font-semibold text-right">Sortants</th>
                  <th className="px-3 py-2 font-semibold text-right">Profondeur</th>
                </tr>
              </thead>
              <tbody>
                {report.pages.map((p) => (
                  <PageRow key={p.url} page={p} />
                ))}
              </tbody>
            </table>
          </ScrollArea>
        </CardContent>
      </Card>

      {Object.keys(report.failed_urls).length > 0 && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
              Pages non lues ({Object.keys(report.failed_urls).length})
            </p>
            {/* Nommées, pas seulement comptées : sans l'URL, l'agence ne peut
                ni vérifier ni corriger. */}
            <ul className="space-y-1 text-xs text-muted-foreground">
              {Object.entries(report.failed_urls)
                .slice(0, 30)
                .map(([u, motif]) => (
                  <li key={u} className="flex justify-between gap-3">
                    <span className="truncate">{u}</span>
                    <span className="shrink-0">{motif}</span>
                  </li>
                ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function PageRow({ page }: { page: PageLinkStats }) {
  return (
    <tr className="border-t">
      <td className="px-3 py-1.5">
        <span className="block truncate max-w-md" title={page.title ?? page.url}>
          {page.title || page.url}
        </span>
        <span className="block truncate max-w-md text-muted-foreground">{page.url}</span>
      </td>
      <td
        className={cn(
          "px-3 py-1.5 text-right tabular-nums",
          page.inbound === 0 && "font-semibold text-destructive",
        )}
      >
        {page.inbound}
      </td>
      <td
        className={cn(
          "px-3 py-1.5 text-right tabular-nums",
          page.outbound === 0 && "font-semibold text-warning",
        )}
      >
        {page.outbound}
      </td>
      <td className="px-3 py-1.5 text-right tabular-nums">
        {page.depth === null ? (
          <span className="text-destructive">hors d&apos;atteinte</span>
        ) : (
          page.depth
        )}
      </td>
    </tr>
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

function UrlList({
  title,
  hint,
  urls,
}: {
  title: string;
  hint: string;
  urls: string[];
}) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-baseline gap-2 mb-1">
          <p className="text-sm font-semibold">{title}</p>
          <Badge variant={urls.length ? "destructive" : "secondary"} className="text-xs">
            {urls.length}
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground mb-3">{hint}</p>
        {urls.length === 0 ? (
          <p className="text-xs text-success-strong">Aucune.</p>
        ) : (
          <ul className="space-y-1 text-xs">
            {urls.slice(0, 15).map((u) => (
              <li key={u} className="truncate" title={u}>
                {u}
              </li>
            ))}
            {urls.length > 15 && (
              <li className="text-muted-foreground">
                … et {urls.length - 15} autre(s)
              </li>
            )}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export default function Page() {
  return (
    <AuthGuard>
      <AuditPage />
    </AuthGuard>
  );
}
