-- Style-Reference RAG schema (pgvector) for Supabase.
--
-- DEFERRED PRODUCTION ENABLEMENT — apply this once, then set the embedding env (below).
-- Until applied, the backend runs the in-memory dev path under DEV_NO_AUTH=1; in production
-- (no DEV_NO_AUTH) the /api/style/* routes hit these tables via PostgREST + the RPC.
--
-- vector(1024) matches EMBEDDING_DIM=1024 (Voyage voyage-3). If you choose a different model,
-- change the dimension here AND set EMBEDDING_DIM to match (changing later needs a re-embed).
--
-- Apply via the Supabase SQL editor or the Supabase MCP `apply_migration` tool.
-- Required backend env for the live path:
--   EMBEDDING_API_KEY=<voyage or openai key>   EMBEDDING_PROVIDER=voyage   EMBEDDING_MODEL=voyage-3
--   EMBEDDING_DIM=1024   (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY already set for documents_store)

create extension if not exists vector;

create table if not exists public.style_references (
  id            uuid primary key,
  owner_id      text not null,
  domain        text not null,
  name          text not null default '',
  lang_src      text not null default '',
  lang_tgt      text not null default '',
  has_pairs     boolean not null default false,
  segment_count integer not null default 0,
  created_at    timestamptz not null default now()
);
create index if not exists style_references_owner_domain
  on public.style_references (owner_id, domain);

create table if not exists public.style_segments (
  id           uuid primary key,
  reference_id uuid not null references public.style_references (id) on delete cascade,
  owner_id     text not null,
  domain       text not null,
  role         text not null,            -- 'style' (target sentence) | 'tm_src' (source w/ pair_target)
  lang         text not null default '',
  text         text not null,
  pair_target  text,                     -- approved translation for role='tm_src'
  embedding    vector(1024) not null,
  created_at   timestamptz not null default now()
);
create index if not exists style_segments_owner_domain_role
  on public.style_segments (owner_id, domain, role);
-- Approximate nearest-neighbour index for cosine distance (build after some data exists).
create index if not exists style_segments_embedding_ivfflat
  on public.style_segments using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- Similarity search used by style_memory.match() (called with the service-role key).
-- score = cosine similarity = 1 - cosine_distance; nearest first.
create or replace function public.match_style_segments(
  p_owner text, p_domain text, p_role text, p_query vector, p_k int
) returns table (id uuid, reference_id uuid, text text, pair_target text, score float)
language sql stable as $$
  select s.id, s.reference_id, s.text, s.pair_target,
         1 - (s.embedding <=> p_query) as score
  from public.style_segments s
  where s.owner_id = p_owner and s.domain = p_domain and s.role = p_role
  order by s.embedding <=> p_query
  limit p_k
$$;

-- Defense-in-depth: the backend uses the service-role key (bypasses RLS), but enable RLS so
-- any direct client access is still owner-scoped. owner_id is the JWT `sub`.
alter table public.style_references enable row level security;
alter table public.style_segments  enable row level security;

create policy style_references_owner on public.style_references
  for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);
create policy style_segments_owner on public.style_segments
  for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);
