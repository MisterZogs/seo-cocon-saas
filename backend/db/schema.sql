-- Schéma Supabase pour la persistance des runs du pipeline.
-- À coller dans le SQL Editor du projet Supabase (une seule fois).
--
-- Un "run" = une exécution du pipeline déclenchée par le formulaire.
-- Le job RQ associé (job_id) expire au bout de 24h ; le run, lui, reste.

create table if not exists public.runs (
    id            uuid primary key default gen_random_uuid(),
    job_id        text unique,
    agency_id     text,
    project_name  text,

    -- Entrée
    form          jsonb not null,
    mode          text  not null check (mode in ('brief', 'full')),
    language      text  not null default 'fr',

    -- État
    status        text  not null default 'queued'
                        check (status in ('queued', 'running', 'completed', 'failed')),
    progress      jsonb,
    error         text,
    error_traceback text,

    -- Sortie (le PipelineResult complet, source de vérité)
    result        jsonb,

    -- Compteurs dénormalisés — évitent de parser `result` pour lister l'historique
    cocoons_count  int not null default 0,
    articles_count int not null default 0,

    created_at    timestamptz not null default now(),
    started_at    timestamptz,
    ended_at      timestamptz,
    updated_at    timestamptz not null default now()
);

create index if not exists runs_agency_created_idx
    on public.runs (agency_id, created_at desc);
create index if not exists runs_status_idx
    on public.runs (status);
create index if not exists runs_job_id_idx
    on public.runs (job_id);

-- Checkpoints : sortie de chaque étape du pipeline, pour reprendre un run
-- échoué sans refaire (ni repayer) les étapes déjà passées.
create table if not exists public.run_checkpoints (
    run_id      uuid not null references public.runs(id) on delete cascade,
    step        text not null,
    payload     jsonb not null,
    created_at  timestamptz not null default now(),
    primary key (run_id, step)
);

-- updated_at auto
create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists runs_touch_updated_at on public.runs;
create trigger runs_touch_updated_at
    before update on public.runs
    for each row execute function public.touch_updated_at();

-- RLS : activée dès maintenant pour ne pas exposer la table en cas de fuite
-- de la clé anon. Le backend utilise la SERVICE_ROLE_KEY, qui bypasse RLS.
-- Les policies par agence viendront avec l'auth Supabase (V1).
alter table public.runs enable row level security;
alter table public.run_checkpoints enable row level security;
