-- Run once in Supabase → SQL Editor (new project). Required for login + "내 문서" + upload ownership.
-- See docs/superpowers/specs/2026-06-11-stunningbox-backend-auth-userdocs-design.md

create table public.documents (
  id           text primary key,
  owner_id     uuid not null references auth.users(id) on delete cascade,
  title        text,
  source_lang  text,
  target_lang  text,
  status       text not null default 'processing',
  created_at   timestamptz not null default now()
);

alter table public.documents enable row level security;

create policy "owner can read own documents" on public.documents
  for select using (auth.uid() = owner_id);

create index documents_owner_created_idx on public.documents (owner_id, created_at desc);
