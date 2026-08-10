"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { clearSession, getAgency, isAuthenticated, redirectToLogin } from "@/lib/auth";

/**
 * Barrière d'accès aux écrans agence.
 *
 * Le vrai contrôle est côté API — ce composant ne protège aucune donnée, il
 * évite seulement d'afficher une page qui se remplirait d'erreurs 401. La
 * vérification a lieu dans un effet et pas au rendu parce que `localStorage`
 * n'existe pas pendant le rendu serveur : lire le jeton plus tôt renverrait
 * toujours « pas connecté » et déclencherait une redirection permanente.
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (isAuthenticated()) {
      setReady(true);
    } else {
      redirectToLogin();
    }
  }, []);

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
