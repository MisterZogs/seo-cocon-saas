"use client";

import { useState } from "react";
import { CheckIcon, TriangleAlertIcon, UploadIcon } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { exportToWordPress } from "@/lib/api";
import type { WordPressExportReport } from "@/lib/types";

/**
 * Publication du livrable dans le WordPress du client.
 *
 * Les identifiants ne sont **jamais** mémorisés — ni en état persistant, ni en
 * `localStorage`. Un mot de passe d'application donne un accès en écriture au
 * site d'un client de l'agence : il se retape à chaque export, et c'est le prix
 * assumé pour ne le stocker nulle part.
 */
export function WordPressExport({ runId }: { runId: string | null }) {
  const [open, setOpen] = useState(false);
  const [siteUrl, setSiteUrl] = useState("");
  const [username, setUsername] = useState("");
  const [appPassword, setAppPassword] = useState("");
  const [status, setStatus] = useState("draft");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<WordPressExportReport | null>(null);

  if (!runId) return null;

  async function submit() {
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      const res = await exportToWordPress(
        runId!,
        {
          site_url: siteUrl.trim(),
          username: username.trim(),
          app_password: appPassword,
        },
        { status },
      );
      setReport(res);
      // Le mot de passe ne reste pas en mémoire une seconde de plus que
      // nécessaire — l'export est terminé, il n'a plus d'usage.
      setAppPassword("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export impossible");
    } finally {
      setBusy(false);
    }
  }

  const ready = siteUrl.trim() && username.trim() && appPassword;

  return (
    <>
      {/* Bouton hors du Dialog plutôt qu'un DialogTrigger : Base UI compose par
          `render` et non par `asChild`, et piloter l'ouverture par l'état est
          plus lisible que de deviner l'API de composition. */}
      <Button
        size="sm"
        variant="outline"
        className="gap-1.5"
        onClick={() => setOpen(true)}
      >
        <UploadIcon className="h-3.5 w-3.5" />
        Export WordPress
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Publier dans le WordPress du client</DialogTitle>
            <DialogDescription>
              Les articles sont créés en brouillon, puis leurs liens internes
              sont réécrits avec les URL réelles une fois toutes les pages en
              place.
            </DialogDescription>
          </DialogHeader>

          {report ? (
            <ExportReport report={report} onRestart={() => setReport(null)} />
          ) : (
            <div className="space-y-4">
              {error && (
                <Alert variant="destructive">
                  <TriangleAlertIcon className="h-4 w-4" />
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}

              <div className="space-y-1.5">
                <Label htmlFor="wp-url">URL du site</Label>
                <Input
                  id="wp-url"
                  value={siteUrl}
                  onChange={(e) => setSiteUrl(e.target.value)}
                  placeholder="client.fr"
                  autoComplete="off"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="wp-user">Identifiant WordPress</Label>
                <Input
                  id="wp-user"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="off"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="wp-pass">Mot de passe d&apos;application</Label>
                <Input
                  id="wp-pass"
                  type="password"
                  value={appPassword}
                  onChange={(e) => setAppPassword(e.target.value)}
                  placeholder="xxxx xxxx xxxx xxxx xxxx xxxx"
                  autoComplete="new-password"
                />
                <p className="text-xs text-muted-foreground">
                  Dans WordPress :{" "}
                  <strong>
                    Utilisateurs → Profil → Mots de passe d&apos;application
                  </strong>
                  . Ce n&apos;est pas le mot de passe du compte, et il se
                  révoque d&apos;un clic. Nous ne le stockons pas — il faudra le
                  ressaisir au prochain export.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="wp-status">Statut des articles</Label>
                <Select
                  value={status}
                  onValueChange={(v) => setStatus(v ?? "draft")}
                >
                  <SelectTrigger id="wp-status">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="draft">
                      Brouillon (recommandé)
                    </SelectItem>
                    <SelectItem value="pending">
                      En attente de relecture
                    </SelectItem>
                    <SelectItem value="private">Privé</SelectItem>
                    <SelectItem value="publish">
                      Publié immédiatement
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <Button
                onClick={submit}
                disabled={!ready || busy}
                className="w-full"
              >
                {busy ? "Publication en cours…" : "Publier"}
              </Button>
              {busy && (
                <p className="text-xs text-muted-foreground text-center">
                  Deux passes sur chaque article — comptez quelques secondes.
                </p>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

function ExportReport({
  report,
  onRestart,
}: {
  report: WordPressExportReport;
  onRestart: () => void;
}) {
  const crees = report.posts.filter((p) => p.created).length;
  const nonMailles = report.posts.filter((p) => !p.linked);

  return (
    <div className="space-y-4">
      <Alert variant={report.errors.length ? "destructive" : "default"}>
        {report.errors.length ? (
          <TriangleAlertIcon className="h-4 w-4" />
        ) : (
          <CheckIcon className="h-4 w-4" />
        )}
        <AlertDescription>
          {report.posts.length} article(s) sur {report.site_url} — {crees}{" "}
          créé(s), {report.posts.length - crees} mis à jour,{" "}
          <strong>{report.internal_links_resolved} liens internes posés</strong>
          .
        </AlertDescription>
      </Alert>

      {report.errors.length > 0 && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wide">
            Échecs
          </p>
          {report.errors.map((e) => (
            <p key={e} className="text-xs text-muted-foreground">
              {e}
            </p>
          ))}
        </div>
      )}

      {nonMailles.length > 0 && (
        <p className="text-xs text-muted-foreground">
          ⚠️ {nonMailles.length} page(s) publiée(s) sans leurs liens internes.
          Relancez l&apos;export : il met à jour au lieu de dupliquer.
        </p>
      )}

      <div className="space-y-1.5">
        {report.posts.map((post) => (
          <div
            key={post.slug}
            className="flex items-center justify-between gap-2 rounded border px-3 py-2 text-sm"
          >
            <div className="min-w-0">
              <p className="truncate font-medium">{post.title}</p>
              <a
                href={post.url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-muted-foreground hover:underline truncate block"
              >
                {post.url}
              </a>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              {post.is_mother && <Badge className="text-xs">Mère</Badge>}
              <Badge variant="outline" className="text-xs">
                {post.internal_links} liens
              </Badge>
              <Badge
                variant={post.created ? "secondary" : "outline"}
                className="text-xs"
              >
                {post.created ? "créé" : "màj"}
              </Badge>
            </div>
          </div>
        ))}
      </div>

      <Button variant="outline" onClick={onRestart} className="w-full">
        Nouvel export
      </Button>
    </div>
  );
}
