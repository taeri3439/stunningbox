# StunningBox — Backend Auth Protection + Per-User Documents Design

**Date:** 2026-06-11
**Scope:** Make the FastAPI backend verify the Supabase JWT and enforce **per-user document ownership** — every document is owned by the user who created it, and a user can only see/access their own. Builds on the merged Supabase frontend-gate auth. Touches backend (FastAPI), the frontend (token forwarding + "내 문서" dashboard), and a new Supabase Postgres table.

---

## 1. Context (verified)

- Frontend: Next 16, `/api/:path*` **rewrites** to FastAPI `BACKEND_URL` (forwards request headers incl. `Authorization` and `Cookie`). Supabase `@supabase/ssr` cookie sessions; browser client at `@/lib/supabase/client`. Frontend `api.ts` calls bare `fetch("/api/...")` with **no auth header today**.
- Backend: FastAPI (`backend/main.py`), file-based storage `backend/storage/<doc_id>/`, **no users today**. ~25 endpoints under `/api/...`. Already verifies a JWT for OnlyOffice (`backend/onlyoffice.py`) — a reuse pattern for HS256 verification.
- Supabase project exists (frontend auth live). Access tokens are **HS256** (confirmed from the login smoke test — token header `{"alg":"HS256"}`), signed by the project **JWT Secret**.
- Endpoints fall into **three tiers** (drives the auth design):
  - **JSON/mutating** (`fetch`-able, can carry a Bearer header): `POST /api/documents` (upload), `POST /api/detect-language`, `GET .../status|result|segments|glossary-report|editor-config`, `POST .../translate|retranslate-segment|render|replace|replace/preview|glossary/term`.
  - **Read-only navigation GETs** (iframe/img/anchor — no custom header): `GET .../download`, `.../preview`, `.../assets/{name}`, `.../source-html/{path}`, `.../view`.
  - **Document-Server (server-to-server, OnlyOffice JWT)**: `GET .../docx`, `POST .../onlyoffice-callback`.
  - **Public**: `GET /api/health`, `GET /api/glossary/template`.

## 2. Decisions (agreed)

- **Ownership store: Supabase Postgres `documents` table** (chosen for deploy-robustness — survives ephemeral disks / multiple instances, unlike a local file/SQLite map).
- **Existing docs: wipe `backend/storage/*`** (clean slate).
- **"내 문서" UI: `/app` becomes a dashboard** (upload card + my-documents list).
- **Auth mechanism:** verify Supabase JWT locally, **HS256 + `SUPABASE_JWT_SECRET`**. Token source = **`Authorization: Bearer`** for JSON/mutating endpoints (CSRF-immune), **`sb-<ref>-auth-token` cookie** for read-only navigation GETs.
- **Backend ↔ documents table:** backend uses the **service-role key** to insert/select/update; **frontend reads "내 문서" directly via supabase-js + RLS** (no backend list endpoint).

## 3. Architecture & data flow

```
Browser (Supabase session)
 ├─ fetch /api/... (Authorization: Bearer <access_token>)  ─┐  Next rewrite forwards
 ├─ <a>/<iframe>/<img> /api/... (Cookie: sb-<ref>-auth-token)┤  headers+cookies →  FastAPI
 └─ supabase-js  ── reads documents (RLS owner_id=auth.uid()) ──→ Supabase Postgres
FastAPI:
  current_user(request) → verify HS256 JWT (Bearer or cookie) → uid (sub)
  on upload: service-role INSERT documents(owner_id=uid, …)
  on access: service-role SELECT documents WHERE id=doc_id → require owner_id==uid else 404
OnlyOffice DS ── docx fetch / save callback ──→ FastAPI (OnlyOffice JWT, unchanged)
```

## 4. `documents` table (Supabase Postgres) + RLS

Run once in the Supabase SQL editor (user task; or via Supabase MCP):

```sql
create table public.documents (
  id           text primary key,                       -- = backend doc_id
  owner_id     uuid not null references auth.users(id) on delete cascade,
  title        text,
  source_lang  text,
  target_lang  text,
  status       text not null default 'processing',     -- 'processing' | 'done' | 'error'
  created_at   timestamptz not null default now()
);
alter table public.documents enable row level security;
-- Frontend reads only its own rows; the backend writes via the service role (bypasses RLS).
create policy "owner can read own documents" on public.documents
  for select using (auth.uid() = owner_id);
create index documents_owner_created_idx on public.documents (owner_id, created_at desc);
```

No INSERT/UPDATE/DELETE policies — those happen server-side with the service role. (Frontend never writes the table.)

## 5. JWT verification (FastAPI)

New module `backend/auth.py`:
- `current_user(request) -> str` (FastAPI dependency): extract token, verify, return `uid`.
  - **Token extraction:** `Authorization: Bearer <t>` if present; else parse the Supabase auth cookie `sb-<ref>-auth-token` (and chunked `…token.0/.1`) — value is `base64-<base64(JSON session)>`; JSON has `access_token`. Reassemble chunks, strip the `base64-` prefix, b64-decode, json-parse, take `access_token`.
  - **Verify:** `jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")`. On failure → `HTTPException(401)`.
  - Return `payload["sub"]` (the user uid).
- `current_user_bearer(request)`: Bearer-only variant for mutating endpoints (ignores cookie → no CSRF via cookie on POSTs).
- Env: `SUPABASE_JWT_SECRET` (Supabase → Settings → API → JWT Secret). Reuse the existing `load_dotenv` pattern.

## 6. Endpoint protection (apply per tier)

| Endpoint | Tier | Auth | Ownership |
|---|---|---|---|
| `GET /api/health`, `GET /api/glossary/template` | public | none | — |
| `POST /api/documents` (upload) | JSON | Bearer (`current_user_bearer`) | **creates** row (owner=uid) |
| `POST /api/detect-language` | JSON | Bearer | — (no doc yet) |
| `GET .../status`, `.../result`, `.../segments`, `.../glossary-report`, `.../editor-config` | JSON | Bearer | require owner |
| `POST .../translate`, `.../retranslate-segment`, `.../render`, `.../replace`, `.../replace/preview`, `.../glossary/term` | JSON mutating | Bearer | require owner |
| `GET .../download`, `.../preview`, `.../assets/{name}`, `.../source-html/{path}`, `.../view` | nav GET | cookie (or Bearer) | require owner |
| `GET .../docx`, `POST .../onlyoffice-callback` | DS | OnlyOffice JWT (unchanged) | — (docx URL only issued by authed `editor-config`) |

**Ownership helper** `require_owner(doc_id, uid)`: service-role `SELECT owner_id FROM documents WHERE id=doc_id`; if no row or `owner_id != uid` → `HTTPException(404)` (not 403 — don't leak existence). Used by every doc endpoint above.

## 7. Upload + status lifecycle

- `upload`: after creating the doc (existing logic), `INSERT documents(id=doc_id, owner_id=uid, title=<derived filename/title>, source_lang, target_lang, status='processing')` via service role. Return `doc_id` as today.
- Job completion: when the background job finishes (in `jobs.py`/`process.py` done-path), `UPDATE documents SET status='done'` (or `'error'`). Keep minimal — a single update at terminal state. The frontend list shows 변환 중/완료 from `status`.

## 8. Backend ↔ Supabase access

- Use **`supabase` (supabase-py)** with the **service-role key** for `documents` writes/reads, OR direct PostgREST via `httpx` (`{SUPABASE_URL}/rest/v1/documents` with `apikey`+`Authorization: Bearer <service_role>`). Decide in the plan; prefer the lighter of the two given existing deps (the backend likely already has `httpx`/`requests`). Wrap in a small `backend/documents_store.py` (functions: `create`, `get_owner`, `set_status`) so the data layer is swappable and testable.
- Env: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (Supabase → Settings → API → service_role secret). **Backend-only secret — never sent to the browser.**

## 9. Frontend changes

- **`src/lib/api.ts`:** add a small `authedFetch` that injects `Authorization: Bearer <access_token>` (from the browser client `supabase.auth.getSession()`) into every JSON/mutating call. `downloadUrl`/`previewUrl` (used as `<a>`/`<iframe>` src) stay header-less — they authenticate via the forwarded cookie. (Assets/source-html are backend-built URLs in the viewer — also cookie.)
- **`/app` dashboard** (`src/app/app/page.tsx`): keep the upload card; add a **"내 문서"** section that reads `documents` via supabase-js (`createClient().from("documents").select(...).order("created_at",{ascending:false})` — RLS returns only the user's rows), rendering each as a card/row → `/c/[id]?lang=<target_lang>`, showing **title · status(변환 중/완료) · 날짜**. Empty state: "아직 문서가 없어요 — 위에서 업로드해 보세요."
- No new frontend env (browser client already configured). Middleware already gates `/app`.

## 10. Existing documents

Wipe `backend/storage/*` (delete all current doc folders) — clean slate; no migration. (Document this as a one-time manual/scripted step in the plan.)

## 11. Setup (user tasks)

1. **Backend `backend/.env`** (agents can't write `.env`): add `SUPABASE_JWT_SECRET`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
2. **SQL migration:** run §4 SQL in the Supabase SQL editor (creates `documents` + RLS).
3. Restart backend; rebuild/restart frontend.

## 12. Verification

- Backend unit tests (pytest exists in `backend/`): `current_user` accepts a valid HS256 token (signed with a test secret) from header AND cookie; rejects missing/invalid/expired; `require_owner` returns owner-match / raises 404 on mismatch/missing. `documents_store` create/get/set_status against a mocked Supabase client.
- Frontend: `npm run lint` + build (inline placeholder env). Manual: upload while signed in → appears in "내 문서"; open own doc → works; **another account cannot open it** (404); download/preview still work (cookie); sign-out → `/app` gated.
- Full cross-user E2E needs two accounts + the live project + SQL applied → user-run.

## 13. Security notes

- **Bearer for mutating** endpoints (`current_user_bearer`) — cookie alone never authorizes a state change (CSRF-safe). Read-only GET navigation may use the cookie.
- **404 (not 403)** for non-owned/missing docs — no existence leak.
- **Service-role key** is backend-only; never exposed to the browser or in `NEXT_PUBLIC_*`.
- DS docx endpoint stays OnlyOffice-JWT-gated; its signed URL is only minted by the now-authed `editor-config`, so only the owner can obtain it.

## 14. Out of scope (flagged)

- **Document file durability on deploy** — files still live on the backend's local disk (ephemeral on most PaaS). Ownership is durable (Postgres); moving the *files* to Supabase Storage/S3 is a separate future effort.
- Document sharing, teams/orgs, roles, per-doc permissions beyond single-owner.
- Backend rate limiting / quotas.

## 15. Branch

`backend-userdocs-2026.06.11` (off `main`).
