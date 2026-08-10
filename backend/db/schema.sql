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
