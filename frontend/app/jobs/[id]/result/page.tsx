"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { CheckIcon, CopyIcon, TriangleAlertIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AuthGuard } from "@/components/auth-guard";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { fetchJobStatus } from "@/lib/api";
import { GroupHeading, SectionHeading, TONE, type Tone } from "@/components/result/section";
import { Markdown } from "@/components/result/markdown";
import { RegenerateArticle } from "@/components/result/regenerate";
import { cn } from "@/lib/utils";
import type {
  ArticleBrief,
  BacklinkReport,
  CoconStructure,
  GeneratedArticle,
  JobStatusResponse,
  PipelineResult,
} from "@/lib/types";

function ResultPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: jobId } = use(params);
  const [status, setStatus] = useState<JobStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchJobStatus(jobId)
      .then(setStatus)
      .catch((e) => setError(e instanceof Error ? e.message : "Erreur"));
  }, [jobId]);

  if (error) {
    return (
      <ErrorLayout>
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </ErrorLayout>
    );
  }

  if (!status) return <ErrorLayout>Chargement…</ErrorLayout>;

  if (status.status !== "finished") {
    return (
      <ErrorLayout>
        <Alert>
          <AlertDescription>
            Ce job n&apos;est pas encore terminé (statut : {status.status}).{" "}
            <Link href={`/jobs/${jobId}`} className="underline">
              Voir la progression
            </Link>
          </AlertDescription>
        </Alert>
      </ErrorLayout>
    );
  }

  const result = status.result;
  if (!result) {
    return (
      <ErrorLayout>
        <Alert variant="destructive">
          <AlertDescription>Résultat introuvable pour ce job.</AlertDescription>
        </Alert>
      </ErrorLayout>
    );
  }

  return <ResultView result={result} jobId={jobId} runId={status.run_id} />;
}

function ResultView({
  result,
  jobId,
  runId,
}: {
  result: PipelineResult;
  jobId: string;
  runId: string | null;
}) {
  const totalArticles =
    result.cocoons.reduce((acc, c) => acc + 1 + c.daughters.length, 0);
  const mode = result.form.mode;

  function downloadJson() {
    const blob = new Blob([JSON.stringify(result, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `cocons-${jobId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="min-h-screen">
      <header className="border-b bg-background/80 backdrop-blur sticky top-0 z-40">
        <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-4">
            <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
              ← Accueil
            </Link>
            <Separator orientation="vertical" className="h-4" />
            <span className="text-sm">
              <span className="font-medium">{result.form.product}</span>{" "}
              <span className="text-muted-foreground">· {totalArticles} articles</span>
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={mode === "brief" ? "secondary" : "default"}>
              {mode === "brief" ? "Mode Brief" : "Mode Full"}
            </Badge>
            {result.usage && (
              // Coût réel de CETTE run, pas une fourchette générique : c'est ce
              // que l'agence refacture, elle doit pouvoir le lire sans nous.
              <Badge
                variant="outline"
                title={
                  `${result.usage.claude_calls} appels Claude · ` +
                  `${result.usage.output_tokens.toLocaleString("fr-FR")} tokens de sortie · ` +
                  `DataForSEO $${result.usage.dataforseo_cost_usd.toFixed(2)} · ` +
                  `caching économisé $${result.usage.cache_savings_usd.toFixed(2)}`
                }
              >
                $
                {(
                  result.usage.claude_cost_usd + result.usage.dataforseo_cost_usd
                ).toFixed(2)}
              </Badge>
            )}
            <Button size="sm" variant="outline" onClick={downloadJson}>
              Export JSON
            </Button>
            <WordPressExport runId={runId} />
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-10 space-y-8">
        {/* Overview */}
        <div className="grid md:grid-cols-4 gap-4">
          <StatCard label="Cocons" value={result.cocoons.length} tone="cocon" />
          <StatCard label="Articles" value={totalArticles} tone="article" />
          <StatCard
            label="Mots-clés analysés"
            value={result.keywords_researched.length}
            tone="serp"
          />
          <StatCard
            label="Liens internes"
            value={Object.values(result.maillage_map.links).reduce(
              (n, l) => n + l.length,
              0,
            )}
            tone="maillage"
            hint={
              result.maillage_map.inter_cocon_links.length === 0
                ? "cocons étanches"
                : `dont ${result.maillage_map.inter_cocon_links.length} inter-cocons`
            }
          />
        </div>

        {/* Cocons */}
        <Tabs defaultValue={result.cocoons[0]?.id} className="w-full">
          <TabsList className="flex w-full flex-wrap gap-2 h-auto p-1">
            {result.cocoons.map((cocon, i) => (
              <TabsTrigger key={cocon.id} value={cocon.id} className="text-sm">
                Cocon {i + 1}
              </TabsTrigger>
            ))}
          </TabsList>

          {result.cocoons.map((cocon) => (
            <TabsContent key={cocon.id} value={cocon.id} className="space-y-6 mt-6">
              <CoconOverview cocon={cocon} />
              <ArticlesPanel
                cocon={cocon}
                briefs={result.briefs}
                articles={result.articles}
                mode={mode}
                runId={runId}
              />
              <BacklinkPanel
                report={result.backlink_reports.find((r) => r.cocon_id === cocon.id)}
              />
            </TabsContent>
          ))}
        </Tabs>
      </main>
    </div>
  );
}

// ============================================================
// Sub-components
// ============================================================

function StatCard({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: number;
  tone: Tone;
  hint?: string;
}) {
  const t = TONE[tone];
  return (
    <Card className={cn("border-l-4", t.border.replace("border-", "border-l-"))}>
      <CardContent className="pt-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className={cn("text-3xl font-bold mt-1 tabular-nums", t.text)}>{value}</p>
        {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
      </CardContent>
    </Card>
  );
}

function CoconOverview({ cocon }: { cocon: CoconStructure }) {
  const t = TONE.cocon;
  return (
    <Card className={cn("border-l-4", t.border.replace("border-", "border-l-"))}>
      <CardHeader>
        <CardTitle className={cn("text-lg font-bold", t.text)}>
          {cocon.theme}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Mot-clé principal
          </span>
          <span
            className={cn(
              "rounded-md px-2 py-0.5 font-mono text-xs font-semibold",
              t.bg,
              t.text,
            )}
          >
            {cocon.main_keyword}
          </span>
        </div>
        <p className="text-muted-foreground italic leading-relaxed">
          {cocon.rationale}
        </p>
      </CardContent>
    </Card>
  );
}

function ArticlesPanel({
  cocon,
  briefs,
  articles,
  mode,
  runId,
}: {
  cocon: CoconStructure;
  briefs: ArticleBrief[];
  articles: GeneratedArticle[];
  mode: "brief" | "full";
  runId: string | null;
}) {
  const stubs = [cocon.mother, ...cocon.daughters];

  return (
    <div className="space-y-3">
      <GroupHeading tone="article" count={stubs.length}>
        Articles
      </GroupHeading>
      {stubs.map((stub) => {
        const brief = briefs.find((b) => b.stub.slug === stub.slug);
        const article = articles.find((a) => a.stub.slug === stub.slug);
        const isMother = stub.article_type === "mother";
        return (
          <Card
            key={stub.slug}
            className={cn(
              "border-l-4",
              isMother
                ? "border-l-tone-cocon bg-tone-cocon-soft/40"
                : "border-l-sky-300 dark:border-l-sky-800",
            )}
          >
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2 mb-1.5">
                    <Badge
                      className={cn(
                        "font-semibold",
                        isMother
                          ? "bg-tone-cocon text-background"
                          : "bg-tone-article-soft text-tone-article",
                      )}
                    >
                      {isMother ? "Mère" : "Fille"}
                    </Badge>
                    <Badge variant="outline" className="text-xs font-medium">
                      {stub.intent}
                    </Badge>
                  </div>
                  <CardTitle className="text-base font-bold leading-snug">
                    {stub.h1_title}
                  </CardTitle>
                </div>
              </div>
              <p className="text-xs font-mono text-muted-foreground mt-1">/{stub.slug}</p>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <MetaBlock stub={stub} />

              {mode === "brief" && brief && <BriefContent brief={brief} />}
              {mode === "full" && article && <ArticleContent article={article} />}

              {!brief && !article && (
                <p className="text-muted-foreground italic">
                  Contenu non disponible (analyse SERP peut avoir échoué).
                </p>
              )}

              {(brief || article) && (
                <>
                  <Separator />
                  <RegenerateArticle
                    runId={runId}
                    slug={stub.slug}
                    currentDirectives={stub.directives ?? null}
                  />
                </>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function MetaBlock({ stub }: { stub: CoconStructure["mother"] }) {
  return (
    <div className="grid md:grid-cols-2 gap-4 text-xs bg-muted/50 rounded-lg p-3 border">
      <div>
        <p className="font-semibold uppercase tracking-wide text-muted-foreground mb-0.5">
          Meta title
        </p>
        <p className="font-mono text-foreground">{stub.meta_title}</p>
      </div>
      <div>
        <p className="font-semibold uppercase tracking-wide text-muted-foreground mb-0.5">
          KW cible
        </p>
        <p className="font-mono font-semibold text-tone-serp">
          {stub.target_keyword}
        </p>
      </div>
      <div className="md:col-span-2">
        <p className="font-semibold uppercase tracking-wide text-muted-foreground mb-0.5">
          Meta description
        </p>
        <p className="text-foreground">{stub.meta_description}</p>
      </div>
    </div>
  );
}

function BriefContent({ brief }: { brief: ArticleBrief }) {
  return (
    <div className="space-y-5">
      <div>
        <SectionHeading tone="article">Angle unique</SectionHeading>
        <p className="text-sm leading-relaxed">{brief.unique_angle}</p>
      </div>

      <div>
        <SectionHeading
          tone="serp"
          hint={`${brief.serp_analysis.scraped_pages_count} pages de référence sur ${brief.serp_analysis.serp_urls_count} résultats`}
        >
          Analyse SERP
        </SectionHeading>
        {brief.serp_analysis.low_sample && (
          <p className="mb-2 rounded border border-warning-line bg-warning-soft px-2 py-1.5 text-xs text-warning-strong">
            Calibration peu fiable : seulement {brief.serp_analysis.scraped_pages_count} page
            {brief.serp_analysis.scraped_pages_count > 1 ? "s" : ""} exploitable
            {brief.serp_analysis.scraped_pages_count > 1 ? "s" : ""} dans le top 10.
            {Object.keys(brief.serp_analysis.rejected_pages ?? {}).length > 0 && (
              <>
                {" "}
                Écartées :{" "}
                {Object.entries(brief.serp_analysis.rejected_pages)
                  .map(([motif, n]) => `${n} × ${motif}`)
                  .join(", ")}
                .
              </>
            )}{" "}
            Longueur cible et nombre de H2 à vérifier manuellement.
          </p>
        )}
        <div className="flex flex-wrap gap-2 text-xs">
          <Metric label="Longueur cible" value={`${brief.serp_analysis.recommended_word_count} mots`} />
          <Metric label="H2" value={String(brief.serp_analysis.recommended_h2_count)} />
        </div>
        {brief.serp_analysis.key_entities.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-1">
            {brief.serp_analysis.key_entities.slice(0, 12).map((e) => (
              <Badge
                key={e}
                className="bg-tone-serp-soft text-tone-serp text-xs font-medium"
              >
                {e}
              </Badge>
            ))}
          </div>
        )}
      </div>

      <div>
        <SectionHeading tone="article" count={brief.sections.length}>
          Structure
        </SectionHeading>
        <ol className="space-y-2.5 text-sm">
          {brief.sections.map((s, i) => (
            <li key={i} className="border-l-2 border-tone-article-line pl-3">
              <p className="font-semibold text-foreground">{s.h2}</p>
              {s.h3s.length > 0 && (
                <ul className="text-xs text-muted-foreground mt-1 space-y-0.5">
                  {s.h3s.map((h3, j) => (
                    <li key={j} className="flex gap-1.5">
                      <span className="text-tone-article">›</span>
                      {h3}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      </div>

      {brief.faq_questions.length > 0 && (
        <div>
          <SectionHeading tone="faq" count={brief.faq_questions.length}>
            FAQ suggérée
          </SectionHeading>
          <ul className="space-y-1.5 text-sm">
            {brief.faq_questions.map((q, i) => (
              <li key={i} className="flex gap-2">
                <span className="font-semibold text-tone-faq">?</span>
                {q}
              </li>
            ))}
          </ul>
        </div>
      )}

      {brief.internal_links_plan.length > 0 && (
        <MaillageBlock links={brief.internal_links_plan} />
      )}

      {brief.editorial_notes && (
        <div>
          <SectionHeading tone="neutral">Notes éditoriales</SectionHeading>
          <p className="text-sm whitespace-pre-wrap leading-relaxed rounded-lg border bg-muted/40 p-3">
            {brief.editorial_notes}
          </p>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5 rounded-md border bg-muted/40 px-2 py-1">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-semibold text-foreground">{value}</span>
    </span>
  );
}

function EeatBadge({ score }: { score: number }) {
  const tone =
    score >= 70
      ? "bg-success-soft text-success-strong"
      : score >= 50
        ? "bg-warning-soft text-warning-strong"
        : "bg-destructive-soft text-destructive-strong";
  return (
    <span
      className={cn(
        "inline-flex items-baseline gap-1.5 rounded-md px-2 py-1 text-xs font-semibold",
        tone,
      )}
    >
      E-E-A-T
      <span className="tabular-nums">{score}/100</span>
    </span>
  );
}

/**
 * Copie le markdown source — y compris depuis la « Vue lisible ». C'est le
 * livrable que l'agence colle dans son CMS ; copier le rendu perdrait les
 * titres, les tableaux et les marqueurs de maillage.
 */
function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "ok" | "error">("idle");

  useEffect(() => {
    if (state === "idle") return;
    const t = setTimeout(() => setState("idle"), 2000);
    return () => clearTimeout(t);
  }, [state]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("ok");
    } catch {
      // Clipboard refusée (contexte non sécurisé, permission) : on le dit au
      // lieu de laisser croire que la copie a marché.
      setState("error");
    }
  }

  return (
    <Button
      size="sm"
      variant="ghost"
      className="text-xs h-7"
      onClick={copy}
      aria-live="polite"
      title="Copier le markdown de l'article"
    >
      {state === "ok" ? (
        <>
          <CheckIcon data-icon="inline-start" className="text-success" />
          Copié
        </>
      ) : state === "error" ? (
        <>
          <TriangleAlertIcon data-icon="inline-start" className="text-warning" />
          Échec
        </>
      ) : (
        <>
          <CopyIcon data-icon="inline-start" />
          Copier
        </>
      )}
    </Button>
  );
}

function ArticleContent({ article }: { article: GeneratedArticle }) {
  const [raw, setRaw] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Metric label="Longueur" value={`${article.word_count} mots`} />
        {article.eeat_score && <EeatBadge score={article.eeat_score.overall} />}
      </div>

      {article.eeat_score?.warnings.length ? (
        <Alert className="border-warning-line bg-warning-soft">
          <AlertDescription>
            <p className="text-xs font-bold text-warning-strong mb-1.5">
              À améliorer avant publication
            </p>
            <ul className="text-xs space-y-1">
              {article.eeat_score.warnings.map((w, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-warning">•</span>
                  {w}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      <div>
        <div className="flex items-center justify-between">
          <SectionHeading tone="article" className="mb-0">
            Article rédigé
          </SectionHeading>
          <div className="flex items-center gap-1">
            <CopyButton text={article.content_markdown} />
            <Button
              size="sm"
              variant="ghost"
              className="text-xs h-7"
              onClick={() => setRaw((r) => !r)}
            >
              {raw ? "Vue lisible" : "Markdown brut"}
            </Button>
          </div>
        </div>
        <ScrollArea className="mt-2 h-[32rem] rounded-lg border bg-card px-5 py-3">
          {raw ? (
            <pre className="text-xs whitespace-pre-wrap font-mono text-muted-foreground">
              {article.content_markdown}
            </pre>
          ) : (
            <Markdown content={article.content_markdown} />
          )}
        </ScrollArea>
      </div>

      {article.internal_links.length > 0 && (
        <MaillageBlock links={article.internal_links} />
      )}
    </div>
  );
}

function MaillageBlock({
  links,
}: {
  links: { anchor_text: string; target_slug: string; target_h1: string; link_type: string }[];
}) {
  // Une couleur par type de lien : on voit d'un coup d'œil si la hiérarchie du
  // cocon est respectée, et un lien inter-cocon (rare par défaut) saute aux yeux.
  const LINK_STYLE: Record<string, { label: string; className: string }> = {
    daughter_to_mother: {
      label: "↑ Mère",
      className:
        "bg-tone-cocon-soft text-tone-cocon",
    },
    mother_to_daughter: {
      label: "↓ Fille",
      className: "bg-tone-article-soft text-tone-article",
    },
    sister_to_sister: {
      label: "↔ Sœur",
      className:
        "bg-success-soft text-success-strong",
    },
    cross_cocon: {
      label: "⤳ Inter-cocon",
      className:
        "bg-warning-soft text-warning-strong",
    },
  };

  return (
    <div>
      <SectionHeading tone="maillage" count={links.length}>
        Maillage interne
      </SectionHeading>
      <ul className="space-y-1.5 text-xs">
        {links.map((l, i) => {
          const style = LINK_STYLE[l.link_type];
          return (
            <li
              key={i}
              className="flex flex-wrap items-center gap-2 rounded-md border bg-card px-2.5 py-1.5"
            >
              <Badge
                className={cn(
                  "font-semibold shrink-0",
                  style?.className ?? "bg-muted text-foreground",
                )}
              >
                {style?.label ?? l.link_type}
              </Badge>
              <span className="font-semibold text-foreground">
                « {l.anchor_text} »
              </span>
              <span className="font-mono text-muted-foreground">
                → /{l.target_slug}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function BacklinkPanel({ report }: { report: BacklinkReport | undefined }) {
  if (!report) return null;
  return (
    <div className="space-y-3">
      <GroupHeading tone="backlinks">Rapport backlinks</GroupHeading>
      <Card className="border-l-4 border-l-warning">
        <CardContent className="pt-6 space-y-5">
          <div>
            <SectionHeading tone="backlinks">
              Ratio d&apos;ancres recommandé
            </SectionHeading>
            <div className="grid grid-cols-5 gap-3 text-xs">
              <RatioBar label="Exact" value={report.recommended_anchor_ratio.exact} />
              <RatioBar label="Partial" value={report.recommended_anchor_ratio.partial} />
              <RatioBar label="Brand" value={report.recommended_anchor_ratio.branded} />
              <RatioBar label="URL nue" value={report.recommended_anchor_ratio.naked_url} />
              <RatioBar label="Générique" value={report.recommended_anchor_ratio.generic} />
            </div>
          </div>

          <div>
            <SectionHeading tone="backlinks" count={report.opportunities.length}>
              Opportunités
            </SectionHeading>
            <ul className="space-y-2 text-sm">
              {report.opportunities.slice(0, 10).map((o, i) => (
                <li key={i} className="border rounded-lg p-3 bg-card">
                  <div className="flex items-center justify-between gap-2 mb-1.5">
                    <span className="font-bold text-warning-strong">
                      {o.referring_domain}
                    </span>
                    <div className="flex items-center gap-2 text-xs">
                      {o.domain_rating != null && (
                        <Badge variant="outline" className="font-semibold">
                          DR {o.domain_rating}
                        </Badge>
                      )}
                      <Badge className="bg-warning-soft text-warning-strong font-medium">
                        {o.outreach_template_type}
                      </Badge>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    {o.reason}
                  </p>
                  <p className="text-xs mt-1.5">
                    <span className="text-muted-foreground">Ancre suggérée : </span>
                    <span className="font-mono font-semibold text-foreground">
                      « {o.suggested_anchor} »
                    </span>
                  </p>
                </li>
              ))}
            </ul>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function RatioBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-bold tabular-nums text-warning-strong">
          {pct}%
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className="h-full rounded-full bg-warning"
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}

function ErrorLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="border-b">
        <div className="max-w-4xl mx-auto flex items-center px-6 py-4">
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
            ← Accueil
          </Link>
        </div>
      </header>
      <main className="max-w-2xl mx-auto px-6 py-16">{children}</main>
    </div>
  );
}

export default function Page(props: { params: Promise<{ id: string }> }) {
  return (
    <AuthGuard>
      <ResultPage {...props} />
    </AuthGuard>
  );
}
