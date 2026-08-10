-- Schéma de persistance des runs du pipeline (Postgres 16).
--
-- Appliqué automatiquement au démarrage du backend (cf. db/postgres.py) :
-- tout est idempotent, le rejouer ne casse rien.
--
-- Un "run" = une exécution du pipeline déclenchée par le formulaire.
-- Le job RQ associé (job_id) expire au bout de 24h ; le run, lui, reste.

-- Agences : un compte = une agence SEO. Pas de table `users` séparée au MVP —
-- une agence a un seul jeu d'identifiants. Le multi-utilisateur par agence
-- viendra avec le white-label (chantier 11), il s'ajoutera par une table
-- `agency_members` sans casser celle-ci.
create table if not exists agencies (
    id            uuid primary key default gen_random_uuid(),
    -- Stocké en minuscules, normalisé en code (pas de citext : ça imposerait
    -- une extension à installer sur une base déjà déployée).
    email         text not null unique,
    password_hash text not null,
    name          text not null,
    created_at    timestamptz not null default now(),
    last_login_at timestamptz
);

create table if not exists runs (
    id            uuid primary key default gen_random_uuid(),
    job_id        text unique,
    -- Contient l'`agencies.id` (en texte) depuis la mise en place de l'auth.
    -- Volontairement SANS clé étrangère : la colonne existait avant, remplie
    -- avec du texte libre saisi au formulaire, et une FK ferait échouer
    -- l'application du schéma sur la base de prod à cause de ces lignes.
    agency_id     text,
    project_name  text,

    -- Entrée
    form          jsonb not null,
    mode          text  not null check (mode in ('brief', 'full')),
    language      text  not null default 'fr',

    -- État
    -- 'awaiting_validation' : le run est suspendu entre la recherche de mots-clés
    -- et la génération, le temps que l'agence valide la sélection. Ce n'est ni un
    -- échec ni une fin — le run reprendra sur le même id.
    status        text  not null default 'queued'
                        check (status in ('queued', 'running', 'awaiting_validation',
                                          'completed', 'failed')),
    progress      jsonb,
    error         text,
    error_traceback text,

    -- Sortie (le PipelineResult complet, source de vérité)
    result        jsonb,

    -- Compteurs dénormalisés — évitent de charger `result` pour lister l'historique
    cocoons_count  int not null default 0,
    articles_count int not null default 0,

    created_at    timestamptz not null default now(),
    started_at    timestamptz,
    ended_at      timestamptz,
    updated_at    timestamptz not null default now()
);

create index if not exists runs_agency_created_idx
    on runs (agency_id, created_at desc);
create index if not exists runs_status_idx
    on runs (status);

-- Checkpoints : sortie de chaque étape du pipeline, pour reprendre un run
-- échoué sans refaire (ni repayer) les étapes déjà passées.
create table if not exists run_checkpoints (
    run_id      uuid not null references runs(id) on delete cascade,
    step        text not null,
    payload     jsonb not null,
    created_at  timestamptz not null default now(),
    primary key (run_id, step)
);

-- Migration : `create table if not exists` ne touche pas une table existante,
-- donc la contrainte de statut d'une base déjà déployée doit être remplacée
-- explicitement. Rejouable sans effet de bord.
alter table runs drop constraint if exists runs_status_check;
alter table runs add constraint runs_status_check
    check (status in ('queued', 'running', 'awaiting_validation', 'completed', 'failed'));

-- ============================================================
-- Facturation — solde de cocons
-- ============================================================
--
-- Tout est compté en **unités**, où 1 cocon = 6 unités (cf. UNITS_PER_COCOON
-- dans backend/billing.py). Le solde doit pouvoir descendre au sixième de cocon
-- parce qu'une régénération d'article se débite à l'article ; stocker des
-- entiers d'unités plutôt qu'un `numeric` évite tout arrondi (1/6 n'a pas
-- d'écriture décimale exacte, et 6 × 0,1667 ≠ 1).

-- Un « lot » = un octroi de cocons avec sa propre date d'expiration. Le report
-- d'un mois sur l'autre tombe de la durée de vie des lots : un lot d'abonnement
-- vit deux périodes, donc au plus deux lots d'abonnement sont vivants en même
-- temps — soit l'allocation du mois plus, au maximum, une allocation reportée.
-- C'est exactement le plafond « 1× l'allocation » décidé côté produit, sans
-- aucun calcul de report à écrire.
create table if not exists cocoon_lots (
    id              uuid primary key default gen_random_uuid(),
    agency_id       uuid not null references agencies(id) on delete cascade,
    kind            text not null check (kind in ('trial', 'subscription', 'purchase', 'manual')),

    -- 'YYYY-MM' pour un lot d'abonnement, null sinon. Sert de clé d'unicité :
    -- c'est ce qui rend l'octroi mensuel rejouable sans double crédit.
    period_key      text,

    granted_units   int not null check (granted_units > 0),
    remaining_units int not null check (remaining_units >= 0),

    granted_at      timestamptz not null default now(),
    -- null = n'expire jamais (cocon acheté à l'unité : il est payé, il reste).
    expires_at      timestamptz,

    constraint cocoon_lots_remaining_le_granted check (remaining_units <= granted_units)
);

create unique index if not exists cocoon_lots_period_uniq
    on cocoon_lots (agency_id, period_key) where period_key is not null;
create index if not exists cocoon_lots_agency_idx
    on cocoon_lots (agency_id, expires_at);

-- Journal append-only. La source de vérité du solde reste `remaining_units` des
-- lots (une somme, pas un repli sur tout l'historique) ; le journal sert à
-- expliquer ce solde à l'agence et à retrouver quels lots un remboursement doit
-- recréditer.
create table if not exists cocoon_ledger (
    id          uuid primary key default gen_random_uuid(),
    agency_id   uuid not null references agencies(id) on delete cascade,
    lot_id      uuid references cocoon_lots(id) on delete set null,

    -- Pas de clé étrangère vers `runs` : un run_id peut être généré localement
    -- quand la persistance a échoué (cf. main.generate), et une FK ferait alors
    -- échouer le débit — c'est-à-dire offrir la génération.
    run_id      uuid,

    kind        text not null check (kind in ('grant', 'debit_generation',
                                              'debit_regeneration', 'refund')),
    -- > 0 crédit, < 0 débit. Jamais 0 : une ligne sans effet n'a rien à dire.
    delta_units int not null check (delta_units <> 0),

    -- Posé sur un débit annulé par un remboursement. Sert aussi de garde
    -- d'idempotence : un run est débitable s'il n'a aucun débit NON annulé.
    -- C'est ce qui permet à une reprise après remboursement de re-débiter,
    -- sans jamais débiter deux fois une génération en cours.
    reversed_at timestamptz,

    note        text,
    created_at  timestamptz not null default now()
);

create index if not exists cocoon_ledger_agency_idx
    on cocoon_ledger (agency_id, created_at desc);
create index if not exists cocoon_ledger_run_idx
    on cocoon_ledger (run_id) where run_id is not null;

-- Formule de l'agence. Colonne ajoutée après coup : `create table if not exists`
-- ne touche pas une table existante.
alter table agencies add column if not exists plan text not null default 'trial';
alter table agencies add column if not exists plan_started_at timestamptz not null default now();

-- updated_at auto
create or replace function touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists runs_touch_updated_at on runs;
create trigger runs_touch_updated_at
    before update on runs
    for each row execute function touch_updated_at();
