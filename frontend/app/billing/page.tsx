"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AccountMenu, AuthGuard } from "@/components/auth-guard";
import {
  fetchBalance,
  fetchLedger,
  fetchOffers,
  openPortal,
  startCheckout,
} from "@/lib/api";
import type {
  BalanceResponse,
  BillingOffers,
  CocoonLot,
  LedgerEntry,
} from "@/lib/types";

const DATE_FMT = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "medium",
  timeStyle: "short",
});

const DAY_FMT = new Intl.DateTimeFormat("fr-FR", { dateStyle: "long" });

const LOT_LABELS: Record<CocoonLot["kind"], string> = {
  trial: "Essai gratuit",
  subscription: "Allocation mensuelle",
  purchase: "Achat à l'unité",
  manual: "Crédit manuel",
};

const ENTRY_LABELS: Record<LedgerEntry["kind"], string> = {
  grant: "Crédit",
  debit_generation: "Génération",
  debit_regeneration: "Régénération d'article",
  refund: "Remboursement",
};

/**
 * Un mouvement s'affiche en cocons, mais se calcule en unités (1 cocon = 6).
 * Le sixième est la granularité d'une régénération d'article, et 1/6 n'a pas
 * d'écriture décimale exacte — d'où l'affichage en fraction plutôt qu'en
 * décimal arrondi, qui ferait apparaître des « 0,17 cocon ».
 */
function formatUnits(units: number, perCocoon: number): string {
  const sign = units > 0 ? "+" : "−";
  const abs = Math.abs(units);
  const whole = Math.floor(abs / perCocoon);
  const rest = abs % perCocoon;
  if (rest === 0) return `${sign}${whole} cocon${whole > 1 ? "s" : ""}`;
  if (whole === 0) return `${sign}${rest}/${perCocoon} de cocon`;
  return `${sign}${whole} + ${rest}/${perCocoon} cocons`;
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      balance: BalanceResponse;
      entries: LedgerEntry[];
      perCocoon: number;
      offers: BillingOffers;
    };

function BillingPage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Retour de Stripe. Le solde n'est pas forcément à jour à la seconde où
  // l'agence revient : le crédit arrive par webhook, en parallèle de la
  // redirection. D'où le message d'attente plutôt qu'un solde affirmé.
  const [paid, setPaid] = useState<"ok" | "annule" | null>(null);
  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get("paiement");
    if (value === "ok" || value === "annule") setPaid(value);
  }, []);

  async function go(action: () => Promise<string>, key: string) {
    setBusy(key);
    setActionError(null);
    try {
      window.location.href = await action();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Erreur inconnue");
      setBusy(null);
    }
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchBalance(), fetchLedger(), fetchOffers()])
      .then(([balance, ledger, offers]) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          balance,
          entries: ledger.entries,
          perCocoon: ledger.units_per_cocoon,
          offers,
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Erreur inconnue",
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-screen">
      <header className="border-b">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
            ← Retour
          </Link>
          <div className="flex items-center gap-4">
            <span className="text-sm font-medium">Solde et consommation</span>
            <AccountMenu />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-6 py-10">
        {state.kind === "loading" && (
          <p className="text-sm text-muted-foreground">Chargement du solde…</p>
        )}

        {state.kind === "error" && (
          <Alert variant="destructive">
            <AlertDescription>{state.message}</AlertDescription>
          </Alert>
        )}

        {paid === "ok" && (
          <Alert className="mb-4">
            <AlertDescription>
              Paiement accepté, merci. Les cocons sont crédités par Stripe dans
              les secondes qui suivent — rafraîchissez la page s&apos;ils
              n&apos;apparaissent pas encore.
            </AlertDescription>
          </Alert>
        )}
        {paid === "annule" && (
          <Alert className="mb-4">
            <AlertDescription>
              Paiement abandonné — rien n&apos;a été débité.
            </AlertDescription>
          </Alert>
        )}
        {actionError && (
          <Alert variant="destructive" className="mb-4">
            <AlertDescription>{actionError}</AlertDescription>
          </Alert>
        )}

        {state.kind === "ready" && (
          <>
            <Card>
              <CardContent className="py-6">
                <p className="text-sm text-muted-foreground">Solde disponible</p>
                <p className="mt-1 font-serif text-3xl">{state.balance.balance_label}</p>
                <p className="mt-3 text-sm text-muted-foreground">
                  Formule <span className="font-medium text-foreground">{state.balance.plan_label}</span>
                  {state.balance.cocoons_per_month > 0 && (
                    <>
                      {" "}
                      — {state.balance.cocoons_per_month} cocons par mois,{" "}
                      {state.balance.monthly_price_eur} €/mois
                    </>
                  )}
                </p>
              </CardContent>
            </Card>

            {state.balance.balance_units === 0 && (
              <Alert className="mt-4">
                <AlertDescription>
                  Solde épuisé. Une génération lancée maintenant serait refusée —
                  la recherche de mots-clés reste offerte, c&apos;est le passage à la
                  rédaction qui est débité.
                </AlertDescription>
              </Alert>
            )}

            <h2 className="mt-10 font-serif text-xl">Formules</h2>
            {!state.offers.payments_enabled && (
              <Alert className="mt-3">
                <AlertDescription>
                  Le paiement en ligne n&apos;est pas activé sur ce serveur. Les
                  tarifs ci-dessous sont indicatifs : contactez-nous pour
                  souscrire, nous créditons votre compte manuellement.
                </AlertDescription>
              </Alert>
            )}

            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              {state.offers.plans.map((plan) => {
                const current = plan.key === state.offers.current_plan;
                return (
                  <Card key={plan.key} className={current ? "border-primary" : undefined}>
                    <CardContent className="py-5">
                      <div className="flex items-center justify-between">
                        <p className="font-medium">{plan.label}</p>
                        {current && <Badge>Formule actuelle</Badge>}
                      </div>
                      <p className="mt-2 font-serif text-2xl">
                        {plan.monthly_price_eur} €
                        <span className="text-sm text-muted-foreground"> /mois</span>
                      </p>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {plan.cocoons_per_month} cocons par mois, soit{" "}
                        {Math.round(
                          (plan.monthly_price_eur / plan.cocoons_per_month) * 10,
                        ) / 10}{" "}
                        € le cocon
                      </p>
                      {state.offers.payments_enabled && !current && (
                        <Button
                          className="mt-4 w-full"
                          size="sm"
                          disabled={busy !== null}
                          onClick={() =>
                            go(() => startCheckout({ plan: plan.key }), plan.key)
                          }
                        >
                          {busy === plan.key ? "Ouverture…" : "Souscrire"}
                        </Button>
                      )}
                    </CardContent>
                  </Card>
                );
              })}
            </div>

            {state.offers.payments_enabled && (
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy !== null}
                  onClick={() => go(() => startCheckout({ cocoons: 1 }), "unit")}
                >
                  {busy === "unit"
                    ? "Ouverture…"
                    : `Acheter 1 cocon — ${state.offers.unit_price_eur} €`}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy !== null}
                  onClick={() => go(openPortal, "portal")}
                >
                  {busy === "portal" ? "Ouverture…" : "Factures et résiliation"}
                </Button>
              </div>
            )}

            <h2 className="mt-10 font-serif text-xl">Vos cocons disponibles</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Les cocons sont consommés en commençant par ceux qui expirent le
              plus tôt. Une allocation mensuelle non utilisée reste valable le
              mois suivant, puis elle est perdue.
            </p>

            {state.balance.lots.length === 0 ? (
              <p className="mt-4 text-sm text-muted-foreground">Aucun cocon disponible.</p>
            ) : (
              <ul className="mt-4 space-y-2">
                {state.balance.lots.map((lot) => (
                  <li key={lot.id}>
                    <Card>
                      <CardContent className="flex flex-wrap items-center gap-x-4 gap-y-1 py-3">
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium">{LOT_LABELS[lot.kind]}</p>
                          <p className="text-xs text-muted-foreground">
                            {lot.expires_at
                              ? `Expire le ${DAY_FMT.format(new Date(lot.expires_at))}`
                              : "Sans expiration"}
                          </p>
                        </div>
                        <Badge variant="outline">
                          {formatUnits(
                            lot.remaining_units,
                            state.perCocoon,
                          ).replace("+", "")}
                        </Badge>
                      </CardContent>
                    </Card>
                  </li>
                ))}
              </ul>
            )}

            <h2 className="mt-10 font-serif text-xl">Journal</h2>
            {state.entries.length === 0 ? (
              <p className="mt-4 text-sm text-muted-foreground">Aucun mouvement.</p>
            ) : (
              <ul className="mt-4 divide-y rounded-lg border">
                {state.entries.map((entry) => (
                  <li
                    key={entry.id}
                    className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-sm">
                        {ENTRY_LABELS[entry.kind]}
                        {/* Un débit annulé reste au journal : il explique
                            pourquoi le solde est remonté. */}
                        {entry.reversed_at && (
                          <span className="ml-2 text-xs text-muted-foreground">
                            annulé
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {DATE_FMT.format(new Date(entry.created_at))}
                        {entry.note && ` · ${entry.note}`}
                      </p>
                    </div>
                    <span
                      className={
                        entry.delta_units > 0
                          ? "text-sm font-medium text-success"
                          : "text-sm font-medium"
                      }
                    >
                      {formatUnits(entry.delta_units, state.perCocoon)}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-10">
              <Link href="/new" className={buttonVariants({ size: "sm" })}>
                Nouvelle génération
              </Link>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

export default function Page() {
  return (
    <AuthGuard>
      <BillingPage />
    </AuthGuard>
  );
}
