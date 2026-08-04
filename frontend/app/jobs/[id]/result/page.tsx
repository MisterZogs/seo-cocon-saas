"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { fetchJobStatus } from "@/lib/api";
import { GroupHeading, SectionHeading, TONE, type Tone } from "@/components/result/section";
import { Markdown } from "@/components/result/markdown";
import { cn } from "@/lib/utils";
import type {
  ArticleBrief,
  BacklinkReport,
  CoconStructure,
  GeneratedArticle,
  JobStatusResponse,
  PipelineResult,
} from "@/lib/types";

export default function ResultPage({
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

  return <ResultView result={result} jobId={jobId} />;
}

function ResultView({ result, jobId }: { result: PipelineResult; jobId: string }) {
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
            <Button size="sm" variant="outline" onClick={downloadJson}>
              Export JSON
            </Button>
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

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className="text-3xl font-bold mt-1">{value}</p>
      </CardContent>
    </Card>
  );
}

function CoconOverview({ cocon }: { cocon: CoconStructure }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">{cocon.theme}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div>
          <span className="text-muted-foreground">Mot-clé principal :</span>{" "}
          <span className="font-mono">{cocon.main_keyword}</span>
        </div>
        <p className="text-muted-foreground italic">{cocon.rationale}</p>
      </CardContent>
    </Card>
  );
}

function ArticlesPanel({
  cocon,
  briefs,
  articles,
  mode,
}: {
  cocon: CoconStructure;
  briefs: ArticleBrief[];
  articles: GeneratedArticle[];
  mode: "brief" | "full";
}) {
  const stubs = [cocon.mother, ...cocon.daughters];

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        Articles ({stubs.length})
      </h3>
      {stubs.map((stub) => {
        const brief = briefs.find((b) => b.stub.slug === stub.slug);
        const article = articles.find((a) => a.stub.slug === stub.slug);
        return (
          <Card key={stub.slug}>
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant={stub.article_type === "mother" ? "default" : "outline"}>
                      {stub.article_type === "mother" ? "Mère" : "Fille"}
                    </Badge>
                    <Badge variant="secondary" className="text-xs">
                      {stub.intent}
                    </Badge>
                  </div>
                  <CardTitle className="text-base">{stub.h1_title}</CardTitle>
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
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function MetaBlock({ stub }: { stub: CoconStructure["mother"] }) {
  return (
    <div className="grid md:grid-cols-2 gap-4 text-xs bg-muted/40 rounded-md p-3">
      <div>
        <p className="text-muted-foreground">Meta title</p>
        <p className="font-mono">{stub.meta_title}</p>
      </div>
      <div>
        <p className="text-muted-foreground">KW cible</p>
        <p className="font-mono">{stub.target_keyword}</p>
      </div>
      <div className="md:col-span-2">
        <p className="text-muted-foreground">Meta description</p>
        <p>{stub.meta_description}</p>
      </div>
    </div>
  );
}

function BriefContent({ brief }: { brief: ArticleBrief }) {
  return (
    <div className="space-y-4">
      <div>
        <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
          Angle unique
        </h4>
        <p>{brief.unique_angle}</p>
      </div>

      <div>
        <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
          Analyse SERP
        </h4>
        <p className="text-xs text-muted-foreground">
          Longueur cible : {brief.serp_analysis.recommended_word_count} mots ·{" "}
          {brief.serp_analysis.recommended_h2_count} H2 ·{" "}
          {brief.serp_analysis.scraped_pages_count} pages analysées
        </p>
        {brief.serp_analysis.key_entities.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {brief.serp_analysis.key_entities.slice(0, 12).map((e) => (
              <Badge key={e} variant="outline" className="text-xs font-normal">
                {e}
              </Badge>
            ))}
          </div>
        )}
      </div>

      <div>
        <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
          Structure ({brief.sections.length} sections)
        </h4>
        <ol className="space-y-2 text-sm">
          {brief.sections.map((s, i) => (
            <li key={i} className="border-l-2 border-primary/30 pl-3">
              <p className="font-medium">{s.h2}</p>
              {s.h3s.length > 0 && (
                <ul className="text-xs text-muted-foreground list-disc list-inside mt-1">
                  {s.h3s.map((h3, j) => (
                    <li key={j}>{h3}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      </div>

      {brief.faq_questions.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
            FAQ suggérée
          </h4>
          <ul className="list-disc list-inside space-y-1 text-sm">
            {brief.faq_questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      )}

      {brief.internal_links_plan.length > 0 && (
        <MaillageBlock links={brief.internal_links_plan} />
      )}

      {brief.editorial_notes && (
        <div>
          <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
            Notes éditoriales
          </h4>
          <p className="text-sm whitespace-pre-wrap">{brief.editorial_notes}</p>
        </div>
      )}
    </div>
  );
}

function ArticleContent({ article }: { article: GeneratedArticle }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <span>{article.word_count} mots</span>
        {article.eeat_score && (
          <span>
            Score E-E-A-T :{" "}
            <span
              className={`font-mono ${
                article.eeat_score.overall >= 70
                  ? "text-green-600"
                  : article.eeat_score.overall >= 50
                    ? "text-amber-600"
                    : "text-destructive"
              }`}
            >
              {article.eeat_score.overall}/100
            </span>
          </span>
        )}
      </div>

      {article.eeat_score?.warnings.length ? (
        <Alert>
          <AlertDescription>
            <p className="text-xs font-semibold mb-1">
              À améliorer avant publication :
            </p>
            <ul className="text-xs list-disc list-inside space-y-0.5">
              {article.eeat_score.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      <div>
        <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
          Article Markdown
        </h4>
        <ScrollArea className="h-96 rounded-md border bg-muted/30 p-4">
          <pre className="text-xs whitespace-pre-wrap font-mono">
            {article.content_markdown}
          </pre>
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
  const typeLabel: Record<string, string> = {
    daughter_to_mother: "→ Mère",
    mother_to_daughter: "→ Fille",
    sister_to_sister: "↔ Sœur",
    cross_cocon: "⟶ Cross-cocon",
  };
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
        Maillage interne ({links.length} liens)
      </h4>
      <ul className="space-y-1 text-xs">
        {links.map((l, i) => (
          <li key={i} className="flex items-center gap-2">
            <Badge variant="outline" className="text-xs font-normal">
              {typeLabel[l.link_type] ?? l.link_type}
            </Badge>
            <span className="font-medium">« {l.anchor_text} »</span>
            <span className="text-muted-foreground">→ /{l.target_slug}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BacklinkPanel({ report }: { report: BacklinkReport | undefined }) {
  if (!report) return null;
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        Rapport backlinks
      </h3>
      <Card>
        <CardContent className="pt-6 space-y-4">
          <div>
            <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
              Ratio d&apos;ancres recommandé
            </h4>
            <div className="grid grid-cols-5 gap-2 text-xs">
              <RatioBar label="Exact" value={report.recommended_anchor_ratio.exact} />
              <RatioBar label="Partial" value={report.recommended_anchor_ratio.partial} />
              <RatioBar label="Brand" value={report.recommended_anchor_ratio.branded} />
              <RatioBar label="URL nue" value={report.recommended_anchor_ratio.naked_url} />
              <RatioBar label="Générique" value={report.recommended_anchor_ratio.generic} />
            </div>
          </div>

          <div>
            <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
              Opportunités ({report.opportunities.length})
            </h4>
            <ul className="space-y-2 text-sm">
              {report.opportunities.slice(0, 10).map((o, i) => (
                <li key={i} className="border rounded-md p-3">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="font-medium">{o.referring_domain}</span>
                    <div className="flex items-center gap-2 text-xs">
                      {o.domain_rating != null && (
                        <Badge variant="outline">DR {o.domain_rating}</Badge>
                      )}
                      <Badge>{o.outreach_template_type}</Badge>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">{o.reason}</p>
                  <p className="text-xs mt-1">
                    Ancre suggérée :{" "}
                    <span className="font-mono">« {o.suggested_anchor} »</span>
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
    <div className="text-center">
      <p className="text-muted-foreground">{label}</p>
      <p className="font-mono text-sm">{pct}%</p>
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
