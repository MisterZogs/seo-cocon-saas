import Link from "next/link";
import {
  ArrowRightIcon,
  FileTextIcon,
  LinkIcon,
  SearchIcon,
  ShieldCheckIcon,
  SparklesIcon,
  TargetIcon,
} from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "#methode", label: "Méthode" },
  { href: "#difference", label: "Différence" },
  { href: "#modes", label: "Modes" },
  { href: "#etapes", label: "Étapes" },
  { href: "#pricing", label: "Tarifs" },
];

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col">
      {/* Bandeau d'annonce — dégradé terracotta, pleine largeur */}
      <div className="bg-[linear-gradient(90deg,var(--primary),color-mix(in_oklch,var(--primary),white_28%))] text-white">
        <div className="mx-auto flex max-w-6xl items-center justify-center gap-2 px-6 py-2.5 text-center text-sm">
          <span className="font-semibold">Accès anticipé</span>
          <span aria-hidden className="opacity-50">
            ·
          </span>
          <Link href="/new" className="hover:underline underline-offset-4">
            Les premières agences partenaires génèrent gratuitement
          </Link>
          <ArrowRightIcon className="size-3.5 opacity-80" />
        </div>
      </div>

      {/* Header */}
      <header className="sticky top-0 z-40 border-b border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <Link href="/" className="font-serif text-xl font-semibold tracking-tight">
            Cocon<span className="text-primary">.</span>
          </Link>
          <nav className="flex items-center gap-1 text-sm sm:gap-6">
            {NAV.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className="hidden text-foreground/80 transition-colors hover:text-primary sm:block"
              >
                {item.label}
              </a>
            ))}
            <Link
              href="/new"
              className={cn(buttonVariants({ size: "sm" }), "h-9 px-4 font-semibold")}
            >
              Créer un cocon
            </Link>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <section className="mx-auto grid w-full max-w-6xl items-center gap-14 px-6 py-20 lg:grid-cols-[1.05fr_1fr] lg:py-28">
        <div>
          {/* `mot-clé` doit rester insécable : le trait d'union offre sinon un
              point de césure et la première ligne casse au milieu du mot. */}
          <h1 className="font-serif text-4xl leading-[1.1] font-semibold text-balance sm:text-5xl lg:text-[3.1rem]">
            Transformez un <span className="whitespace-nowrap">mot-clé</span> en{" "}
            <span className="accent-word-underline">cocon sémantique</span> qui
            se tient.
          </h1>
          <p className="mt-7 max-w-xl text-lg leading-relaxed text-muted-foreground">
            Recherche de mots-clés, analyse SERP, briefs éditoriaux et maillage
            interne calibré à la méthode Bourrelly. Livré à vos rédacteurs en
            quelques minutes, pas en quelques jours.
          </p>
          <div className="mt-9 flex flex-col gap-3 sm:flex-row">
            <Link
              href="/new"
              className={cn(
                buttonVariants({ size: "lg" }),
                "h-13 gap-2 px-8 text-base font-bold shadow-sm",
              )}
            >
              Générer mon premier cocon
              <ArrowRightIcon />
            </Link>
            <a
              href="#etapes"
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "h-13 px-8 text-base font-semibold",
              )}
            >
              Voir comment ça marche
            </a>
          </div>
          <p className="mt-5 text-sm text-muted-foreground">
            Aucune carte bancaire · Marché français · White hat, sans marketplace
            de liens
          </p>
        </div>

        {/* Aperçu produit : la structure d'un cocon, telle qu'elle sort du pipeline */}
        <CoconPreview />
      </section>

      {/* Bandeau chiffres */}
      <section className="border-y border-border bg-surface">
        <div className="mx-auto grid max-w-6xl gap-8 px-6 py-10 sm:grid-cols-3">
          {[
            { value: "30", label: "liens internes par cocon, calculés en code" },
            { value: "6", label: "articles : 1 mère, 5 filles, silo étanche" },
            { value: "0", label: "lien acheté — que du white hat" },
          ].map((stat) => (
            <div key={stat.label} className="text-center">
              <p className="font-serif text-4xl font-semibold text-primary">
                {stat.value}
              </p>
              <p className="mt-2 text-sm text-muted-foreground">{stat.label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Le vrai problème */}
      <section id="methode" className="mx-auto max-w-6xl px-6 py-24">
        <div className="mx-auto max-w-3xl text-center">
          <p className="eyebrow">Le vrai problème</p>
          <h2 className="mt-5 font-serif text-3xl leading-tight font-semibold sm:text-[2.5rem]">
            Vous n&apos;avez pas un problème de rédaction.
            <br />
            Vous avez un problème d&apos;
            <span className="accent-word">architecture</span>.
          </h2>
        </div>

        <div className="mt-14 grid gap-5 md:grid-cols-2">
          {[
            {
              icon: TargetIcon,
              title: "Le maillage se fait à la main",
              body: "Six articles, trente liens, des ancres à choisir une par une. Une heure de tableur par cocon, et une erreur passe inaperçue jusqu'au prochain audit.",
            },
            {
              icon: SearchIcon,
              title: "L'analyse SERP vit dans un autre outil",
              body: "Un abonnement de plus, un export de plus, un copier-coller de plus entre la recherche de mots-clés et le brief que reçoit le rédacteur.",
            },
            {
              icon: LinkIcon,
              title: "Les outils anglophones font du hub/spoke",
              body: "Topic clusters approximatifs, silos poreux, pas de réciprocité entre articles sœurs. Ce n'est pas la méthode que vous vendez à vos clients.",
            },
            {
              icon: ShieldCheckIcon,
              title: "Le contenu générique se fait désindexer",
              body: "HouseFresh, RetroDodo : ce que Google sanctionne, c'est le contenu à l'échelle sans expérience réelle derrière. Il faut pouvoir le prouver.",
            },
          ].map((item) => (
            <article
              key={item.title}
              className="rounded-xl border border-border bg-card p-7 shadow-xs transition-shadow hover:shadow-sm"
            >
              <span className="inline-flex size-11 items-center justify-center rounded-lg bg-accent text-primary">
                <item.icon className="size-5" />
              </span>
              <h3 className="mt-5 font-serif text-lg font-semibold">
                {item.title}
              </h3>
              <p className="mt-2.5 text-[0.95rem] leading-relaxed text-muted-foreground">
                {item.body}
              </p>
            </article>
          ))}
        </div>
      </section>

      {/* Différenciateurs — le cœur de l'argumentaire.
          Tout ce qui est chiffré ici vient de mesures réelles du pipeline
          (run de maillage non normalisé, plafond E-E-A-T). Ne rien y ajouter
          qui ne soit pas vérifiable dans un livrable. */}
      <section
        id="difference"
        className="mx-auto max-w-6xl border-t border-border px-6 py-24"
      >
        <div className="mx-auto max-w-3xl text-center">
          <p className="eyebrow">Ce qui nous distingue</p>
          <h2 className="mt-5 font-serif text-3xl leading-tight font-semibold sm:text-[2.5rem]">
            Tout le monde sait faire rédiger une IA.
            <br />
            Personne ne sait tenir l&apos;
            <span className="accent-word">architecture</span>.
          </h2>
          <p className="mt-5 text-lg leading-relaxed text-muted-foreground">
            La rédaction ne vaut plus rien : elle coûte quelques centimes et
            n&apos;importe qui y a accès. Ce qui reste difficile, c&apos;est la
            structure qui rend ces articles utiles — et la preuve qu&apos;un
            humain était dans la boucle.
          </p>
        </div>

        <div className="mt-14 grid gap-6 lg:grid-cols-2">
          {/* 01 — Maillage */}
          <article className="rounded-xl border border-border bg-card p-8 shadow-xs">
            <div className="flex items-center gap-3">
              <span className="inline-flex size-11 items-center justify-center rounded-lg bg-accent text-primary">
                <LinkIcon className="size-5" />
              </span>
              <p className="eyebrow">Différenciateur 01</p>
            </div>
            <h3 className="mt-5 font-serif text-2xl font-semibold">
              Le maillage est calculé, pas demandé
            </h3>
            <p className="mt-4 text-[0.95rem] leading-relaxed text-muted-foreground">
              Les règles du cocon sont arithmétiques. La mère lie ses cinq
              filles, chaque fille lie la mère et ses quatre sœurs, aucun lien
              ne perce le silo. Trente liens au total, et chaque page reçoit
              exactement cinq liens entrants.
            </p>
            <p className="mt-4 text-[0.95rem] leading-relaxed text-muted-foreground">
              Demandé à un modèle de langage, ce résultat n&apos;arrive jamais.
              Sur un run que nous avons mesuré, sans mise en conformité :
            </p>
            <ul className="mt-5 space-y-2.5 border-l-2 border-primary/25 pl-5 text-[0.95rem] leading-relaxed">
              <li>
                <strong className="font-semibold text-primary">27</strong> des
                40 liens transversaux attendus
              </li>
              <li>aucune réciprocité entre articles sœurs</li>
              <li>une page sans le moindre lien entrant</li>
              <li>
                <strong className="font-semibold text-primary">14</strong> liens
                qui perçaient l&apos;étanchéité entre cocons
              </li>
            </ul>
            <p className="mt-5 text-[0.95rem] leading-relaxed text-muted-foreground">
              Chez nous ces règles ne sont pas dans le prompt, elles sont dans
              le code. Le maillage est complété, nettoyé, et les liens sont
              réécrits jusque dans le corps des articles pour rester cohérents
              avec la map livrée.
            </p>
            <p className="mt-5 rounded-lg bg-accent/60 px-5 py-4 text-[0.95rem] leading-relaxed font-medium">
              Comptez les liens vous-même. C&apos;est probablement la seule
              promesse d&apos;un livrable SEO que vous puissiez vérifier à la
              main en cinq minutes.
            </p>
          </article>

          {/* 02 — Verbatim E-E-A-T */}
          <article className="rounded-xl border border-border bg-card p-8 shadow-xs">
            <div className="flex items-center gap-3">
              <span className="inline-flex size-11 items-center justify-center rounded-lg bg-accent text-primary">
                <ShieldCheckIcon className="size-5" />
              </span>
              <p className="eyebrow">Différenciateur 02</p>
            </div>
            <h3 className="mt-5 font-serif text-2xl font-semibold">
              L&apos;expérience de votre client, mot pour mot
            </h3>

            {/* E-E-A-T n'est pas du vocabulaire courant côté agence : on
                l'explique avant de s'en servir comme argument. */}
            <div className="mt-5 rounded-lg border border-border bg-surface p-5">
              <p className="text-sm font-semibold">E-E-A-T, en clair</p>
              <p className="mt-2 text-[0.9rem] leading-relaxed text-muted-foreground">
                C&apos;est la grille avec laquelle Google fait noter la qualité
                d&apos;une page par ses évaluateurs humains. Quatre critères :
              </p>
              <dl className="mt-4 space-y-2.5 text-[0.9rem] leading-relaxed">
                {[
                  ["Experience", "avez-vous vécu ce dont vous parlez ?"],
                  ["Expertise", "connaissez-vous réellement le sujet ?"],
                  ["Authoritativeness", "les autres vous reconnaissent-ils ?"],
                  ["Trustworthiness", "peut-on vous croire ?"],
                ].map(([term, def]) => (
                  <div key={term} className="flex gap-2">
                    <dt className="font-semibold text-primary">{term}</dt>
                    <dd className="text-muted-foreground">— {def}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-4 text-[0.9rem] leading-relaxed text-muted-foreground">
                Le premier E a été ajouté fin 2022. C&apos;est le seul des
                quatre qu&apos;une IA ne peut pas simuler : elle n&apos;a rien
                vécu.
              </p>
            </div>

            <p className="mt-5 text-[0.95rem] leading-relaxed text-muted-foreground">
              D&apos;où le verbatim. Votre client vous donne un cas réel, une
              donnée maison, un constat de terrain. Ce texte est inséré{" "}
              <strong className="font-semibold text-foreground">
                tel quel
              </strong>
              , en bloc cité et attribué. Le modèle ne le réécrit jamais : il
              pose un marqueur, et le texte exact est substitué en code. Ce sont
              les seuls passages de l&apos;article qui ne sont pas générés — et
              c&apos;est précisément leur intérêt.
            </p>
            <p className="mt-5 rounded-lg bg-accent/60 px-5 py-4 text-[0.95rem] leading-relaxed font-medium">
              Sans bloc verbatim, la note <em>Experience</em> de l&apos;article
              est plafonnée à 40 sur 100, en dur, et l&apos;avertissement est
              écrit noir sur blanc dans le livrable. L&apos;outil refuse de se
              flatter lui-même — vous saurez toujours ce que vous livrez
              vraiment.
            </p>
          </article>
        </div>
      </section>

      {/* Les deux modes */}
      <section id="modes" className="border-y border-border bg-surface">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <div className="mx-auto max-w-2xl text-center">
            <p className="eyebrow">Ce que vous recevez</p>
            <h2 className="mt-5 font-serif text-3xl leading-tight font-semibold sm:text-[2.5rem]">
              Deux modes, <span className="accent-word">votre choix</span>.
            </h2>
            <p className="mt-5 text-lg text-muted-foreground">
              Vos rédacteurs sont bons ? Prenez le brief. Pas le temps ? Prenez
              le premier jet.
            </p>
          </div>

          <div className="mt-14 grid gap-6 md:grid-cols-2">
            <ModeCard
              featured
              eyebrow="Mode 1"
              title="Brief éditorial"
              lede="Livré à vos rédacteurs, qui produisent un contenu qui ranke vraiment."
              items={[
                "Structure H2/H3 calibrée sur le top 10",
                "Entités et questions PAA à couvrir",
                "Plan de maillage interne complet, ancre par ancre",
                "Suggestions de liens externes autoritaires",
                "Notes éditoriales et angle non couvert par la SERP",
              ]}
            />
            <ModeCard
              eyebrow="Mode 2"
              title="Génération complète"
              lede="Nécessite l'upload d'éléments d'expérience client — c'est ce qui évite le contenu générique que Google sanctionne."
              items={[
                "Tout le contenu du mode Brief",
                "Article Markdown prêt à publier",
                "FAQ et Schema JSON-LD générés",
                "Liens internes résolus dans le corps du texte",
                "Score E-E-A-T par article, avec ses avertissements",
              ]}
            />
          </div>
        </div>
      </section>

      {/* Étapes */}
      <section id="etapes" className="mx-auto max-w-6xl px-6 py-24">
        <div className="mx-auto max-w-2xl text-center">
          <p className="eyebrow">Comment ça marche</p>
          <h2 className="mt-5 font-serif text-3xl leading-tight font-semibold sm:text-[2.5rem]">
            Quatre étapes, <span className="accent-word">une validation</span>.
          </h2>
        </div>

        <ol className="mt-14 grid gap-6 md:grid-cols-2 lg:grid-cols-4">
          {[
            {
              n: "01",
              title: "Vous décrivez le client",
              body: "Produit, audience, niche, mots-clés de départ. En français, sans jargon d'outil américain.",
            },
            {
              n: "02",
              title: "On cherche et on regroupe",
              body: "Volumes DataForSEO, analyse SERP du top 10, regroupement des mots-clés en cocons cohérents.",
            },
            {
              n: "03",
              title: "Vous validez les cocons",
              body: "Rien n'est généré avant votre accord sur la structure. Vous gardez la main sur l'arbre.",
            },
            {
              n: "04",
              title: "Vous exportez",
              body: "JSON, Markdown, et bientôt publication WordPress directe dans le site du client.",
            },
          ].map((step) => (
            <li
              key={step.n}
              className="rounded-xl border border-border bg-card p-7 shadow-xs"
            >
              <span className="font-serif text-2xl font-semibold text-primary/35">
                {step.n}
              </span>
              <h3 className="mt-3 font-serif text-lg font-semibold">
                {step.title}
              </h3>
              <p className="mt-2.5 text-[0.95rem] leading-relaxed text-muted-foreground">
                {step.body}
              </p>
            </li>
          ))}
        </ol>
      </section>

      {/* Pricing */}
      <section id="pricing" className="border-y border-border bg-surface">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <div className="mx-auto max-w-2xl text-center">
            <p className="eyebrow">Tarifs</p>
            <h2 className="mt-5 font-serif text-3xl leading-tight font-semibold sm:text-[2.5rem]">
              Vous payez au <span className="accent-word">cocon</span>, pas à
              l&apos;abonnement illimité.
            </h2>
            <p className="mx-auto mt-5 text-lg leading-relaxed text-muted-foreground">
              Un cocon, c&apos;est un article mère, cinq filles et le maillage
              complet entre les six. Les cocons non consommés sont reportés un
              mois, et un cocon qui échoue n&apos;est jamais décompté.
            </p>
          </div>

          <div className="mt-14 grid gap-6 lg:grid-cols-3">
            <PriceCard
              name="Indépendant"
              price="49 €"
              volume="3 cocons par mois"
              unit="soit 16 € le cocon"
              lede="Pour un consultant SEO qui gère son propre portefeuille de clients."
              items={[
                "Mode Brief et Génération complète",
                "Volumes DataForSEO réels, analyse du top 10",
                "Maillage complet, vérifié en code",
                "Export JSON et Markdown",
                "Report des cocons non utilisés sur 1 mois",
              ]}
            />
            <PriceCard
              featured
              name="Agence"
              price="249 €"
              volume="20 cocons par mois"
              unit="soit 12 € le cocon"
              lede="Le format d'une agence qui suit 20 à 30 clients en référencement."
              items={[
                "Tout ce que contient Indépendant",
                "Score E-E-A-T par article, plafond honnête",
                "Rapport backlinks stratégique par cocon",
                "Jusqu'à 4 cocons par génération",
                "Support prioritaire",
              ]}
            />
            <PriceCard
              name="Studio"
              price="690 €"
              volume="60 cocons par mois"
              unit="soit 11 € le cocon"
              lede="Pour les structures qui produisent du contenu en continu."
              items={[
                "Tout ce que contient Agence",
                "White-label sur les livrables (bientôt)",
                "Publication WordPress directe (bientôt)",
                "Projets clients séparés",
                "Accompagnement à la prise en main",
              ]}
            />
          </div>

          <p className="mt-8 text-center text-[0.95rem] text-muted-foreground">
            Sans abonnement :{" "}
            <strong className="font-semibold text-foreground">
              20 € le cocon
            </strong>{" "}
            à l&apos;unité. Au-delà de 60 cocons par mois, sur devis.
          </p>

          <div className="mx-auto mt-14 max-w-2xl rounded-2xl border border-primary/30 bg-card p-8 text-center shadow-md">
            <p className="eyebrow">Accès anticipé</p>
            <p className="mt-4 font-serif text-3xl font-semibold">
              Gratuit pour les premières agences partenaires
            </p>
            <p className="mx-auto mt-3 max-w-lg text-[0.95rem] leading-relaxed text-muted-foreground">
              En échange d&apos;un retour détaillé sur les cocons produits. Sans
              engagement, sans carte bancaire. La grille ci-dessus s&apos;appliquera
              au lancement.
            </p>
            <Link
              href="/new"
              className={cn(
                buttonVariants({ size: "lg" }),
                "mt-7 h-12 gap-2 px-8 text-base font-bold",
              )}
            >
              Commencer
              <ArrowRightIcon />
            </Link>
          </div>
        </div>
      </section>

      {/* CTA final */}
      <section className="mx-auto max-w-3xl px-6 py-24 text-center">
        <h2 className="font-serif text-3xl leading-tight font-semibold sm:text-[2.6rem]">
          Vos cocons méritent mieux
          <br />
          qu&apos;un <span className="accent-word">tableur</span>.
        </h2>
        <p className="mt-5 text-lg text-muted-foreground">
          Décrivez un client, validez la structure, récupérez les briefs.
        </p>
        <Link
          href="/new"
          className={cn(
            buttonVariants({ size: "lg" }),
            "mt-9 h-13 gap-2 px-8 text-base font-bold shadow-sm",
          )}
        >
          Générer mon premier cocon
          <ArrowRightIcon />
        </Link>
      </section>

      {/* Footer */}
      <footer className="mt-auto border-t border-border">
        <div className="mx-auto max-w-6xl px-6 py-12 text-center">
          <p className="font-serif text-xl font-semibold tracking-tight">
            Cocon<span className="text-primary">.</span>
          </p>
          <p className="mt-2 text-sm text-muted-foreground">
            Automation SEO pour agences françaises · © {new Date().getFullYear()}
          </p>
        </div>
      </footer>
    </div>
  );
}

function PriceCard({
  name,
  price,
  volume,
  unit,
  lede,
  items,
  featured = false,
}: {
  name: string;
  price: string;
  volume: string;
  unit: string;
  lede: string;
  items: string[];
  featured?: boolean;
}) {
  return (
    <article
      className={cn(
        "flex flex-col rounded-xl border border-border bg-card p-8 shadow-xs",
        featured && "border-primary/40 shadow-md ring-1 ring-primary/15",
      )}
    >
      <div className="flex items-center gap-3">
        <p className="eyebrow">{name}</p>
        {featured && (
          <span className="rounded-full bg-accent px-2.5 py-0.5 text-xs font-semibold text-primary">
            Le plus choisi
          </span>
        )}
      </div>
      <p className="mt-4 font-serif text-4xl font-semibold">
        {price}
        <span className="ml-1 text-base font-normal text-muted-foreground">
          /mois
        </span>
      </p>
      <p className="mt-2 font-semibold text-primary">{volume}</p>
      <p className="text-sm text-muted-foreground">{unit}</p>
      <p className="mt-4 text-[0.9rem] leading-relaxed text-muted-foreground">
        {lede}
      </p>
      <ul className="mt-6 space-y-3">
        {items.map((item) => (
          <li key={item} className="flex gap-3 text-[0.92rem] leading-relaxed">
            <SparklesIcon className="mt-1 size-4 shrink-0 text-primary" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </article>
  );
}

function ModeCard({
  eyebrow,
  title,
  lede,
  items,
  featured = false,
}: {
  eyebrow: string;
  title: string;
  lede: string;
  items: string[];
  featured?: boolean;
}) {
  return (
    <article
      className={cn(
        "rounded-xl border border-border bg-card p-8 shadow-xs",
        featured && "border-t-4 border-t-primary shadow-md",
      )}
    >
      <div className="flex items-center gap-3">
        <p className="eyebrow">{eyebrow}</p>
        {featured && (
          <span className="rounded-full bg-accent px-2.5 py-0.5 text-xs font-semibold text-primary">
            Recommandé
          </span>
        )}
      </div>
      <h3 className="mt-3 font-serif text-2xl font-semibold">{title}</h3>
      <ul className="mt-6 space-y-3">
        {items.map((item) => (
          <li key={item} className="flex gap-3 text-[0.95rem] leading-relaxed">
            <SparklesIcon className="mt-1 size-4 shrink-0 text-primary" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
      <p className="mt-6 border-t border-border pt-5 text-[0.95rem] leading-relaxed text-muted-foreground">
        {lede}
      </p>
    </article>
  );
}

/*
  Aperçu statique de la structure produite : article mère + 5 filles, avec le
  compte de liens entrants qui vaut 5 pour chaque page — l'arithmétique décrite
  dans CLAUDE.md, montrée telle quelle.
*/
function CoconPreview() {
  const filles = [
    "Choisir ses mots-clés de longue traîne",
    "Structurer un article pilier",
    "Rédiger des ancres de liens internes",
    "Auditer un maillage existant",
    "Mesurer l'autorité thématique",
  ];

  return (
    <div className="rounded-2xl border border-border bg-card p-6 shadow-lg sm:p-8">
      <div className="flex items-center justify-between">
        <p className="eyebrow">Cocon généré</p>
        <span className="rounded-full bg-success-soft px-2.5 py-1 text-xs font-semibold text-success-strong">
          Silo étanche
        </span>
      </div>

      <div className="mt-6 rounded-lg border-l-4 border-primary bg-accent p-4">
        <div className="flex items-center gap-2">
          <FileTextIcon className="size-4 text-primary" />
          <p className="text-xs font-semibold tracking-wide text-primary uppercase">
            Article mère
          </p>
        </div>
        <p className="mt-2 font-serif text-base font-semibold">
          Cocon sémantique : le guide de la méthode Bourrelly
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          5 liens sortants · 5 liens entrants
        </p>
      </div>

      <ul className="mt-4 space-y-2">
        {filles.map((titre) => (
          <li
            key={titre}
            className="flex items-center justify-between gap-3 rounded-lg border border-border bg-background px-4 py-3"
          >
            <span className="text-sm">{titre}</span>
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              ←5
            </span>
          </li>
        ))}
      </ul>

      <p className="mt-5 border-t border-border pt-4 text-xs text-muted-foreground">
        30 liens internes, réciprocité complète entre articles sœurs, aucun lien
        vers un autre cocon.
      </p>
    </div>
  );
}
