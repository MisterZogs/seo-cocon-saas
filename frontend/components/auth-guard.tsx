"use client";

import { useEffect, useSyncExternalStore } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { clearSession, getAgency, isAuthenticated, redirectToLogin } from "@/lib/auth";

/**
 * `useSyncExternalStore` plutôt qu'un `useState` posé dans un effet.
 *
 * La contrainte : `localStorage` n'existe pas au rendu serveur, donc la session
 * ne peut pas être lue pendant le premier rendu. La solution naïve — état à
 * `false` puis `setState` dans un effet — provoque un rendu en cascade
 * (React la refuse via `react-hooks/set-state-in-effect`). Ici le « snapshot
 * serveur » vaut la valeur de repli, le « snapshot client » lit vraiment le
 * stockage, et React fait la bascule sans re-rendu superflu.
 *
 * Pas d'abonnement : la session ne change pas sous les pieds du composant —
 * connexion et déconnexion rechargent la page.
 */
const noSubscription = () => () => {};

/**
 * Barrière d'accès aux écrans agence.
 *
 * Le vrai contrôle est côté API — ce composant ne protège aucune donnée, il
 * évite seulement d'afficher une page qui se remplirait d'erreurs 401.
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const ready = useSyncExternalStore(
    noSubscription,
    () => isAuthenticated(),
    () => false, // au rendu serveur, on ne sait pas encore
  );

  useEffect(() => {
    if (!ready) redirectToLogin();
  }, [ready]);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-muted-foreground">Vérification de la session…</p>
      </div>
    );
  }
  return <>{children}</>;
}

/** Nom de l'agence connectée + déconnexion, à poser dans les en-têtes. */
export function AccountMenu() {
  const [name, setName] = useState<string | null>(null);

  // Même raison que ci-dessus : la session ne se lit qu'une fois monté côté
  // client, sinon le HTML rendu au serveur et celui du client divergent.
  useEffect(() => setName(getAgency()?.name ?? null), []);

  if (!name) return null;

  return (
    <div className="flex items-center gap-3">
      <Link
        href="/runs"
        className="hidden text-sm text-muted-foreground hover:text-foreground sm:inline"
      >
        {name}
      </Link>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => {
          clearSession();
          window.location.href = "/login";
        }}
      >
        Déconnexion
      </Button>
    </div>
  );
}
