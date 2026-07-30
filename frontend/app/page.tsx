import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function Home() {
  return (
    <div className="flex flex-col min-h-screen">
      {/* Header */}
      <header className="border-b bg-background/80 backdrop-blur sticky top-0 z-40">
        <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2 font-semibold">
            <span className="text-lg">🕸️ Cocon</span>
            <Badge variant="outline" className="text-xs">
              Beta
            </Badge>
          </div>
          <nav className="flex items-center gap-4 text-sm">
            <a href="#features" className="text-muted-foreground hover:text-foreground">
              Fonctionnalités
            </a>
            <a href="#pricing" className="text-muted-foreground hover:text-foreground">
              Tarifs
            </a>
            <Link href="/new" className={buttonVariants({ size: "sm" })}>
              Créer un cocon
            </Link>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <section className="max-w-4xl mx-auto px-6 py-24 text-center">
        <Badge variant="secondary" className="mb-6">
          Pour agences SEO françaises premium
        </Badge>
        <h1 className="text-4xl md:text-6xl font-bold tracking-tight mb-6">
          La méthode <span className="text-primary">cocon sémantique</span>,<br />
          automatisée pour vos clients.
        </h1>
        <p className="text-xl text-muted-foreground max-w-2xl mx-auto mb-10">
          Générez des cocons sémantiques rigoureux (méthode Bourrelly) avec un
          maillage interne calibré, des briefs éditoriaux prêts pour vos rédacteurs
          et un rapport backlinks white hat — en quelques minutes.
        </p>
        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <Link href="/new" className={buttonVariants({ size: "lg" })}>
            Générer mon premier cocon
          </Link>
          <a href="#features" className={buttonVariants({ variant: "outline", size: "lg" })}>
            Voir comment ça marche
          </a>
        </div>
        <p className="mt-6 text-xs text-muted-foreground">
          Aucune carte bancaire pour tester · MVP en accès anticipé
        </p>
      </section>

      {/* Value props */}
      <section id="features" className="max-w-6xl mx-auto px-6 py-16 grid md:grid-cols-3 gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">🎯 Rigueur Bourrelly</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Maillage codifié : fille → mère obligatoire, ancres calculées, hiérarchie stricte.
            Pas du «topic cluster» flou des outils anglophones.
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">🔍 Analyse SERP embarquée</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Scraping du top 10 + brief calibré (entités, longueur, structure gagnante) inclus.
            Pas besoin d&apos;un abonnement Surfer en plus.
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">🛡️ Protection anti-deindex</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Score E-E-A-T par article, upload d&apos;éléments d&apos;expérience client,
            zéro marketplace de liens. On garde vos clients sains vis-à-vis de Google.
          </CardContent>
        </Card>
      </section>

      {/* Modes */}
      <section className="bg-muted/40 border-y">
        <div className="max-w-6xl mx-auto px-6 py-16">
          <h2 className="text-3xl font-bold text-center mb-4">Deux modes, votre choix</h2>
          <p className="text-center text-muted-foreground mb-10 max-w-2xl mx-auto">
            Vos rédacteurs sont bons ? Prenez le brief. Pas de temps ? Prenez le premier jet.
          </p>
          <div className="grid md:grid-cols-2 gap-6">
            <Card>
              <CardHeader>
                <Badge variant="secondary" className="w-fit mb-2">Recommandé</Badge>
                <CardTitle>Mode Brief éditorial</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p>✓ Structure H2/H3 calibrée SERP</p>
                <p>✓ Entités et questions PAA à couvrir</p>
                <p>✓ Plan de maillage interne complet</p>
                <p>✓ Suggestions de liens externes</p>
                <p>✓ Notes éditoriales et angle unique</p>
                <p className="text-muted-foreground pt-2">
                  → Livré à vos rédacteurs qui produisent un contenu qui ranke vraiment.
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Mode Génération complète</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p>✓ Tout ce qui est dans le mode Brief</p>
                <p>✓ Article Markdown publish-ready</p>
                <p>✓ FAQ + Schema JSON-LD</p>
                <p>✓ Liens internes résolus</p>
                <p>✓ Score E-E-A-T avec warnings</p>
                <p className="text-muted-foreground pt-2">
                  → Nécessite l&apos;upload d&apos;éléments d&apos;expérience client pour
                  éviter le contenu générique pénalisé par Google.
                </p>
              </CardContent>
            </Card>
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="max-w-4xl mx-auto px-6 py-20 text-center">
        <h2 className="text-3xl font-bold mb-4">Tarifs adaptés aux agences</h2>
        <p className="text-muted-foreground mb-10">
          Pricing final à venir. MVP en accès anticipé gratuit pour les premières agences partenaires.
        </p>
        <Card className="max-w-md mx-auto">
          <CardHeader>
            <CardTitle>Early access</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-4xl font-bold mb-2">Gratuit</p>
            <p className="text-sm text-muted-foreground mb-6">
              Pendant la phase MVP, en échange de feedback détaillé.
            </p>
            <Link href="/new" className={buttonVariants({ className: "w-full" })}>
              Commencer
            </Link>
          </CardContent>
        </Card>
      </section>

      {/* Footer */}
      <footer className="mt-auto border-t">
        <div className="max-w-6xl mx-auto px-6 py-8 text-sm text-muted-foreground flex flex-wrap items-center justify-between gap-2">
          <span>Cocon Sémantique · Automation SEO pour agences françaises</span>
          <span>© {new Date().getFullYear()}</span>
        </div>
      </footer>
    </div>
  );
}
