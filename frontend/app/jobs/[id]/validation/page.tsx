"use client";

import { use, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AuthGuard } from "@/components/auth-guard";
import { Checkbox } from "@/components/ui/checkbox";
import { Separator } from "@/components/ui/separator";
import { fetchValidation, submitValidation } from "@/lib/api";
import type {
  KeywordWithData,
  ValidationDecision,
  ValidationSnapshot,
} from "@/lib/types";

/**
 * Écran d'arbitrage de la sélection de mots-clés.
 *
 * L'état est volontairement minimal : par cocon, la liste ordonnée des mots-clés
 * retenus et lequel est la mère. Tout le reste (volume, justification de Claude,
 * appartenance au pool) se dérive du snapshot, qui ne bouge jamais.
 */

type CoconState = {
  index: number;
  theme: string;
  rationale: string;
  selected: string[];
  mother: string | null;
  /**
   * Consignes libres par article, indexées sur le mot-clé.
   *
   * C'est le seul écran où ça peut se saisir : au moment du formulaire, les
   * articles n'existent pas encore (la structure sort après la recherche de
   * mots-clés), et rien n'est encore rédigé ici — c'est donc la dernière
   * occasion d'infléchir la rédaction sans la repayer.
   */
  directives: Record<string, string>;
};

const DIRECTIVE_MAX = 2000; // miroir de ArticleStub.directives côté backend

/**
 * Consignes de l'agence pour un article, repliées par défaut.
 *
 * Une consigne déjà écrite reste visible même repliée (résumée) : sinon
 * l'agence ne saurait plus lesquels de ses 24 articles portent une instruction.
 */
function DirectiveField({
  value,
  open,
  onToggle,
  onChange,
}: {
  value: string;
  open: boolean;
  onToggle: () => void;
  onChange: (text: string) => void;
}) {
  const filled = value.trim() !== "";

  if (!open) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className="mt-2 ml-9 block text-left text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
      >
        {filled ? `Consigne : « ${value.trim().slice(0, 80)}… »` : "+ Ajouter une consigne"}
      </button>
    );
  }

  return (
    <div className="mt-2 ml-9">
      <Textarea
        autoFocus
        rows={3}
        maxLength={DIRECTIVE_MAX}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Insister sur…, ne pas parler de…, angle à privilégier…"
        className="text-sm"
      />
      <div className="mt-1 flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          Éditorial uniquement — le maillage interne reste imposé par l&apos;outil.
        </p>
        <button
          type="button"
          onClick={onToggle}
          className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
        >
          Replier
        </button>
      </div>
    </div>
  );
}

function volumeLabel(v: number | null | undefined): string {
  if (v === null || v === undefined) return "volume inconnu";
  if (v === 0) return "0 recherche/mois";
  return `${v.toLocaleString("fr-FR")}/mois`;
}

/** 0 mesuré et « pas de donnée » ne se traitent pas pareil : le premier condamne. */
function volumeTone(v: number | null | undefined): string {
  if (v === null || v === undefined) return "text-muted-foreground";
  if (v === 0) return "text-destructive";
  if (v >= 500) return "text-foreground font-medium";
  return "text-foreground";
}

function ValidationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: jobId } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const runId = searchParams.get("run");

  const [snapshot, setSnapshot] = useState<ValidationSnapshot | null>(null);
  const [cocoons, setCocoons] = useState<CoconState[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [search, setSearch] = useState("");
  const [openPool, setOpenPool] = useState<number | null>(null);
  // Jusqu'à 24 articles à l'écran : afficher 24 zones de texte en permanence
  // noierait la sélection de mots-clés, qui reste la tâche principale ici.
  const [openDirectives, setOpenDirectives] = useState<Set<string>>(new Set());

  function toggleDirective(coconIndex: number, keyword: string) {
    setOpenDirectives((prev) => {
      const next = new Set(prev);
      const key = `${coconIndex}|${keyword}`;
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }

  useEffect(() => {
    // L'absence de run_id se lit dans l'URL, pas dans un état : la traiter ici
    // déclencherait un rendu en cascade pour une information déjà connue.
    if (!runId) return;
    let cancelled = false;
    fetchValidation(runId)
      .then((snap) => {
        if (cancelled) return;
        setSnapshot(snap);
        setCocoons(
          snap.proposals.map((p) => ({
            index: p.index,
            theme: p.theme,
            rationale: p.rationale,
            selected: p.picks.map((k) => k.keyword),
            mother:
              p.picks.find((k) => k.role === "mother")?.keyword ??
              p.picks[0]?.keyword ??
              null,
            directives: {},
          })),
        );
      })
      .catch((e) => {
        if (!cancelled) {
          setLoadError(e instanceof Error ? e.message : "Chargement impossible");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  // Données du pool indexées par mot-clé — le snapshot est figé, donc ce calcul
  // ne se refait pas à chaque coche.
  const poolByKeyword = useMemo(() => {
    const map = new Map<string, KeywordWithData>();
    snapshot?.pool.forEach((k) => map.set(k.keyword.toLowerCase(), k));
    return map;
  }, [snapshot]);

  const reasonByKeyword = useMemo(() => {
    const map = new Map<string, string>();
    snapshot?.proposals.forEach((p) =>
      p.picks.forEach((k) => {
        if (k.reason) map.set(k.keyword.toLowerCase(), k.reason);
      }),
    );
    return map;
  }, [snapshot]);

  /** Un mot-clé ne peut appartenir qu'à un seul silo — le backend le refuse aussi. */
  const usedElsewhere = useMemo(() => {
    const map = new Map<string, number>();
    cocoons.forEach((c) =>
      c.selected.forEach((kw) => map.set(kw.toLowerCase(), c.index)),
    );
    return map;
  }, [cocoons]);

  const max = snapshot?.max_per_cocon ?? 6;
  const min = snapshot?.min_per_cocon ?? 4;

  function toggle(coconIndex: number, keyword: string) {
    setCocoons((prev) =>
      prev.map((c) => {
        if (c.index !== coconIndex) return c;
        const has = c.selected.includes(keyword);
        if (has) {
          const selected = c.selected.filter((k) => k !== keyword);
          // La consigne part avec l'article : le backend refuse (422) une
          // consigne rattachée à un mot-clé absent du cocon, et il a raison —
          // une consigne orpheline serait ignorée en silence.
          const { [keyword]: _removed, ...directives } = c.directives;
          return {
            ...c,
            selected,
            directives,
            // Décocher la mère : la promotion revient au premier restant, sinon
            // le cocon n'aurait plus de pilier et le formulaire serait bloqué
            // sans que rien ne l'explique.
            mother: c.mother === keyword ? (selected[0] ?? null) : c.mother,
          };
        }
        if (c.selected.length >= max) return c;
        return { ...c, selected: [...c.selected, keyword] };
      }),
    );
  }

  function setMother(coconIndex: number, keyword: string) {
    setCocoons((prev) =>
      prev.map((c) => (c.index === coconIndex ? { ...c, mother: keyword } : c)),
    );
  }

  function setDirective(coconIndex: number, keyword: string, text: string) {
    setCocoons((prev) =>
      prev.map((c) =>
        c.index === coconIndex
          ? { ...c, directives: { ...c.directives, [keyword]: text } }
          : c,
      ),
    );
  }

  function issuesFor(c: CoconState): string | null {
    if (!c.mother) return "Désignez une mère.";
    if (c.selected.length > max)
      return `${c.selected.length} articles — maximum ${max} (1 mère + ${max - 1} filles).`;
    if (c.selected.length < min)
      return `${c.selected.length} articles — minimum ${min} (1 mère + ${min - 1} filles).`;
    return null;
  }

  const blocking = cocoons.map(issuesFor).filter(Boolean) as string[];
  const totalArticles = cocoons.reduce((n, c) => n + c.selected.length, 0);

  async function handleSubmit() {
    if (!runId || blocking.length > 0) return;
    setSubmitting(true);
    const decision: ValidationDecision = {
      cocoons: cocoons.map((c) => ({
        index: c.index,
        theme: c.theme,
        mother_keyword: c.mother as string,
        daughter_keywords: c.selected.filter((k) => k !== c.mother),
        // Filtré deux fois plutôt qu'une : on n'envoie que les consignes non
        // vides ET rattachées à un mot-clé encore sélectionné. Le backend
        // rejetterait le reste, et un 422 sur ce formulaire coûterait à
        // l'agence toute sa saisie.
        directives: Object.fromEntries(
          Object.entries(c.directives).filter(
            ([kw, text]) => text.trim() !== "" && c.selected.includes(kw),
          ),
        ),
      })),
    };
    try {
      const { job_id, articles } = await submitValidation(runId, decision);
      toast.success(`Génération lancée — ${articles} articles`);
      router.push(`/jobs/${job_id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Validation refusée");
      setSubmitting(false);
    }
  }

  const error = runId ? loadError : "Identifiant de run manquant dans l'URL.";

  if (error) {
    return (
      <Shell jobId={jobId}>
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
        <Link href="/" className="text-sm underline mt-4 inline-block">
          Retour à l&apos;accueil
        </Link>
      </Shell>
    );
  }

  if (!snapshot) {
    return (
      <Shell jobId={jobId}>
        <p className="text-sm text-muted-foreground">
          Chargement de la sélection…
        </p>
      </Shell>
    );
  }

  return (
    <Shell jobId={jobId}>
      <div className="space-y-2 mb-6">
        <h1 className="text-2xl font-semibold">Validez la sélection</h1>
        <p className="text-sm text-muted-foreground">
          Claude a retenu ces mots-clés et explique pourquoi. Décochez ce qui ne
          va pas, piochez dans le pool, désignez la mère. Rien n&apos;est généré
          — donc rien n&apos;est facturé — avant votre feu vert.
        </p>
      </div>

      <div className="space-y-6">
        {cocoons.map((c) => {
          const proposal = snapshot.proposals.find((p) => p.index === c.index);
          const issue = issuesFor(c);
          // Proposés par Claude d'abord, puis ce que l'agence a ajouté.
          const rows = [
            ...(proposal?.picks.map((p) => p.keyword) ?? []),
            ...c.selected.filter(
              (kw) => !proposal?.picks.some((p) => p.keyword === kw),
            ),
          ];

          return (
            <Card key={c.index}>
              <CardHeader>
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <CardTitle>{c.theme}</CardTitle>
                    {c.rationale && (
                      <p className="text-sm text-muted-foreground mt-1">
                        {c.rationale}
                      </p>
                    )}
                  </div>
                  <Badge variant={issue ? "destructive" : "secondary"}>
                    {c.selected.length} / {max}
                  </Badge>
                </div>
              </CardHeader>

              <CardContent className="space-y-1">
                {rows.map((kw) => {
                  const checked = c.selected.includes(kw);
                  const data = poolByKeyword.get(kw.toLowerCase());
                  const reason = reasonByKeyword.get(kw.toLowerCase());
                  const isMother = c.mother === kw;
                  const full = !checked && c.selected.length >= max;
                  const owner = usedElsewhere.get(kw.toLowerCase());
                  const takenElsewhere = owner !== undefined && owner !== c.index;

                  return (
                    <div
                      key={kw}
                      className={`rounded-md p-3 ${
                        checked ? "bg-muted/40" : "opacity-60"
                      }`}
                    >
                      <div className="flex items-start gap-3">
                      <Checkbox
                        checked={checked}
                        disabled={full || takenElsewhere}
                        onCheckedChange={() => toggle(c.index, kw)}
                        className="mt-1"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium">{kw}</span>
                          <span className={`text-xs ${volumeTone(data?.monthly_volume)}`}>
                            {volumeLabel(data?.monthly_volume)}
                          </span>
                          {data?.difficulty !== null &&
                            data?.difficulty !== undefined && (
                              <span className="text-xs text-muted-foreground">
                                KD {data.difficulty}
                              </span>
                            )}
                          {!reason && checked && (
                            <Badge variant="outline">ajouté à la main</Badge>
                          )}
                          {takenElsewhere && (
                            <Badge variant="destructive">
                              déjà dans le cocon {(owner as number) + 1}
                            </Badge>
                          )}
                        </div>
                        {reason && (
                          <p className="text-sm text-muted-foreground mt-0.5">
                            {reason}
                          </p>
                        )}
                      </div>
                      {checked && (
                        <button
                          type="button"
                          onClick={() => setMother(c.index, kw)}
                          className={`shrink-0 text-xs rounded-full px-2.5 py-1 border transition-colors ${
                            isMother
                              ? "bg-primary text-primary-foreground border-primary"
                              : "text-muted-foreground hover:bg-muted"
                          }`}
                        >
                          {isMother ? "mère" : "en faire la mère"}
                        </button>
                      )}
                      </div>

                      {checked && (
                        <DirectiveField
                          value={c.directives[kw] ?? ""}
                          open={openDirectives.has(`${c.index}|${kw}`)}
                          onToggle={() => toggleDirective(c.index, kw)}
                          onChange={(text) => setDirective(c.index, kw, text)}
                        />
                      )}
                    </div>
                  );
                })}

                {issue && (
                  <Alert variant="destructive" className="mt-3">
                    <AlertDescription>{issue}</AlertDescription>
                  </Alert>
                )}

                <Separator className="my-3" />

                <button
                  type="button"
                  onClick={() =>
                    setOpenPool(openPool === c.index ? null : c.index)
                  }
                  className="text-sm underline text-muted-foreground hover:text-foreground"
                >
                  {openPool === c.index
                    ? "Fermer le pool"
                    : `Ajouter depuis le pool (${snapshot.pool.length} mots-clés)`}
                </button>

                {openPool === c.index && (
                  <div className="mt-3 space-y-2">
                    <Input
                      placeholder="Filtrer les mots-clés…"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                    <div className="max-h-72 overflow-y-auto border rounded-md divide-y">
                      {snapshot.pool
                        .filter((k) =>
                          k.keyword.toLowerCase().includes(search.toLowerCase()),
                        )
                        .map((k) => {
                          const owner = usedElsewhere.get(
                            k.keyword.toLowerCase(),
                          );
                          const inThis = owner === c.index;
                          const taken = owner !== undefined && !inThis;
                          const full = !inThis && c.selected.length >= max;
                          return (
                            <button
                              key={k.keyword}
                              type="button"
                              disabled={taken || full}
                              onClick={() => toggle(c.index, k.keyword)}
                              className="w-full text-left px-3 py-2 text-sm flex items-center justify-between gap-3 hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent"
                            >
                              <span className="truncate">{k.keyword}</span>
                              <span className="flex items-center gap-2 shrink-0">
                                <span
                                  className={`text-xs ${volumeTone(k.monthly_volume)}`}
                                >
                                  {volumeLabel(k.monthly_volume)}
                                </span>
                                {inThis && <Badge variant="secondary">retenu</Badge>}
                                {taken && (
                                  <Badge variant="outline">
                                    cocon {(owner as number) + 1}
                                  </Badge>
                                )}
                              </span>
                            </button>
                          );
                        })}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      <div className="sticky bottom-0 mt-8 -mx-6 px-6 py-4 border-t bg-background">
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">
            {cocoons.length} cocon{cocoons.length > 1 ? "s" : ""} ·{" "}
            {totalArticles} article{totalArticles > 1 ? "s" : ""} à générer
            {blocking.length > 0 && (
              <span className="text-destructive"> · {blocking[0]}</span>
            )}
          </p>
          <Button
            onClick={handleSubmit}
            disabled={submitting || blocking.length > 0}
          >
            {submitting ? "Lancement…" : "Lancer la génération"}
          </Button>
        </div>
      </div>
    </Shell>
  );
}

function Shell({
  jobId,
  children,
}: {
  jobId: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen">
      <header className="border-b">
        <div className="max-w-4xl mx-auto flex items-center justify-between px-6 py-4">
          <Link
            href="/"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← Accueil
          </Link>
          <span className="text-xs text-muted-foreground font-mono">
            job {jobId}
          </span>
        </div>
      </header>
      <main className="max-w-4xl mx-auto px-6 py-8">{children}</main>
    </div>
  );
}

export default function Page(props: { params: Promise<{ id: string }> }) {
  return (
    <AuthGuard>
      <ValidationPage {...props} />
    </AuthGuard>
  );
}
