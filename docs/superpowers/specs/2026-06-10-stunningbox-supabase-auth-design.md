# StunningBox — Supabase Authentication (Frontend Gate) Design

**Date:** 2026-06-10
**Scope:** Replace the UI-only `/login` with **real Supabase authentication** — email/password + Google OAuth, SSR cookie sessions, and Next middleware that gates the app routes. Frontend-only; the FastAPI backend is unchanged.

---

## 1. Context (verified)

- App = layout-preserving PDF/HWP/DOCX → translated-DOCX tool. StunningBox brand merged (`/` landing, `/login` UI-only, `/app` upload tool, `/p/[id]`, `/c/[id]`).
- **Supabase is greenfield here:** no SDK, no env, no config, no middleware. `frontend/package.json` has only `next`/`react`.
- Current `/login` is UI-only: `onSubmit` → `router.push("/app")`; Google button disabled; `가입` → `/app`.
- Funnel: `/` → `/login` → `/app`. FastAPI stores by `doc_id`, no users.

## 2. Decisions (agreed)

- **Depth:** Frontend gate. Real Supabase auth + Next middleware gating `/app`, `/p/:id`, `/c/:id`. FastAPI untouched.
- **Methods:** Email + password (email confirmation ON) and Google OAuth. (No magic link.)
- **Project:** A **brand-new** Supabase project (old prototype project left untouched). User supplies URL + anon key.
- **Session strategy:** `@supabase/ssr` cookie-based sessions (the official App Router pattern; enables server/middleware gating with no auth flash). Not client-only.
- **Signup:** dedicated `/signup` page (mirrors `/login`). **Email confirmation ON.**

## 3. Goals / Non-goals

**Goals:** real accounts; sign-in (email/pw + Google); sign-up with email confirmation; **password reset (request email + set new password)**; sign-out; SSR cookie session shared across server/client/middleware; protected app routes with redirect-back; Korean error UX.

**Non-goals (explicitly deferred — separate future specs):**
- Backend Supabase-JWT verification → **the FastAPI API stays reachable by `doc_id`** (stated caveat).
- Per-user document ownership ("내 문서").
- Magic link, MFA, a full account-settings page (beyond the password-update screen).

## 4. Architecture & data flow

`@supabase/ssr` with cookie sessions. Auth traffic goes **browser↔Supabase** and **Next-server↔Supabase directly** — NOT through the `/api` FastAPI proxy (no backend/proxy change).

Three integration points:
- **Browser client** (`createBrowserClient`) — client components (login/signup forms, Google button, sign-out, AuthMenu).
- **Server client** (`createServerClient` over `next/headers` cookies) — route handlers (auth callbacks).
- **Middleware client** — refreshes the session cookie on every request and makes the gate decision.

**Sequence (unauth hits a protected route):** `/app` → middleware `getUser()` returns null → redirect `/login?redirect=/app` → user signs in (`signInWithPassword`) or Google (`signInWithOAuth` → `/auth/callback` → `exchangeCodeForSession`) → session cookies set → redirect to `/app` → middleware `getUser()` returns user → allowed.

**Security note:** the gate uses `supabase.auth.getUser()` (revalidates the JWT with Supabase), **not** `getSession()` (reads cookie only, spoofable). `getClaims()` (local asymmetric verification) is a valid later optimization but out of scope.

## 5. File structure

| File | Responsibility |
|---|---|
| `frontend/src/lib/supabase/client.ts` | **Create.** Browser client factory (`createBrowserClient`). |
| `frontend/src/lib/supabase/server.ts` | **Create.** Server client factory (`createServerClient` over `cookies()` with `getAll`/`setAll`). |
| `frontend/src/middleware.ts` | **Create.** Session refresh + route gate (matcher excludes static/auth/public). |
| `frontend/src/app/auth/callback/route.ts` | **Create.** Google OAuth code exchange (`exchangeCodeForSession`) → redirect target/`/app`. |
| `frontend/src/app/auth/confirm/route.ts` | **Create.** Email link handler (`verifyOtp` with `token_hash` + `type`) for both signup-confirmation and password-**recovery**; honors a `next` param → recovery goes to `/update-password`, signup goes to `/app`. |
| `frontend/src/app/signup/page.tsx` | **Create.** Signup form (mirrors login); `signUp` → "확인 메일을 보냈어요" state. |
| `frontend/src/app/forgot-password/page.tsx` | **Create.** Email input → `resetPasswordForEmail(email, { redirectTo: \`${origin}/auth/confirm?next=/update-password\` })` → "재설정 메일을 보냈어요" state. |
| `frontend/src/app/update-password/page.tsx` | **Create.** New-password input → `updateUser({ password })` → success → `/app`. Requires the (recovery) session set by `/auth/confirm`. |
| `frontend/src/components/AuthMenu.tsx` | **Create.** Client component: signed-in email + `로그아웃` (`signOut` → `/login`). |
| `frontend/src/app/login/page.tsx` | **Modify.** Wire `signInWithPassword` + enable Google `signInWithOAuth`; honor `?redirect=`; add "비밀번호를 잊으셨나요?" → `/forgot-password`; Korean errors. |
| `frontend/src/app/app/page.tsx` | **Modify.** Add `<AuthMenu/>` to the masthead action area. |
| `frontend/src/app/c/[id]/page.tsx` | **Modify.** Add `<AuthMenu/>` to the action bar (near 새 문서). |
| `frontend/package.json` | **Modify.** Add `@supabase/supabase-js`, `@supabase/ssr`. |
| `frontend/.env.local` | **Create (local, gitignored).** `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (user values). |

`/p/[id]` is protected by middleware but needs no UI change (it's a transient polling screen that auto-routes to `/c/[id]`). Landing (`/`) stays public and unchanged (optional future polish: swap 로그인 for an account link when signed in).

## 6. Middleware gating rules

- **Protected (require a session):** `/app`, `/p/:path*`, `/c/:path*`, `/update-password` (reachable via the recovery session that `/auth/confirm` sets, or by an already-signed-in user changing their password).
- **Public:** `/`, `/login`, `/signup`, `/forgot-password`, `/auth/*`, Next internals/static, `/icon.svg`.
- Unauthenticated on a protected path → `307` redirect to `/login?redirect=<pathname + search>`.
- Authenticated on `/login` or `/signup` → redirect to `/app`. (`/forgot-password` and `/update-password` are NOT in this redirect rule — a signed-in user may still reset/update a password.)
- Always return the response that carries refreshed Supabase cookies (per `@supabase/ssr` middleware pattern — copy cookies onto any redirect response too).
- `matcher` excludes `_next/static`, `_next/image`, favicon/icon, and other static assets.

## 7. Auth UX details

- **Login** (`/login`): email/password fields wired to `signInWithPassword`. On success → `router.push(redirect ?? "/app")` (+ `router.refresh()` so server components see the session). On error → inline Korean message (e.g. 잘못된 이메일/비밀번호, 이메일 미확인). Google button (remove `disabled`) → `signInWithOAuth({ provider: "google", options: { redirectTo: \`${location.origin}/auth/callback?redirect=${redirect}\` } })`. The already-authenticated → `/app` redirect is owned by **middleware** (§6); the page does not duplicate it. `가입` → `/signup`.
- **Signup** (`/signup`): email/password → `signUp({ email, password, options: { emailRedirectTo: \`${origin}/auth/confirm\` } })`. Because confirmation is ON, render a success state: "확인 메일을 보냈어요 — 메일의 링크를 눌러 가입을 완료하세요." Link back to `/login`. Reuses the split-panel layout + `<Logo tone="onAccent"/>`.
- **Forgot password** (`/forgot-password`): email → `resetPasswordForEmail(email, { redirectTo: \`${origin}/auth/confirm?next=/update-password\` })`. Always render the same neutral success state ("재설정 메일을 보냈어요 — 메일이 도착했다면 링크로 새 비밀번호를 설정하세요.") regardless of whether the email exists (no account-enumeration). Reached from the `/login` "비밀번호를 잊으셨나요?" link.
- **Update password** (`/update-password`): runs after `/auth/confirm` verifies the recovery `token_hash` and sets a session (so the page has an authenticated session). New-password (+ confirm) field → `updateUser({ password })` → on success "비밀번호를 변경했어요" → `router.push("/app")`. If reached without a session, middleware bounces to `/login` (acceptable; the link may have expired).
- **AuthMenu**: reads the user (browser client `getUser`/`onAuthStateChange`); shows the email (truncated) + `로그아웃` (`signOut()` → `router.push("/login")` + refresh). Placed in `/app` masthead and `/c/[id]` action bar.
- **Errors:** map common Supabase auth errors to friendly Korean copy; callback/confirm failures → `/login?error=<code>` rendered as an inline message.

## 8. Environment & dev origin

- `frontend/.env.local` (gitignored): `NEXT_PUBLIC_SUPABASE_URL=…`, `NEXT_PUBLIC_SUPABASE_ANON_KEY=…`.
- ⚠️ `NEXT_PUBLIC_*` is **inlined at build time** (like `BACKEND_URL`). Set it for `npm run build`. For local prod-preview: `BACKEND_URL=http://127.0.0.1:8001 npm run build && npx next start -p 3000`.
- **Standardize the dev/preview origin on `http://localhost:3000`** so OAuth + email links resolve to a registered URL (we've been using ad-hoc ports; auth requires a fixed, registered origin). The anon key is browser-safe by design (RLS protects data; we have no app tables), but still lives in `.env.local`.

## 9. Supabase dashboard setup (user task — exact checklist)

1. Create a **new project**; from Project Settings → API copy **Project URL** and **anon public** key → `frontend/.env.local`.
2. Authentication → Providers → **Email**: enabled; **Confirm email = ON**.
3. Authentication → Providers → **Google**: enabled. In Google Cloud Console create an **OAuth 2.0 Web client**, set **Authorized redirect URI** to `https://<project-ref>.supabase.co/auth/v1/callback`, then paste the **Client ID + Client secret** into Supabase.
4. Authentication → URL Configuration → **Site URL** = `http://localhost:3000`; **Redirect URLs** add `http://localhost:3000/**` (add the production origin later).
5. No tables / RLS / SQL needed (auth uses the managed `auth.users`).

## 10. Dependencies & API notes

- `@supabase/supabase-js` + `@supabase/ssr` (latest). At implementation time, confirm the **current `@supabase/ssr` cookie API** (it uses `cookies: { getAll, setAll }` — the older `get`/`set`/`remove` is deprecated) via the supabase skill / context7. Heed `frontend/AGENTS.md` (Next 16 has breaking changes — check `node_modules/next/dist/docs/` for middleware/route-handler specifics before writing).

## 11. Verification

- `cd frontend && npm run lint && NEXT_PUBLIC_SUPABASE_URL=… NEXT_PUBLIC_SUPABASE_ANON_KEY=… BACKEND_URL=http://127.0.0.1:8001 npm run build` — must pass, no new lint errors (3 pre-existing known).
- Frontend has **no unit-test runner**; verify via build + manual browser walk.
- **Full E2E requires the live project + the user's keys + Google creds**, so the user completes §9 and runs the final E2E:
  - Unauth → `/app` bounces to `/login?redirect=/app`.
  - Signup → confirmation email → link → `/auth/confirm` → `/app`.
  - Login (email/pw) → `/app`; wrong password → Korean error.
  - Google → consent → `/auth/callback` → `/app`.
  - Forgot password → email → recovery link → `/auth/confirm` → `/update-password` → set new password → `/app`; then sign in with the new password.
  - `로그아웃` → `/login`; signed-in visiting `/login` → `/app`.
  - Light + dark; reduced-motion; mobile.

## 12. Branch

`supabase-auth-2026.06.10` (off `main`).
