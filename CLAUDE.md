# Cocon Sémantique SaaS — Contexte Projet

## Concept
Outil SaaS B2B pour **agences SEO françaises premium** : automatise la création de cocons sémantiques rigoureux (méthode Bourrelly) avec maillage interne précis + optionnellement génération d'articles complets.

**Positionnement :** "Le premier outil SaaS qui automatise la vraie méthode cocon sémantique française. Pas du 'topic cluster' hub/spoke flou — la rigueur Bourrelly appliquée à l'échelle."

**Deux modes disponibles :**
- **Mode Brief** — briefs éditoriaux détaillés + structure + maillage précis. L'agence rédige avec ses rédacteurs.
- **Mode Génération complète** — articles rédigés en premier jet. Nécessite upload d'éléments d'expérience (case study, data, screenshots) par le client final pour éviter les pénalités Google.

## Fondateur
Gaetan — co-fondateur de Wall of Traders (walloftraders.com/blog), expert SEO cocons sémantiques et maillage interne. Répondre au niveau technique quand il pose des questions techniques.

---

## Décisions prises (session Opus 4.7)

### Stratégie (pivot après analyse concurrentielle Scalenut)
| Décision | Choix |
|---|---|
| **Persona cible** | Agences SEO françaises premium (bulk, white-label, export WP) |
| **Positioning** | "Automation cocon sémantique Bourrelly-rigoureux" — Mode hybride Brief OU Génération |
| **Marché V1** | **France uniquement** — angle Bourrelly authentique, réseau exploitable |
| **Marché V2+** | Vision long-terme : concurrencer Scalenut sur l'international EN une fois le modèle prouvé en FR |
| **Angle défendable** | Rigueur maillage Bourrelly + Mode Brief distinct + white hat + FR-native |
| **Pricing cible** | €199-499/mois (adapté agences FR, couvre coûts API) |

### Vision long-terme (V2+, 12-18 mois post-MVP)
Concurrencer Scalenut de front sur le marché international n'est PAS exclu. Le marché SEO tools mondial pèse plusieurs milliards — plusieurs acteurs peuvent coexister. Slack a été lancé après HipChat, Notion après Evernote, Airbnb après les hôtels. La question n'est pas "y a-t-il un concurrent" mais "peut-on mieux servir un segment précis".

**Séquence probable :**
1. **MVP → V1 (0-12 mois)** : dominer marché FR agences avec cocon Bourrelly premium
2. **V2 (12-18 mois)** : décliner en EN avec les apprentissages FR, entrer sur l'international
3. **V3+ (18+ mois)** : concurrencer Scalenut/Frase/Outranking avec différenciateurs affinés (Mode Brief, rigueur maillage, E-E-A-T score, white hat)

**Ce qui doit rester vrai pour justifier V2+ :** différenciateurs clairs vs Scalenut (voir section "Différenciateurs assumés"), pas juste "on est moins cher" ou "on est en plus".

### Stratégie multi-vertical (V2+) — "One brand, industry solutions"

**Principe :** UNE seule marque, UN seul site, UN seul produit — mais plusieurs **solutions verticales** dans le même site + produit qui s'adapte à la verticale choisie par le client.

**Modèle inspiré de Stripe / HubSpot / Segment :**
```
domaine.com                            ← marque unique forte
├── /                                  ← homepage agences (cible principale)
├── /solutions/agences-seo             ← vertical principal MVP
├── /solutions/e-commerce              ← vertical 2 (V2)
├── /solutions/finance-fintech         ← vertical 3
├── /solutions/saas-tech               ← vertical 4
├── /solutions/crypto-trading          ← vertical 5 (angle Wall of Traders)
├── /solutions/immobilier              ← vertical 6
└── /solutions/services-pro            ← vertical 7 (avocats, comptables, etc.)
```

**Chaque page verticale a :**
- Titre spécifique ("Cocons sémantiques pour agences SEO", "Cocons sémantiques pour e-commerce")
- Case studies clients de la verticale
- Features mises en avant pertinentes
- Templates de cocons pré-configurés (téléchargeables en démo)
- Pricing/packaging adapté si besoin

**Dans le produit :**
- Le formulaire demande "Quel secteur ?" en étape 1
- Selon la réponse, l'IA charge :
  - Templates spécifiques (structure d'articles adaptée à la verticale)
  - Prompts optimisés (vocabulaire, exemples, ton propre à la niche)
  - Base de KW pré-populée pour la verticale
  - Réglages spécifiques (finance = compliance + disclaimers YMYL, tech = jargon technique OK, crypto = données live + réglementation)
  - Structures de cocons de référence pour la verticale

**Verticales prioritaires identifiées :**
1. **Agences SEO** (MVP) — cible principale, revenu récurrent élevé
2. **E-commerce** — gros marché, besoins SEO clairs
3. **Crypto / Trading** — angle Wall of Traders, ticket élevé, réseau exploitable
4. **Finance / Fintech** — YMYL premium, haute valeur
5. **SaaS / Tech** — early adopters, comprennent le SEO
6. **Services professionnels** (avocats, comptables, consultants) — local SEO fort
7. **Immobilier** — SEO local + long tail

**⚠️ Ce qu'on NE fait PAS : multi-brand (5 sites séparés)**

Rationale documenté :
- Coût marketing ×5 (5 SEO strategies, 5 blogs à maintenir, 5 landing pages, 5 personas)
- Dilution marque : réputation se construit sur UNE identité
- Piège du "same product, different paint" : Google/prospects détectent → footprints SEO + perte de confiance
- Complexité opérationnelle (5 supports, 5 CRM, 5 SSL, facturation confuse)
- Apprentissage dilué : focus = apprentissage rapide
- Only makes sense pour : Toyota/Lexus (positionning radicalement différent), acquisition (rachat concurrent), cibles conflictuelles B2B/B2C, entreprises 50+ employés avec équipes dédiées

Note : à V4+ (18+ mois), SI une verticale explose (ex: crypto/trading grâce à Wall of Traders), un site dédié premium avec marque propre peut être envisagé — mais basé sur data, pas intuition initiale.

**Attention piège vocabulaire :** "IA" n'est PAS une verticale industrie, c'est une horizontale techno. Une entreprise IA peut être finance/e-commerce/B2B/etc. Préférer les vraies industries (finance, santé, immobilier...).

### Stack technique
| Composant | Choix |
|---|---|
| **Frontend** | Next.js + shadcn/ui (TypeScript) — déployé sur le VPS (Docker) |
| **Backend API** | FastAPI (Python async) — déployé sur le VPS (Docker) |
| **Workers** | RQ (Redis Queue) pour jobs longs de génération |
| **Base de données** | Postgres 16 auto-hébergé (Docker Compose sur le VPS) |
| **Cache / Queue** | Redis 7 auto-hébergé (Docker Compose sur le VPS) |
| **APIs tierces MVP** | DataForSEO uniquement (KW + SERP + backlinks) |
| **APIs tierces V1** | Ahrefs backlinks, Hunter.io outreach |

### Modèles Claude
| Usage | Modèle | Raison |
|---|---|---|
| **Article mère (pillar)** | `claude-opus-4-7` | Qualité maximale sur l'article central |
| **Articles filles (cluster)** | `claude-sonnet-4-6` | Bon rapport qualité/coût, 5 filles par cocon |
| **Tâches simples** | `claude-haiku-4-5-20251001` | Slug, meta description, classification intent |
| **Prompt caching** | **OBLIGATOIRE** | Sans caching = 4x plus cher (contexte cocon + SERP réutilisés) |

Doc caching : https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching

---

## Pipeline

```
1. Formulaire agence (interface FR-native, tone pro)
   → produit client, description, langue (FR par défaut au MVP), mots clés seeds, audience, niche
   → nb de cocons souhaités (1-3)
   → MODE : Brief OU Génération complète
   → Si Génération : upload obligatoire d'éléments d'expérience
     (case study, data propres, screenshots, insights)

2. Keyword Research
   → Claude (Haiku) étend les seeds → 30 KW candidats + intent + cluster
   → DataForSEO Keywords API → volume, CPC, concurrence
   → DataForSEO SERP API → analyse SERP features par KW (guides ? listicles ? snippets ? PAA ?)
   → Claude (Sonnet) sélectionne top KW et les groupe en N cocons
   → Retour au client : validation cocons avant génération

3. Design des cocons (Claude Sonnet)
   → Chaque cocon : 1 article mère + 5 articles filles
   → Génère slug, titre H1, meta description, meta title pour chaque article
   → Définit la stratégie de maillage (voir étape 6)
   → Attribue KW principal + KW secondaires à chaque article

4. Analyse SERP par article (Surfer-like)
   → DataForSEO SERP → top 10 URLs pour chaque KW cible
   → httpx + BeautifulSoup → scrape contenu, H2/H3, word count
   → Claude (Sonnet) analyse : entités clés, questions PAA récurrentes,
     angles non couverts, longueur moyenne, structure gagnante
   → Produit un brief calibré par article

5a. MODE BRIEF (sortie immédiate)
   → JSON structuré : titre, meta, plan H2/H3 détaillé, entités à couvrir,
     questions à répondre (FAQ), suggestions liens externes,
     brief backlinks, ratio ancres, plan maillage complet

5b. MODE GÉNÉRATION COMPLÈTE (si activé et éléments uploadés)
   → Claude (Opus pour mère, Sonnet pour filles) génère article complet :
      - Structure H2/H3 calibrée SERP
      - Rédigé dans la voix des `style_samples` fournis (si présents)
      - Section FAQ (questions PAA)
      - 1-3 liens externes vers sources autoritaires
      - Schema JSON-LD (Article + FAQ)
      - Liens internes avec ancres précises (maillage)
   → Injection verbatim des éléments d'expérience (article_generator.py)
      Le LLM ne pose qu'un marqueur `[[EXPERIENCE:id]]` avec son amorce au-dessus
      et sa conclusion en dessous ; le texte exact du client est substitué EN CODE,
      en bloc cité et attribué. Même raison que pour le maillage : ce qui doit être
      exact n'est pas confié au prompt. Si on laissait le modèle recopier, il
      paraphraserait — or ces blocs sont les seuls passages non générés de l'article,
      c'est tout leur intérêt (E-E-A-T réel + seuls segments qu'un détecteur peut
      créditer comme humains).
   → Score E-E-A-T par article (0-100) basé sur :
      - Expérience démontrée — UNIQUEMENT les blocs verbatim réellement placés.
        Plafonné à 40 en code sinon (le modèle se notait 70-80 sur des articles
        écrits intégralement à partir de sources publiques).
      - Sources externes citées
      - Données originales présentes
      - Unicité vs top 10 SERP

6. Maillage interne (méthode du cocon sémantique, appliquée strictement)
   → Fille → Mère (toujours, ancre sur KW principal de la mère)
   → Mère → toutes ses Filles (toujours)
   → Filles ↔ Filles : TOUTES liées entre elles, systématiquement et
     réciproquement. Le maillage transversal fait partie de la méthode,
     ce n'est pas une option réservée aux cas "justifiés".
   → Inter-cocons : AUCUN par défaut (étanchéité des silos)
   → Ancres choisies pour l'utilisateur d'abord, optimisées KW ensuite
   → Map complète : slug → [slugs liés + ancres + justification]

   ARITHMÉTIQUE ATTENDUE d'un cocon de 1 mère + 5 filles :
   mère → 5 filles, chaque fille → mère + 4 sœurs = 30 liens, et chaque
   page reçoit exactement 5 liens entrants. Les filles égalent la mère en
   liens entrants — c'est normal, la mère tire sa force de sa position
   dans l'arbre, pas d'un surnombre de liens internes.

   ÉTANCHÉITÉ (vérifié en août 2026 contre les sources). Le principe central
   du siloing est qu'une page du cocon A ne lie pas vers une page du cocon B :
   ça crée une rupture sémantique. Bourrelly lui-même reste prudent plutôt
   qu'interdicteur ("on doit prendre garde aux liaisons entre les silos ;
   elles doivent avoir du sens"), mais aucune source ne prescrit de lier les
   cocons entre eux. Les cocons sont déjà reliés par le HAUT de l'arbre
   (accueil → page cible de chaque cocon) : les relier latéralement est
   redondant. D'où le défaut strict.

   Réglage `inter_cocon_policy` sur le formulaire (models.py) :
   - `strict` (DÉFAUT) — aucun lien inter-cocon, conforme à la méthode
   - `mothers_only` — mère ↔ mère uniquement, jamais les filles
   - `libre` — aucune contrainte, pour tester l'approche "siloing assoupli"

   MISE EN CONFORMITÉ EN CODE, pas dans le prompt (pipeline/maillage.py).
   Les règles sont déterministes, le LLM ne les respecte pas de lui-même :
   sur un run mesuré il ne produisait que 27 des 40 liens transversaux
   attendus, sans réciprocité, avec une page sans aucun lien entrant de ses
   sœurs, et 14 liens inter-cocons tous entre filles. La normalisation
   complète ce qui manque, retire ce qui viole la politique, et réécrit les
   marqueurs [[INTERNAL_LINK:...]] du markdown pour que le corps de l'article
   reste cohérent avec la map.

7. Rapport backlinks stratégique par cocon
   → DataForSEO backlink API → analyse top 10 concurrents
   → Types de sites qui linkent, DR moyen, ancres utilisées
   → Opportunités : sites ayant linké vers concurrents, annuaires, médias niche
   → Ratio d'ancres recommandé par article (exact / partial / brand / naked)
   → Templates outreach prêts (guest post + niche edit)

8. Output multi-format
   → JSON structuré complet (source de vérité)
   → Export Markdown (agences avec workflow custom)
   → Export WordPress REST API (publication directe dans site client)
   → Export HTML (agences avec site custom)
```

---

## Stratégie Backlinks

**Approche : rapport backlink stratégique par cocon — white hat, pas de marketplace de liens.**

Raisons : marketplace = contre CGU Google, risque légal/réputationnel, meilleures marketplaces sans API publique. Construire son propre réseau = business à part entière hors scope.

**Contenu du rapport par cocon :**
- Analyse backlinks des top 10 concurrents (DataForSEO API, upgrade Ahrefs en V1)
- Opportunités : sites ayant déjà linké vers concurrents, annuaires sectoriels, médias niche
- Ratio d'ancres recommandé par article (exact / partial / brand / naked)
- Templates d'outreach prêts (guest post + niche edit)

**Roadmap backlinks :**
- MVP : rapport stratégique + ancres recommandées + templates outreach
- V1 : Ahrefs pour données backlinks premium
- V2 : module outreach automatisé (Hunter.io + CRM léger)
- Jamais : marketplace de liens achetés

---

## Compréhension SEO 2026 (référence)

Points intégrés dans le design du pipeline :

- **E-E-A-T** : Mode 2 exige upload d'expérience, score E-E-A-T par article.
  Les éléments d'expérience sont repris **verbatim** (bloc cité + attribué), jamais
  paraphrasés — voir « Détection IA » ci-dessous. Le score `experience` est plafonné
  à 40 en code si aucun bloc verbatim n'a été placé : ce score est l'argument
  anti-deindex vendu aux agences, le laisser mentir viderait la feature de son sens.
- **Voix de marque (few-shot)** : l'agence peut fournir jusqu'à 5 articles existants
  du client. Ils sont injectés dans le contexte caché partagé (`style_samples`) et
  conditionnent la génération. C'est le levier le plus fort sur le rendu final.
- **AI Overviews (SGE)** : prompts optimisés pour réponse directe premiers 100 mots + structure liste/tableau
- **Helpful Content** : information gain calculé vs top 10 SERP
- **Schema markup** : Article + FAQ auto-générés
- **Liens externes** : 1-3 par article vers sources autoritaires
- **Intent mapping** : SERP analysis calibre l'intent par KW
- **Topical Authority** : structure cocon = meilleur modèle pour ça
- **Maillage strict** : étanchéité des silos par défaut, maillage transversal complet
  entre sœurs, mise en conformité déterministe en code (voir étape 6 du pipeline)

Cas de deindex documentés : HouseFresh (-91%), RetroDodo, Giant Freakin Robot, majorité affiliate AI sites en 2024-2025.

---

## Détection IA — position tranchée (août 2026)

**L'humanizer ne sert à rien. Position antérieure corrigée.** La doc affirmait qu'« un
humanizer passe les détecteurs IA mais ne modifie aucun signal Google ». La seconde
moitié tient ; **la première est démentie**. Donc l'option ne vaut ni comme bouclier
Google ni comme bouclier détecteur, et la proposer commercialement reviendrait à
facturer un service sans effet mesurable. **Ne pas la réintroduire.**

Mesuré sur pangram.com : sortie brute du pipeline (article mère, 1428 mots) → 100 % IA,
confiance High. Le même texte repassé au skill `humanizer` → 100 % IA également.
Cohérent avec Pangram 4 (juillet 2026), entraîné spécifiquement contre les humanizers :
97,67 % de détection du texte humanizé, classifieur dédié à 91,5-99,4 % selon l'outil,
FPR 0,0041 %. Confirmé par trois évaluations indépendantes (VU Bruxelles, UChicago BFI,
UMD). Les articles « tel humanizer bat Pangram » proviennent des vendeurs d'humanizers.

**Ce qui marche à la place — conditionner à la génération, pas retoucher après.**
Donnée Epoch AI 2026, même modèle et même détecteur, seul le prompt change :

| Condition | FNR Pangram | FNR Originality |
|---|---|---|
| Prompt basique | ≤ 1 % | ≤ 1 % |
| Imitation de style, ~5 passages d'auteur | **10,1 %** | 17,85 % |
| Idem, rédaction technique | **~25 %** | ~29 % |

Un ordre de grandeur au-dessus de n'importe quel post-traitement, sans dégrader le
texte. D'où `style_samples` dans le formulaire. Les attaques par RL (StealthRL,
AuthorMist) atteignent 99,8 % d'évasion mais uniquement contre des détecteurs
open-source — ni Pangram ni Originality testés — et pour un coût qualité rédhibitoire
(LLM-judge 2,51/5 contre 3,78/5). Inutilisable sur un livrable facturé.

**Le verbatim vise « Mixed », pas « humain ».** Pangram 4 rend des verdicts mixtes
(55-66 % de rappel sur l'identification des passages d'auteurs distincts, 92 % de
précision token-level). Les blocs d'expérience cités ne feront donc pas basculer
l'article en « humain », mais ressortiront comme segments humains identifiés — ce qui
change entièrement la conversation avec le client final.

**Cadrage commercial.** Google ne pénalise pas le contenu IA en tant que tel et le
score de détection n'est pas un facteur de ranking ; ce qui est sanctionné, c'est le
*scaled content abuse*. Le problème de détection est donc un problème de **perception
client**, pas de SEO — mais bien réel pour la cible : des agences rapportent des
clients qui passent les livrables à Originality.ai avant de payer, litiges de
facturation à la clé. **Ne jamais promettre « indétectable »** (infalsifiable, et
c'est un tapis roulant contre une boîte financée dont c'est le seul métier). Livrer à
la place un rapport de détection + l'argumentaire à opposer au client.

---

## Paysage concurrentiel

### Concurrents vérifiés

**Scalenut** ⚠️ — leader international, fait 80% du concept mais généraliste
- Features : keyword clusters (5-75/mois), article generation, internal linking (dès $89), SERP analysis, GEO audit (AI Overviews), tracking ChatGPT/AIO/Perplexity, backlinks marketplace
- Pricing : $59-199/mois (Starter/Plus/Pro) + VIP custom
- Cible : mixte (marketers + agences + enterprises), 1M+ users
- **Faille exploitable** : positionning générique international, ne comprend PAS la rigueur cocon Bourrelly, marketplace de liens (angle éthique problématique), pas de mode Brief distinct, EN-centric
- **Verdict** : concurrent sérieux sur marché EN, pas positionné FR/Bourrelly

**Arvow (ex-Journalist AI)** — pas un vrai concurrent
- Fait du bulk article/autoblog, pas de topic cluster structuré, pas de SERP analysis embarquée
- Backlink Exchange + Link Building Service (marketplace de liens)
- 20k+ marketeurs/agences mais positionning différent
- **Verdict** : pas de conflit direct avec notre positionning

**Autres concurrents identifiés :**
- **Outranking.io** — cluster planning + génération mais maillage manuel
- **NeuronWriter** — clusters + génération + optimisation, pas de KW research intégré
- **Koala.sh / Byword.ai** — bulk articles, zéro logique cocon/maillage
- **SEO.ai** — full articles + optimization
- **GrowthBar** — clusters + articles
- **AlliAI** — automatisation SEO complète

**Différenciateurs assumés (par ordre de force) :**
1. **Rigueur maillage Bourrelly codifiée** — règles précises (fille→mère, mère→filles,
   sœurs toutes liées entre elles, étanchéité inter-cocons), appliquées en code et non
   laissées au bon vouloir du LLM. Vérifiable : chaque page reçoit exactement 5 liens
   entrants dans un cocon de 6. Aucun concurrent EN ne fait ça.
2. **Positionnement FR-native** — interface FR, prompts optimisés FR, comprend le SEO à la française
3. **Mode Brief vs Génération distincts** — Scalenut ne le fait pas clairement
4. **Score E-E-A-T par article** anti-deindex — unique
5. **White hat pur** (pas de marketplace de liens) — différenciation éthique vs Scalenut/Arvow
6. **Multi-cocons reliés** — inter-cocon maillage explicite
7. **Positionnement agences** (white-label, export WordPress, bulk)
8. **Analyse SERP embarquée** sans abonnement externe (déjà couvert par Scalenut mais bien fait chez nous)

---

## Structure du code prévue

```
seo/
├── backend/                    (FastAPI + workers Python)
│   ├── main.py                 FastAPI routes
│   ├── models.py               Pydantic schemas
│   ├── pipeline/
│   │   ├── keyword_research.py
│   │   ├── serp_analyzer.py
│   │   ├── cocon_builder.py
│   │   ├── article_generator.py
│   │   └── backlink_analyzer.py
│   ├── workers/
│   │   └── rq_worker.py        Job runner
│   ├── clients/
│   │   ├── anthropic_client.py (avec prompt caching)
│   │   └── dataforseo_client.py
│   ├── db/
│   │   ├── postgres.py
│   │   └── schema.sql
│   ├── requirements.txt
│   └── .env.example
│
├── frontend/                   (Next.js)
│   ├── app/                    App router
│   ├── components/             shadcn/ui
│   ├── lib/                    API client, utils
│   └── package.json
│
└── CLAUDE.md                   (ce fichier)
```

---

## Roadmap

**MVP (à coder maintenant) — marché FR agences, 1 vertical** :
- Interface FR-native
- Formulaire agence (Next.js)
- Pipeline complet en Mode Brief
- 1 seule verticale = agences SEO (pas de sélecteur secteur au formulaire au MVP)
- Export JSON + Markdown
- Auth basique (à trancher : JWT FastAPI maison ou service tiers)
- Dashboard agence : historique générations, projets clients
- Pricing €199-499/mois

**V1 (post-MVP, 1-2 mois) — consolidation FR** :
- Mode Génération complète avec upload expérience
- Score E-E-A-T par article
- Export WordPress REST API
- Ahrefs pour backlinks premium
- White-label pour agences (leur logo, leur nom sur les livrables)
- Prépration architecture "verticale-aware" du produit (formulaire step "secteur", système de templates)

**V2 (3-6 mois) — expansion verticale FR (2-3 verticales)** :
- Ajout landing pages verticales : `/solutions/e-commerce`, `/solutions/crypto-trading`
- Templates + prompts + KW bases pré-populées par verticale
- Sélecteur "secteur" dans formulaire → charge config adaptée
- Case studies clients par verticale
- Ranking tracking intégré (positions Google FR)
- Content refresh (audit + mise à jour cocons existants)
- Audit technique SEO (Core Web Vitals, sitemap, canonical)
- Brand voice questionnaire

**V3 (6-12 mois) — expansion verticale FR complète + préparation intl** :
- Ajout des 4-5 verticales restantes (`/solutions/finance-fintech`, `/solutions/saas-tech`, `/solutions/services-pro`, `/solutions/immobilier`)
- Pricing éventuellement différencié par verticale (finance/crypto premium)
- Multi-langue (EN, ES, DE, IT)
- Adaptation prompts par langue/marché
- Module outreach automatisé (Hunter.io + CRM léger)
- Import site existant (audit maillage existant + intégration cocons)
- Domain rebranding possible (nom EN si besoin)

**V4+ (12-18 mois) — attaque marché international + double-down verticales fortes** :
- Concurrencer Scalenut/Frase/Outranking de front sur le marché EN
- Positionning "The rigorous cocoon method — from France, now for the world"
- Différenciateurs affinés grâce aux 12+ mois d'apprentissage FR
- Pricing $199-799/mois (adapté marché international)
- **Si une verticale explose** (ex: crypto/trading via Wall of Traders) : envisager site dédié premium avec marque propre — décision data-driven, pas intuition

---

## État d'avancement

- [x] Concept validé et repositionné (mode hybride)
- [x] Pipeline détaillé (8 étapes, cohérent)
- [x] Stack technique tranchée (Next.js + FastAPI + Postgres + RQ + Redis)
- [x] Persona client défini (agences SEO françaises premium)
- [x] Marché défini (FR d'abord, vision V4+ internationale)
- [x] Choix modèles Claude par étape
- [x] Prompt caching identifié comme obligatoire
- [x] Analyse concurrentielle faite (Scalenut vérifié, Arvow vérifié)
- [x] Pivot stratégique acté (FR premium suite à concurrence Scalenut sur EN)
- [x] Stratégie backlinks tranchée (white hat pur)
- [x] Vision long-terme documentée (V4+ concurrence Scalenut EN)
- [x] requirements.txt initial créé
- [x] .env.example initial créé
- [x] Setup projet Next.js + FastAPI + Postgres
- [x] Code métier pipeline (6 modules + orchestrateur + worker + API)
- [x] Déploiement prod (VPS gringo, https://cocon.178.104.70.16.sslip.io)
- [x] Persistance des runs + reprise sur checkpoint après échec
- [x] Position tranchée sur la détection IA (humanizer abandonné, voir section dédiée)
- [x] Voix de marque few-shot (`style_samples`) + injection verbatim des éléments
      d'expérience + plafond E-E-A-T honnête
- [ ] Test end-to-end avec DataForSEO réel (credentials à obtenir)
- [ ] Mesurer le gain réel du few-shot sur Pangram (nécessite des crédits ; le compte
      gratuit de Gaetan est épuisé)
- [ ] Rapport de détection dans le livrable + argumentaire client
- [ ] Auth multi-agences

---

## Prochaine étape

1. **Setup infrastructure** : ✅ fait (VPS gringo, Docker Compose, Postgres, Redis) — reste compte DataForSEO
2. **Coder backend dans l'ordre** :
   `models.py → clients/anthropic_client.py (avec caching) → clients/dataforseo_client.py → pipeline/keyword_research.py → pipeline/serp_analyzer.py → pipeline/cocon_builder.py → pipeline/article_generator.py → pipeline/backlink_analyzer.py → workers/rq_worker.py → main.py`
3. **Coder frontend Next.js** : formulaire multi-step FR → dashboard → preview cocon → export
