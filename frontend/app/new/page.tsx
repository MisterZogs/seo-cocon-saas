"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useForm, useFieldArray } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { createGeneration } from "@/lib/api";
import type { ClientForm as ApiClientForm } from "@/lib/types";

const STEPS = [
  { id: 1, label: "Contexte client" },
  { id: 2, label: "Mots-clés seeds" },
  { id: 3, label: "Mode & options" },
  { id: 4, label: "Récapitulatif" },
] as const;

// 1 mère + 5 filles. Le nombre de filles est arrêté par le modèle au design du
// cocon (3 à 8), donc c'est une estimation — miroir de TYPICAL_ARTICLES_PER_COCON
// côté backend (models.py), qui reste la source de vérité.
const TYPICAL_ARTICLES_PER_COCON = 6;

const formSchema = z
  .object({
    product: z.string().min(2, "Nom du produit requis (min 2 caractères)"),
    description: z.string().min(30, "Description trop courte (min 30 caractères)"),
    niche: z.string().min(2, "Niche requise"),
    audience: z.string().min(20, "Description d'audience trop courte (min 20 caractères)"),
    language: z.string().min(2),
    seed_keywords_text: z
      .string()
      .min(1, "Au moins 1 mot-clé seed requis")
      .refine(
        (v) => v.split(/[\n,]+/).filter((k) => k.trim()).length >= 1,
        "Au moins 1 mot-clé requis",
      ),
    num_cocoons: z.number().int().min(1).max(4),
    mode: z.enum(["brief", "full"]),
    validate_keywords: z.boolean(),
    experience_elements: z.array(
      z.object({
        type: z.enum(["case_study", "data", "screenshot", "insight", "quote"]),
        title: z.string().min(1),
        content: z.string().min(20),
      }),
    ),
    style_samples: z
      .array(
        z.object({
          title: z.string(),
          // 200 caractères : en dessous, l'échantillon ne porte pas assez de
          // signal stylistique pour infléchir la génération.
          content: z.string().min(200, "Échantillon trop court (min 200 caractères)"),
        }),
      )
      .max(5, "5 échantillons maximum"),
  })
  .superRefine((data, ctx) => {
    if (data.mode === "full" && data.experience_elements.length === 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["experience_elements"],
        message:
          "Le mode Génération complète exige au moins 1 élément d'expérience (case study, data, insight...) pour éviter le contenu générique pénalisé par Google.",
      });
    }
  });

type FormValues = z.infer<typeof formSchema>;

export default function NewGenerationPage() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    defaultValues: {
      product: "Extermination Frelons",
      description: "mon client tue les frelons dans les maisons des particuliers et les copropriétés en passant de l'insecticide sous pression",
      niche: "frelons",
      audience: "propiétaires de maisons individuelles et syndics de copropriété",
      language: "fr",
      seed_keywords_text: "tuer frelons\nexterminer frelons\nse débarasser des frelons\ndétruire nid de frelons\nnid de frelons",
      num_cocoons: 2,
      mode: "full",
      validate_keywords: true,
      experience_elements: [
        {
          type: "case_study",
          title: "Intervention frelons",
          content: "j'ai été appelé par un particulier ayant une petite maison avec un nid de frelons à détruire. J'ai passé de l'insecticide sous pression à 6 bars et les frelons ont été tués.",
        },
      ],
      style_samples: [],
    },
  });

  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: "experience_elements",
  });

  const styleArray = useFieldArray({
    control: form.control,
    name: "style_samples",
  });

  const mode = form.watch("mode");
  const values = form.watch();

  // Les coûts affichés plus bas sont en USD parce que c'est la devise de
  // facturation d'Anthropic et de DataForSEO — les convertir en euros donnerait
  // une fausse précision. Ils sont MESURÉS, pas estimés : ~$1/cocon en Brief
  // (37 724 tokens de sortie sur un cocon de 6) et ~$3/cocon en Génération
  // complète (72 006 tokens, dont la mère en Opus), DataForSEO ~$0,38 inclus.
  // Les valeurs affichées auparavant (€5-10 et €15-25) étaient inventées et
  // surestimaient d'un facteur 5 à 8. Le coût réel de chaque run est désormais
  // remonté dans `result.usage` — voir RunUsage côté backend.

  async function goNext() {
    const fieldsPerStep: Record<number, (keyof FormValues)[]> = {
      1: ["product", "description", "niche", "audience"],
      2: ["seed_keywords_text"],
      3: ["num_cocoons", "mode", "experience_elements", "style_samples"],
    };
    const ok = await form.trigger(fieldsPerStep[step] ?? []);
    if (!ok) {
      const errs = form.formState.errors;
      const messages: string[] = [];
      for (const field of fieldsPerStep[step] ?? []) {
        const err = errs[field];
        if (err?.message) messages.push(String(err.message));
        else if (err?.root?.message) messages.push(String(err.root.message));
      }
      // Erreurs imbriquées (ex: experience_elements[0].content)
      if (errs.experience_elements && Array.isArray(errs.experience_elements)) {
        errs.experience_elements.forEach((item, idx) => {
          if (item?.title?.message)
            messages.push(`Élément ${idx + 1} — titre : ${item.title.message}`);
          if (item?.content?.message)
            messages.push(`Élément ${idx + 1} — contenu : ${item.content.message}`);
        });
      }
      if (errs.style_samples && Array.isArray(errs.style_samples)) {
        errs.style_samples.forEach((item, idx) => {
          if (item?.content?.message)
            messages.push(`Échantillon ${idx + 1} : ${item.content.message}`);
        });
      }
      toast.error(messages[0] || "Corrige les champs signalés avant de continuer.", {
        description: messages.slice(1).join(" · ") || undefined,
      });
      return;
    }
    setStep((s) => Math.min(4, s + 1));
  }

  async function onSubmit(data: FormValues) {
    setSubmitting(true);
    try {
      const payload: ApiClientForm = {
        product: data.product,
        description: data.description,
        language: data.language,
        seed_keywords: data.seed_keywords_text
          .split(/[\n,]+/)
          .map((k) => k.trim())
          .filter(Boolean)
          .slice(0, 20),
        audience: data.audience,
        niche: data.niche,
        num_cocoons: Number(data.num_cocoons),
        mode: data.mode,
        validate_keywords: data.validate_keywords,
        experience_elements: data.experience_elements,
        style_samples: data.style_samples.map((s) => ({
          title: s.title.trim() || null,
          content: s.content,
        })),
      };
      const { job_id } = await createGeneration(payload);
      toast.success("Génération lancée");
      router.push(`/jobs/${job_id}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Erreur inconnue";
      toast.error(msg);
      setSubmitting(false);
    }
  }

  const seedCount = values.seed_keywords_text
    .split(/[\n,]+/)
    .map((k) => k.trim())
    .filter(Boolean).length;

  return (
    <div className="min-h-screen">
      <header className="border-b">
        <div className="max-w-4xl mx-auto flex items-center justify-between px-6 py-4">
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
            ← Retour
          </Link>
          <span className="text-sm font-medium">Nouvelle génération</span>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10">
        {/* Progression */}
        <div className="mb-8">
          <ol className="flex items-center gap-2 text-sm">
            {STEPS.map((s, i) => (
              <li key={s.id} className="flex items-center gap-2">
                <span
                  className={`inline-flex h-7 w-7 items-center justify-center rounded-full border text-xs font-medium ${
                    step >= s.id
                      ? "bg-primary text-primary-foreground border-primary"
                      : "text-muted-foreground"
                  }`}
                >
                  {s.id}
                </span>
                <span className={step >= s.id ? "font-medium" : "text-muted-foreground"}>
                  {s.label}
                </span>
                {i < STEPS.length - 1 && (
                  <span className="mx-1 h-px w-8 bg-border" aria-hidden />
                )}
              </li>
            ))}
          </ol>
        </div>

        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
          {step === 1 && (
            <Card>
              <CardHeader>
                <CardTitle>Contexte client</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="product">Produit / service *</Label>
                  <Input
                    id="product"
                    placeholder="Ex: Wall of Traders"
                    {...form.register("product")}
                  />
                  {form.formState.errors.product && (
                    <p className="text-sm text-destructive">
                      {form.formState.errors.product.message}
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="description">Description détaillée *</Label>
                  <Textarea
                    id="description"
                    rows={4}
                    placeholder="Décrivez précisément ce que fait votre client. Plus c'est précis, meilleurs seront les cocons."
                    {...form.register("description")}
                  />
                  {form.formState.errors.description && (
                    <p className="text-sm text-destructive">
                      {form.formState.errors.description.message}
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="niche">Niche / secteur *</Label>
                  <Input
                    id="niche"
                    placeholder="Ex: Trading crypto et copy-trading pour particuliers"
                    {...form.register("niche")}
                  />
                  {form.formState.errors.niche && (
                    <p className="text-sm text-destructive">
                      {form.formState.errors.niche.message}
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="audience">Audience cible *</Label>
                  <Textarea
                    id="audience"
                    rows={3}
                    placeholder="Ex: Particuliers 25-45 ans intéressés par le trading crypto, revenu moyen à élevé..."
                    {...form.register("audience")}
                  />
                  {form.formState.errors.audience && (
                    <p className="text-sm text-destructive">
                      {form.formState.errors.audience.message}
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="language">Langue cible</Label>
                  <select
                    id="language"
                    className="flex h-9 w-full rounded-md border bg-transparent px-3 py-1 text-sm"
                    {...form.register("language")}
                  >
                    <option value="fr">Français</option>
                    <option value="en">English</option>
                    <option value="es">Español</option>
                    <option value="de">Deutsch</option>
                  </select>
                </div>
              </CardContent>
            </Card>
          )}

          {step === 2 && (
            <Card>
              <CardHeader>
                <CardTitle>Mots-clés seeds</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="seeds">
                    Mots-clés que tapent les clients dans Google *{" "}
                    <span className="text-muted-foreground font-normal">
                      ({seedCount}/20)
                    </span>
                  </Label>
                  <Textarea
                    id="seeds"
                    rows={6}
                    placeholder={`Un mot-clé par ligne, ou séparés par virgules.\n\nEx:\ncopy trading crypto\nsignaux trading\ntrader débutant\ntrading automatique`}
                    {...form.register("seed_keywords_text")}
                  />
                  {form.formState.errors.seed_keywords_text && (
                    <p className="text-sm text-destructive">
                      {form.formState.errors.seed_keywords_text.message}
                    </p>
                  )}
                  <p className="text-xs text-muted-foreground">
                    Claude étendra ces seeds en 30 mots-clés candidats puis
                    DataForSEO fournira volume / CPC / concurrence pour chaque.
                  </p>
                </div>
              </CardContent>
            </Card>
          )}

          {step === 3 && (
            <Card>
              <CardHeader>
                <CardTitle>Mode & options</CardTitle>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="space-y-3">
                  <Label>Nombre de cocons à générer</Label>
                  <div className="flex gap-2">
                    {[1, 2, 3, 4].map((n) => (
                      <label
                        key={n}
                        className={`flex-1 border rounded-md p-3 text-center cursor-pointer text-sm ${
                          Number(values.num_cocoons) === n
                            ? "border-primary bg-primary/5"
                            : ""
                        }`}
                      >
                        <input
                          type="radio"
                          value={n}
                          className="sr-only"
                          {...form.register("num_cocoons", { valueAsNumber: true })}
                        />
                        {n} cocon{n > 1 ? "s" : ""}
                      </label>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Chaque cocon = 1 article mère + 5 filles. 2 cocons = 12 articles.
                  </p>
                </div>

                <div className="space-y-3">
                  <Label>Mode de génération</Label>
                  <RadioGroup
                    value={mode}
                    onValueChange={(v) => form.setValue("mode", v as "brief" | "full")}
                    className="space-y-3"
                  >
                    <label className="flex items-start gap-3 border rounded-md p-4 cursor-pointer">
                      <RadioGroupItem value="brief" id="mode-brief" className="mt-1" />
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">Brief éditorial</span>
                          <Badge variant="secondary">Recommandé</Badge>
                        </div>
                        <p className="text-sm text-muted-foreground mt-1">
                          Structure + entités + questions + plan maillage. Vos
                          rédacteurs produisent le contenu final. Coût mesuré :
                          ~$1 par cocon.
                        </p>
                      </div>
                    </label>
                    <label className="flex items-start gap-3 border rounded-md p-4 cursor-pointer">
                      <RadioGroupItem value="full" id="mode-full" className="mt-1" />
                      <div className="flex-1">
                        <span className="font-medium">Génération complète</span>
                        <p className="text-sm text-muted-foreground mt-1">
                          Articles Markdown prêts + FAQ + schema JSON-LD + score
                          E-E-A-T. Nécessite l&apos;upload d&apos;au moins un
                          élément d&apos;expérience client (ci-dessous). Coût
                          mesuré : ~$3 par cocon.
                        </p>
                      </div>
                    </label>
                  </RadioGroup>
                </div>

                <label className="flex items-start gap-3 border rounded-md p-4 cursor-pointer">
                  <Checkbox
                    checked={values.validate_keywords}
                    onCheckedChange={(checked) =>
                      form.setValue("validate_keywords", checked === true)
                    }
                    className="mt-1"
                  />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">
                        Valider les mots-clés avant de générer
                      </span>
                      <Badge variant="secondary">Recommandé</Badge>
                    </div>
                    <p className="text-sm text-muted-foreground mt-1">
                      Le run s&apos;arrête après la recherche de mots-clés et vous
                      montre la sélection de Claude, avec ses justifications. Vous
                      décochez, vous en ajoutez d&apos;autres, puis vous lancez la
                      génération. Décochez cette case pour un run d&apos;un seul
                      tenant.
                    </p>
                  </div>
                </label>

                {mode === "full" && (
                  <div className="space-y-3">
                    <Label>Éléments d&apos;expérience client (obligatoire pour Full)</Label>
                    <Alert>
                      <AlertDescription className="text-xs">
                        {/* {" "} explicite après chaque </strong> : le compilateur JSX
                            supprime l'espace qui suit une balise fermante en début de
                            nœud texte, ce qui collait « mot pour mot » à « dans ». */}
                        Chaque élément est repris <strong>mot pour mot</strong>{" "}
                        dans un article, en bloc cité et attribué — et un élément donné
                        n&apos;est placé que dans <strong>un seul</strong>{" "}
                        article. Comptez donc idéalement un élément par article, soit{" "}
                        {Number(values.num_cocoons || 1) * TYPICAL_ARTICLES_PER_COCON} pour{" "}
                        {values.num_cocoons || 1} cocon
                        {Number(values.num_cocoons || 1) > 1 ? "s" : ""}.
                      </AlertDescription>
                    </Alert>
                    {(() => {
                      const expected =
                        Number(values.num_cocoons || 1) * TYPICAL_ARTICLES_PER_COCON;
                      const provided = values.experience_elements?.length ?? 0;
                      const capped = Math.max(0, expected - provided);
                      if (capped === 0) return null;
                      return (
                        <p className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                          {provided} élément{provided > 1 ? "s" : ""} pour ~{expected}{" "}
                          articles : <strong>{capped} article{capped > 1 ? "s" : ""}</strong>{" "}
                          n&apos;auront aucun passage first-hand et verront leur score
                          E-E-A-T « expérience » plafonné à 40. La génération reste
                          possible, mais ces articles seront rédigés uniquement à partir
                          de sources publiques.
                        </p>
                      );
                    })()}
                    <div className="space-y-3">
                      {fields.map((field, index) => (
                        <div key={field.id} className="border rounded-md p-3 space-y-2">
                          <div className="flex items-center justify-between gap-2">
                            <select
                              className="h-9 rounded-md border bg-transparent px-2 text-sm"
                              {...form.register(`experience_elements.${index}.type` as const)}
                            >
                              <option value="case_study">Case study</option>
                              <option value="data">Data / chiffres propres</option>
                              <option value="insight">Insight terrain</option>
                              <option value="screenshot">Screenshot / capture</option>
                              <option value="quote">Citation / verbatim</option>
                            </select>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={() => remove(index)}
                            >
                              Supprimer
                            </Button>
                          </div>
                          <Input
                            placeholder="Titre court"
                            {...form.register(`experience_elements.${index}.title` as const)}
                          />
                          <Textarea
                            rows={3}
                            placeholder="Contenu détaillé (min 20 caractères) — ce que l'IA intégrera dans les articles."
                            {...form.register(`experience_elements.${index}.content` as const)}
                          />
                        </div>
                      ))}
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() =>
                          append({ type: "case_study", title: "", content: "" })
                        }
                      >
                        + Ajouter un élément d&apos;expérience
                      </Button>
                    </div>
                    {form.formState.errors.experience_elements?.message && (
                      <p className="text-sm text-destructive">
                        {form.formState.errors.experience_elements.message}
                      </p>
                    )}
                  </div>
                )}

                <div className="space-y-3 pt-2 border-t">
                  <div className="flex items-center gap-2">
                    <Label>Voix de marque — articles de référence</Label>
                    <Badge variant="secondary">Optionnel</Badge>
                  </div>
                  <Alert>
                    <AlertDescription className="text-xs">
                      Collez 2 à 5 articles déjà publiés par le client (ou son
                      rédacteur). L&apos;IA écrira dans cette voix plutôt que dans
                      un style générique. C&apos;est le réglage qui pèse le plus
                      sur le rendu final : conditionner sur des textes réels fait
                      chuter d&apos;un ordre de grandeur les marqueurs d&apos;écriture
                      machine, là où un « humanizer » appliqué après coup n&apos;a
                      aucun effet mesurable.
                    </AlertDescription>
                  </Alert>
                  <div className="space-y-3">
                    {styleArray.fields.map((field, index) => (
                      <div key={field.id} className="border rounded-md p-3 space-y-2">
                        <div className="flex items-center justify-between gap-2">
                          <Input
                            placeholder="Titre de l'article (optionnel)"
                            {...form.register(`style_samples.${index}.title` as const)}
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => styleArray.remove(index)}
                          >
                            Supprimer
                          </Button>
                        </div>
                        <Textarea
                          rows={5}
                          placeholder="Collez le texte de l'article (min 200 caractères). Plus l'extrait est long et représentatif, mieux la voix est captée."
                          {...form.register(`style_samples.${index}.content` as const)}
                        />
                        <p className="text-xs text-muted-foreground">
                          {(values.style_samples?.[index]?.content ?? "").length} caractères
                        </p>
                      </div>
                    ))}
                    {styleArray.fields.length < 5 && (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => styleArray.append({ title: "", content: "" })}
                      >
                        + Ajouter un article de référence
                      </Button>
                    )}
                  </div>
                  {form.formState.errors.style_samples?.message && (
                    <p className="text-sm text-destructive">
                      {form.formState.errors.style_samples.message}
                    </p>
                  )}
                </div>
              </CardContent>
            </Card>
          )}

          {step === 4 && (
            <Card>
              <CardHeader>
                <CardTitle>Récapitulatif</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <Row label="Produit" value={values.product} />
                <Row label="Niche" value={values.niche} />
                <Row label="Langue" value={values.language} />
                <Row label="Cocons" value={String(values.num_cocoons)} />
                <Row
                  label="Mode"
                  value={
                    values.mode === "brief"
                      ? "Brief éditorial"
                      : "Génération complète"
                  }
                />
                <Row label="Seeds" value={`${seedCount} mots-clés`} />
                {values.mode === "full" && (
                  <Row
                    label="Éléments d'expérience"
                    value={(() => {
                      const expected =
                        Number(values.num_cocoons || 1) * TYPICAL_ARTICLES_PER_COCON;
                      const provided = values.experience_elements.length;
                      const capped = Math.max(0, expected - provided);
                      return capped > 0
                        ? `${provided} — ${capped} article(s) sans passage first-hand`
                        : `${provided}`;
                    })()}
                  />
                )}
                <Row
                  label="Voix de marque"
                  value={
                    values.style_samples?.length
                      ? `${values.style_samples.length} article(s) de référence`
                      : "Aucun échantillon — style générique"
                  }
                />
                <p className="text-xs text-muted-foreground pt-3 border-t">
                  Le job va prendre environ 3 à 10 minutes selon le mode et le
                  nombre de cocons. Tu pourras suivre la progression en temps
                  réel sur la page suivante.
                </p>
              </CardContent>
            </Card>
          )}

          {/* Navigation */}
          <div className="flex justify-between">
            <Button
              type="button"
              variant="outline"
              onClick={() => setStep((s) => Math.max(1, s - 1))}
              disabled={step === 1}
            >
              Précédent
            </Button>
            {step < 4 ? (
              <Button type="button" onClick={goNext}>
                Suivant
              </Button>
            ) : (
              <Button type="submit" disabled={submitting}>
                {submitting ? "Lancement..." : "Lancer la génération"}
              </Button>
            )}
          </div>
        </form>
      </main>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b pb-2 last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-right">{value || "—"}</span>
    </div>
  );
}
