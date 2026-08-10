"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { login, register } from "@/lib/api";
import { isAuthenticated, saveSession } from "@/lib/auth";

/** Doit rester aligné sur `MIN_PASSWORD_LENGTH` dans backend/auth.py. */
const MIN_PASSWORD_LENGTH = 10;

type Mode = "login" | "register";

/**
 * Destination post-connexion, ramenée à un chemin interne.
 *
 * `next` vient de l'URL, donc de l'utilisateur : le laisser passer tel quel
 * ouvrirait une redirection arbitraire. `startsWith("/")` ne suffit pas —
 * `//evil.example` est une URL protocole-relatif qui commence bien par « / » et
 * sort pourtant du site.
 */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/new";
  return raw;
}

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Page où retourner après connexion, posée par `redirectToLogin`. Lue depuis
  // `window` plutôt qu'avec `useSearchParams`, qui imposerait un <Suspense>
  // autour de toute la page au prérendu.
  const [next, setNext] = useState("/new");

  useEffect(() => {
    const target = safeNext(new URLSearchParams(window.location.search).get("next"));
    setNext(target);
    // Déjà connecté : rien à faire ici.
    if (isAuthenticated()) window.location.href = target;
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (mode === "register" && password.length < MIN_PASSWORD_LENGTH) {
      setError(`Le mot de passe doit faire au moins ${MIN_PASSWORD_LENGTH} caractères.`);
      return;
    }

    setBusy(true);
    try {
      const session =
        mode === "login"
          ? await login({ email, password })
          : await register({ email, password, name });
      saveSession(session);
      // Rechargement complet plutôt que `router.push` : les écrans agence
      // lisent la session au montage, une navigation client les laisserait
      // avec l'état d'avant connexion.
      window.location.href = next;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Erreur inconnue");
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
            ← Retour
          </Link>
          <span className="text-sm font-medium">Espace agence</span>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-12">
        <h1 className="font-serif text-2xl">
          {mode === "login" ? "Connexion" : "Créer un compte agence"}
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {mode === "login"
            ? "Vos cocons, vos briefs et votre historique sont rattachés à votre compte."
            : "Un compte par agence. Vos générations ne sont visibles que par vous."}
        </p>

        <Card className="mt-6">
          <CardContent className="pt-6">
            <form onSubmit={submit} className="space-y-4">
              {mode === "register" && (
                <div className="space-y-2">
                  <Label htmlFor="name">Nom de l&apos;agence</Label>
                  <Input
                    id="name"
                    required
                    minLength={2}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Agence Exemple"
                  />
                </div>
              )}

              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="contact@agence.fr"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="password">Mot de passe</Label>
                <Input
                  id="password"
                  type="password"
                  required
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                {mode === "register" && (
                  <p className="text-xs text-muted-foreground">
                    {MIN_PASSWORD_LENGTH} caractères minimum. Aucune règle de
                    composition : une phrase longue vaut mieux qu&apos;un mot court
                    truffé de symboles.
                  </p>
                )}
              </div>

              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}

              <Button type="submit" className="w-full" disabled={busy}>
                {busy
                  ? "Un instant…"
                  : mode === "login"
                    ? "Se connecter"
                    : "Créer le compte"}
              </Button>
            </form>

            <p className="mt-6 text-center text-sm text-muted-foreground">
              {mode === "login" ? "Pas encore de compte ?" : "Déjà un compte ?"}{" "}
              <button
                type="button"
                className="font-medium text-foreground underline underline-offset-4"
                onClick={() => {
                  setMode(mode === "login" ? "register" : "login");
                  setError(null);
                }}
              >
                {mode === "login" ? "Créer un compte" : "Se connecter"}
              </button>
            </p>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
