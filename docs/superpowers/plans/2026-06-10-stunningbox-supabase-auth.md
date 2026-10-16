# StunningBox Supabase Auth (Frontend Gate) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the UI-only `/login` with real Supabase auth — email/password + Google OAuth + password reset, SSR cookie sessions, and Next middleware that gates `/app`·`/p`·`/c`·`/update-password`.

**Architecture:** `@supabase/ssr` cookie sessions. A browser client (client components), a server client (route handlers, async `cookies()`), and a middleware client that refreshes the session and gates protected routes. Auth traffic goes browser/Next-server ↔ Supabase directly (not through the `/api` FastAPI proxy). Backend unchanged.

**Tech Stack:** Next.js 16.2.6 App Router, React 19, Tailwind v4, `@supabase/ssr`, `@supabase/supabase-js`.

**Spec:** `docs/superpowers/specs/2026-06-10-stunningbox-supabase-auth-design.md`
**Branch:** `supabase-auth-2026.06.10` (already created/checked out).

---

## ⚠️ Read before starting

1. **API confirmed (context7, 2026-06-10):** server client = `const cookieStore = await cookies()` then `createServerClient(url, key, { cookies: { getAll, setAll } })`. Middleware = `getAll`/`setAll` on `request.cookies`/response + `await supabase.auth.getUser()` (NOT `getSession`; never insert code between client creation and `getUser`). Callback = `exchangeCodeForSession`. These are the ONLY cookie patterns to use (the deprecated `get`/`set`/`remove` cookie methods must NOT be used).
2. **Next 16 middleware vs proxy — VERIFY IN TASK 2:** Next 16.2.6 may use `src/proxy.ts` exporting `proxy()` instead of `src/middleware.ts` exporting `middleware()` (the Supabase example exports `proxy`). Per `frontend/AGENTS.md`, read `node_modules/next/dist/docs/` to confirm the correct filename + export name for 16.2.6, and use it. The function body is identical either way.
3. **Build needs env — agents do NOT write `.env*` (secret guard).** Tooling deny-lists writing any `.env*` file (and blocks circumventing it). So **agents never create `.env.local`**. Instead, every build passes the placeholder `NEXT_PUBLIC_*` **inline** (they're inlined at build time). The **USER** creates `frontend/.env.local` (gitignored) with the REAL Project URL + anon key at the dashboard step (Task 8) and rebuilds for live auth. Exact file contents for the user are in Task 8.
4. **No unit-test runner.** The canonical verify command for EVERY task (use this exact form in all build steps below) is:
   ```bash
   npm run lint && NEXT_PUBLIC_SUPABASE_URL=https://placeholder.supabase.co NEXT_PUBLIC_SUPABASE_ANON_KEY=placeholder-anon-key BACKEND_URL=http://127.0.0.1:8001 npm run build
   ```
   Lint must show only the 3 known pre-existing errors (`GlossaryManagePanel.tsx:40`; `onlyofficeConnector.ts:28` & `:29`; + 1 pre-existing warning `OnlyOfficeEditor.tsx:104`). Real login E2E is the user's job (spec §11) — it needs the live project + Google creds.
5. **Run npm from `frontend/`. Do not switch branches.**

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/.env.local` | **USER-created (gitignored), not by agents.** `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` — real values at Task 8. Agents build with inline placeholders. |
| `frontend/src/lib/supabase/client.ts` | **Create.** Browser client factory. |
| `frontend/src/lib/supabase/server.ts` | **Create.** Server client factory (async). |
| `frontend/src/middleware.ts` *(or `src/proxy.ts` — see Task 2)* | **Create.** Session refresh + route gate. |
| `frontend/src/app/auth/callback/route.ts` | **Create.** Google OAuth `exchangeCodeForSession`. |
| `frontend/src/app/auth/confirm/route.ts` | **Create.** Email/recovery `verifyOtp`. |
| `frontend/src/components/AuthShell.tsx` | **Create.** Shared split brand-panel + form-slot layout for the 4 auth pages. |
| `frontend/src/app/login/page.tsx` | **Rewrite.** Real `signInWithPassword` + Google + forgot link + `?redirect=`. |
| `frontend/src/app/signup/page.tsx` | **Rewrite.** `signUp` + email-confirmation state. |
| `frontend/src/app/forgot-password/page.tsx` | **Create.** `resetPasswordForEmail`. |
| `frontend/src/app/update-password/page.tsx` | **Create.** `updateUser({ password })`. |
| `frontend/src/components/AuthMenu.tsx` | **Create.** Email + 로그아웃. |
| `frontend/src/app/app/page.tsx` | **Modify.** Add `<AuthMenu/>` to masthead. |
| `frontend/src/app/c/[id]/page.tsx` | **Modify.** Add `<AuthMenu/>` to action bar. |
| `frontend/package.json` | **Modify.** Add the two deps. |

---

## Task 1: Dependencies, env, Supabase client factories

**Files:** Create `frontend/src/lib/supabase/client.ts`, `frontend/src/lib/supabase/server.ts`; modify `frontend/package.json`. (Do NOT create `.env.local` — see Read-before-starting #3; builds use inline placeholder env.)

- [ ] **Step 1: Install deps**

Run from `frontend/`:
```bash
npm install @supabase/ssr @supabase/supabase-js
```

- [ ] **Step 2: (no env file)** — Agents must NOT write `.env.local` (deny-listed secret guard). Builds use inline placeholder `NEXT_PUBLIC_*` (Step 5). The user creates the real `.env.local` at Task 8.

- [ ] **Step 3: Create `frontend/src/lib/supabase/client.ts`**

```ts
import { createBrowserClient } from "@supabase/ssr";

// Browser Supabase client for use inside client components.
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
}
```

- [ ] **Step 4: Create `frontend/src/lib/supabase/server.ts`**

```ts
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

// Server Supabase client for route handlers / server code. cookies() is async in Next 16.
export async function createClient() {
  const cookieStore = await cookies();
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          try {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options),
            );
          } catch {
            // Called from a Server Component — safe to ignore; middleware refreshes the session.
          }
        },
      },
    },
  );
}
```

- [ ] **Step 5: Verify** (inline placeholder env — see Read-before-starting #4)

```bash
npm run lint && NEXT_PUBLIC_SUPABASE_URL=https://placeholder.supabase.co NEXT_PUBLIC_SUPABASE_ANON_KEY=placeholder-anon-key BACKEND_URL=http://127.0.0.1:8001 npm run build
```
Expected: lint only the 3 known pre-existing errors; build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/supabase/
git commit -m "feat(web): add @supabase/ssr + browser/server client factories"
```

---

## Task 2: Middleware — session refresh + route gate

**Files:** Create `frontend/src/middleware.ts` **OR** `frontend/src/proxy.ts` (verify in Step 1).

- [ ] **Step 1: Confirm the Next 16.2.6 convention**

Read `node_modules/next/dist/docs/` (and/or the Next 16 middleware guide) to determine whether this version uses `middleware.ts` exporting `middleware` or `proxy.ts` exporting `proxy`. Use the correct filename + export name. Everything else below is identical. (If both work, prefer `middleware.ts`/`middleware`.)

- [ ] **Step 2: Create the middleware file** with this body (rename file/export per Step 1)

```ts
import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  // IMPORTANT: call getUser() immediately, with no code between it and createServerClient.
  // getUser() revalidates the JWT with Supabase (getSession() only reads the cookie).
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  const isProtected =
    path === "/app" ||
    path.startsWith("/app/") ||
    path.startsWith("/p/") ||
    path.startsWith("/c/") ||
    path === "/update-password";
  const isAuthPage = path === "/login" || path === "/signup";

  // Unauthenticated → bounce off protected routes to /login with a redirect-back.
  if (!user && isProtected) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    url.searchParams.set("redirect", path + request.nextUrl.search);
    const redirectRes = NextResponse.redirect(url);
    response.cookies.getAll().forEach((c) => redirectRes.cookies.set(c));
    return redirectRes;
  }

  // Authenticated users shouldn't see login/signup.
  if (user && isAuthPage) {
    const url = request.nextUrl.clone();
    url.pathname = "/app";
    url.search = "";
    const redirectRes = NextResponse.redirect(url);
    response.cookies.getAll().forEach((c) => redirectRes.cookies.set(c));
    return redirectRes;
  }

  return response;
}

export const config = {
  matcher: [
    // Run on everything except Next internals and static assets (so /, /login, /auth/* still refresh the session).
    "/((?!_next/static|_next/image|favicon.ico|icon.svg|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
```

- [ ] **Step 3: Verify**

`npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build` — lint = 3 known errors; build succeeds. (Middleware does not run at build time; this only checks it compiles.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/middleware.ts   # or frontend/src/proxy.ts
git commit -m "feat(web): Supabase session middleware + route gate (/app·/p·/c·/update-password)"
```

---

## Task 3: Auth route handlers

**Files:** Create `frontend/src/app/auth/callback/route.ts`, `frontend/src/app/auth/confirm/route.ts`.

- [ ] **Step 1: Create `frontend/src/app/auth/callback/route.ts`** (Google OAuth code exchange)

```ts
import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  let next = searchParams.get("redirect") ?? "/app";
  if (!next.startsWith("/")) next = "/app";

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
  }
  return NextResponse.redirect(`${origin}/login?error=oauth`);
}
```

- [ ] **Step 2: Create `frontend/src/app/auth/confirm/route.ts`** (email confirmation + password recovery)

```ts
import { type EmailOtpType } from "@supabase/supabase-js";
import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const token_hash = searchParams.get("token_hash");
  const type = searchParams.get("type") as EmailOtpType | null;
  // recovery links carry next=/update-password; signup confirmation defaults to /app.
  let next = searchParams.get("next") ?? "/app";
  if (!next.startsWith("/")) next = "/app";

  if (token_hash && type) {
    const supabase = await createClient();
    const { error } = await supabase.auth.verifyOtp({ type, token_hash });
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
  }
  return NextResponse.redirect(`${origin}/login?error=confirm`);
}
```

- [ ] **Step 3: Verify** — `npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build` (3 known errors; build OK; routes include `/auth/callback`, `/auth/confirm`).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/auth/
git commit -m "feat(web): /auth/callback (OAuth) + /auth/confirm (email+recovery) handlers"
```

---

## Task 4: AuthShell + rewire /login

**Files:** Create `frontend/src/components/AuthShell.tsx`; rewrite `frontend/src/app/login/page.tsx`.

- [ ] **Step 1: Create `frontend/src/components/AuthShell.tsx`** (shared split layout)

```tsx
"use client";

import type { ReactNode } from "react";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";

// Shared auth-page chrome: gradient brand panel (lg+) + centered form column. The page
// supplies its form via children. Mirrors the original /login split layout.
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-h-0 flex-1">
      <div className="absolute right-4 top-4 z-10">
        <ThemeToggle />
      </div>

      <div
        className="relative hidden flex-col justify-between p-12 lg:flex lg:w-1/2"
        style={{ background: "var(--gradient-brand)" }}
      >
        <Logo size={32} tone="onAccent" href="/" />
        <div>
          <p className="font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.2, color: "var(--text-on-accent)" }}>
            레이아웃은 그대로,<br />번역은 완벽하게
          </p>
          <p className="mt-3 max-w-sm" style={{ fontSize: "var(--fs-base)", color: "rgba(255,255,255,0.9)" }}>
            PDF·Word·HWP의 구조를 지키며 본문만 번역합니다.
          </p>
        </div>
        <p className="slug" style={{ color: "rgba(255,255,255,0.8)" }}>© 2026 StunningBox</p>
      </div>

      <div className="flex flex-1 items-center justify-center overflow-y-auto p-6 lg:w-1/2">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex justify-center lg:hidden">
            <Logo size={30} href="/" />
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Rewrite `frontend/src/app/login/page.tsx`** (full replacement)

```tsx
"use client";

import { Suspense, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AuthShell } from "@/components/AuthShell";
import { createClient } from "@/lib/supabase/client";

function LoginInner() {
  const router = useRouter();
  const redirect = useSearchParams().get("redirect") ?? "/app";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: err } = await supabase.auth.signInWithPassword({ email, password });
    if (err) {
      setError(
        err.message.includes("Email not confirmed")
          ? "이메일 인증이 완료되지 않았어요. 메일의 링크를 확인해 주세요."
          : "이메일 또는 비밀번호가 올바르지 않아요.",
      );
      setBusy(false);
      return;
    }
    router.push(redirect);
    router.refresh();
  }

  async function onGoogle() {
    setError(null);
    const supabase = createClient();
    const { error: err } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${location.origin}/auth/callback?redirect=${encodeURIComponent(redirect)}` },
    });
    if (err) setError("Google 로그인을 시작하지 못했어요.");
  }

  return (
    <>
      <h1 className="text-2xl font-bold leading-tight">다시 오신 걸 환영해요</h1>
      <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>스터닝박스에 로그인하세요.</p>

      <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="label">이메일</span>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="field" placeholder="you@example.com" autoComplete="email" />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="label flex items-center justify-between">
            비밀번호
            <Link href="/forgot-password" className="text-sm font-semibold" style={{ color: "var(--primary-hover)" }}>비밀번호를 잊으셨나요?</Link>
          </span>
          <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} className="field" placeholder="••••••••" autoComplete="current-password" />
        </label>
        {error && <p className="text-sm" style={{ color: "var(--danger)" }}>{error}</p>}
        <button type="submit" disabled={busy} className="btn-primary mt-1 w-full">{busy ? "로그인 중…" : "로그인"}</button>
      </form>

      <div className="my-5 flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="slug">또는</span>
        <span className="h-px flex-1 bg-border" />
      </div>

      <button type="button" onClick={onGoogle} className="btn-secondary w-full">Google로 계속</button>

      <p className="mt-6 text-center text-text-2" style={{ fontSize: "var(--fs-body)" }}>
        계정이 없으신가요? <Link href="/signup" className="font-semibold" style={{ color: "var(--primary-hover)" }}>가입</Link>
      </p>
    </>
  );
}

export default function LoginPage() {
  return (
    <AuthShell>
      {/* useSearchParams must sit inside a Suspense boundary for static generation. */}
      <Suspense>
        <LoginInner />
      </Suspense>
    </AuthShell>
  );
}
```

- [ ] **Step 3: Verify** — `npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build`. Lint = 3 known errors (confirm NO "useSearchParams should be wrapped in a suspense boundary" build error). Build OK.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/AuthShell.tsx "frontend/src/app/login/page.tsx"
git commit -m "feat(web): wire /login to Supabase (password + Google), shared AuthShell"
```

---

## Task 5: /signup page

**Files:** Rewrite `frontend/src/app/signup/page.tsx`.

> Note: `/signup` already exists? No — there is no signup page yet; the current `가입` link pointed at `/app`. This task **creates** `frontend/src/app/signup/page.tsx`.

- [ ] **Step 1: Create `frontend/src/app/signup/page.tsx`**

```tsx
"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { createClient } from "@/lib/supabase/client";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: err } = await supabase.auth.signUp({
      email,
      password,
      options: { emailRedirectTo: `${location.origin}/auth/confirm` },
    });
    if (err) {
      setError(err.message.includes("already registered") ? "이미 가입된 이메일이에요." : "가입에 실패했어요. 잠시 후 다시 시도해 주세요.");
      setBusy(false);
      return;
    }
    setSent(true);
  }

  return (
    <AuthShell>
      {sent ? (
        <div>
          <h1 className="text-2xl font-bold leading-tight">확인 메일을 보냈어요</h1>
          <p className="mt-3 text-text-2" style={{ fontSize: "var(--fs-body)" }}>
            {email}로 보낸 메일의 링크를 눌러 가입을 완료하세요.
          </p>
          <Link href="/login" className="btn-secondary mt-6 inline-flex">로그인으로</Link>
        </div>
      ) : (
        <>
          <h1 className="text-2xl font-bold leading-tight">계정 만들기</h1>
          <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>스터닝박스를 무료로 시작하세요.</p>
          <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="label">이메일</span>
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="field" placeholder="you@example.com" autoComplete="email" />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="label">비밀번호</span>
              <input type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} className="field" placeholder="6자 이상" autoComplete="new-password" />
            </label>
            {error && <p className="text-sm" style={{ color: "var(--danger)" }}>{error}</p>}
            <button type="submit" disabled={busy} className="btn-primary mt-1 w-full">{busy ? "가입 중…" : "가입하기"}</button>
          </form>
          <p className="mt-6 text-center text-text-2" style={{ fontSize: "var(--fs-body)" }}>
            이미 계정이 있으신가요? <Link href="/login" className="font-semibold" style={{ color: "var(--primary-hover)" }}>로그인</Link>
          </p>
        </>
      )}
    </AuthShell>
  );
}
```

- [ ] **Step 2: Verify** — `npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build` (3 known errors; `/signup` Static).

- [ ] **Step 3: Commit**

```bash
git add "frontend/src/app/signup/page.tsx"
git commit -m "feat(web): /signup with email-confirmation flow"
```

---

## Task 6: /forgot-password + /update-password

**Files:** Create `frontend/src/app/forgot-password/page.tsx`, `frontend/src/app/update-password/page.tsx`.

- [ ] **Step 1: Create `frontend/src/app/forgot-password/page.tsx`**

```tsx
"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { createClient } from "@/lib/supabase/client";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const supabase = createClient();
    // Recovery email lands on /auth/confirm (verifyOtp type=recovery) → /update-password.
    await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${location.origin}/auth/confirm?next=/update-password`,
    });
    setSent(true); // Always neutral — do not reveal whether the email exists.
  }

  return (
    <AuthShell>
      {sent ? (
        <div>
          <h1 className="text-2xl font-bold leading-tight">재설정 메일을 보냈어요</h1>
          <p className="mt-3 text-text-2" style={{ fontSize: "var(--fs-body)" }}>
            메일이 도착했다면 링크를 눌러 새 비밀번호를 설정하세요.
          </p>
          <Link href="/login" className="btn-secondary mt-6 inline-flex">로그인으로</Link>
        </div>
      ) : (
        <>
          <h1 className="text-2xl font-bold leading-tight">비밀번호 재설정</h1>
          <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>가입한 이메일로 재설정 링크를 보내드려요.</p>
          <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="label">이메일</span>
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="field" placeholder="you@example.com" autoComplete="email" />
            </label>
            <button type="submit" disabled={busy} className="btn-primary mt-1 w-full">{busy ? "보내는 중…" : "재설정 메일 보내기"}</button>
          </form>
          <p className="mt-6 text-center text-text-2" style={{ fontSize: "var(--fs-body)" }}>
            <Link href="/login" className="font-semibold" style={{ color: "var(--primary-hover)" }}>로그인으로 돌아가기</Link>
          </p>
        </>
      )}
    </AuthShell>
  );
}
```

- [ ] **Step 2: Create `frontend/src/app/update-password/page.tsx`**

```tsx
"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/AuthShell";
import { createClient } from "@/lib/supabase/client";

export default function UpdatePasswordPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: err } = await supabase.auth.updateUser({ password });
    if (err) {
      setError("비밀번호를 변경하지 못했어요. 링크가 만료되었을 수 있어요.");
      setBusy(false);
      return;
    }
    router.push("/app");
    router.refresh();
  }

  return (
    <AuthShell>
      <h1 className="text-2xl font-bold leading-tight">새 비밀번호 설정</h1>
      <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>새로 사용할 비밀번호를 입력하세요.</p>
      <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="label">새 비밀번호</span>
          <input type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} className="field" placeholder="6자 이상" autoComplete="new-password" />
        </label>
        {error && <p className="text-sm" style={{ color: "var(--danger)" }}>{error}</p>}
        <button type="submit" disabled={busy} className="btn-primary mt-1 w-full">{busy ? "변경 중…" : "비밀번호 변경"}</button>
      </form>
    </AuthShell>
  );
}
```

- [ ] **Step 3: Verify** — `npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build` (3 known errors; `/forgot-password`, `/update-password` present).

- [ ] **Step 4: Commit**

```bash
git add "frontend/src/app/forgot-password/page.tsx" "frontend/src/app/update-password/page.tsx"
git commit -m "feat(web): password reset (/forgot-password + /update-password)"
```

---

## Task 7: AuthMenu + wire into app + viewer

**Files:** Create `frontend/src/components/AuthMenu.tsx`; modify `frontend/src/app/app/page.tsx`, `frontend/src/app/c/[id]/page.tsx`.

- [ ] **Step 1: Create `frontend/src/components/AuthMenu.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

// Signed-in identity + sign-out, for the app/viewer chrome.
export function AuthMenu() {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      setEmail(session?.user?.email ?? null);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  async function signOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="flex items-center gap-2.5">
      {email && <span className="slug hidden max-w-[12rem] truncate sm:inline">{email}</span>}
      <button type="button" onClick={signOut} className="btn-secondary">로그아웃</button>
    </div>
  );
}
```

- [ ] **Step 2: Add `<AuthMenu/>` to the `/app` masthead** — `frontend/src/app/app/page.tsx`

Add the import after the existing `import { Logo } from "@/components/Logo";` line:
```tsx
import { AuthMenu } from "@/components/AuthMenu";
```
Then, in the masthead's right-hand cluster, place `<AuthMenu/>` after `<ThemeToggle />`. Replace:
```tsx
            <ThemeToggle />
          </div>
        </div>
      </header>
```
with:
```tsx
            <ThemeToggle />
            <AuthMenu />
          </div>
        </div>
      </header>
```
(If the exact surrounding lines differ, the rule is: add `<AuthMenu />` immediately after the `<ThemeToggle />` that sits in the masthead's right cluster — the one in the `<div className="flex items-center gap-2.5">` containing the mode pill.)

- [ ] **Step 3: Add `<AuthMenu/>` to the viewer action bar** — `frontend/src/app/c/[id]/page.tsx`

Add the import after the existing `import { Logo } from "@/components/Logo";` line:
```tsx
import { AuthMenu } from "@/components/AuthMenu";
```
In the action-bar right cluster, place `<AuthMenu/>` immediately after `<ThemeToggle />`. Replace:
```tsx
          <ThemeToggle />
          <Link href="/app" className="btn-secondary">
            새 문서
          </Link>
```
with:
```tsx
          <ThemeToggle />
          <AuthMenu />
          <Link href="/app" className="btn-secondary">
            새 문서
          </Link>
```

- [ ] **Step 4: Verify** — `npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build` (3 known errors; build OK).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AuthMenu.tsx "frontend/src/app/app/page.tsx" "frontend/src/app/c/[id]/page.tsx"
git commit -m "feat(web): AuthMenu (email + 로그아웃) in app + viewer chrome"
```

---

## Task 8: Final verification + Supabase setup + E2E (user)

**Files:** none (verification + handoff).

- [ ] **Step 1: Final build/lint** — from `frontend/`: `npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build`. Confirm only the 3 known pre-existing lint errors; build emits routes `/`, `/app`, `/login`, `/signup`, `/forgot-password`, `/update-password`, `/auth/callback`, `/auth/confirm`, `/c/[id]`, `/p/[id]`, `/icon.svg`.

- [ ] **Step 2: Structural browser check (placeholder env, no real auth)** — serve `BACKEND_URL=http://127.0.0.1:8001 npx next start -p 3000` and confirm the pages RENDER (light + dark + mobile): `/login` (Google enabled, forgot link), `/signup`, `/forgot-password`, `/update-password`. (Auth calls will fail against placeholder keys — that's expected; this only checks rendering + that middleware doesn't crash.) Note: visiting `/app` with placeholder keys → middleware `getUser()` returns no user → you'll be redirected to `/login?redirect=/app`, which itself demonstrates the gate.

- [ ] **Step 3: Hand off the live setup to the user** (spec §9). The user must, in their NEW Supabase project:
  1. **Create `frontend/.env.local`** (this file is the user's — gitignored, holds secrets) with the real values:
     ```
     NEXT_PUBLIC_SUPABASE_URL=https://<your-ref>.supabase.co
     NEXT_PUBLIC_SUPABASE_ANON_KEY=<your-anon-public-key>
     ```
  2. Authentication → Providers → Email: enabled, **Confirm email ON**.
  3. Authentication → Providers → Google: enabled; Google Cloud OAuth 2.0 Web client with redirect URI `https://<ref>.supabase.co/auth/v1/callback`; paste Client ID + Secret.
  4. Authentication → URL Configuration → Site URL `http://localhost:3000`; Redirect URLs add `http://localhost:3000/**`.
  Then **rebuild with the real `.env.local`** (it's auto-loaded; `NEXT_PUBLIC_*` inlines at build) and run the E2E walk: unauth `/app` → `/login?redirect=/app`; signup → email → `/auth/confirm` → `/app`; login (pw) → `/app`; wrong pw → Korean error; Google → consent → `/auth/callback` → `/app`; forgot → email → `/update-password` → set → `/app`; 로그아웃 → `/login`; signed-in `/login` → `/app`.

- [ ] **Step 4: Commit** (only if Step 2 surfaced a fix)

```bash
git add -A frontend/src
git commit -m "fix(web): <whatever the structural check surfaced>"
```

---

## Self-Review (plan vs spec)

**Spec coverage:** §4 clients → Task 1; §6 middleware gate → Task 2; §5 auth routes → Task 3; §7 login → Task 4, signup → Task 5, forgot/update → Task 6, AuthMenu → Task 7; §8 env + build-time + :3000 → Task 1 (.env.local) + Task 8 (serve on 3000); §9 dashboard + §11 E2E → Task 8 (user). AuthShell (DRY across 4 auth pages) added — consistent with spec's split-layout intent. ✓

**Placeholder scan:** `.env.local` placeholder values are intentional (real values are user-supplied secrets, gitignored) — not a plan gap. Every code step is complete. No "TBD".

**Type/name consistency:** `createClient` is the factory name in BOTH `lib/supabase/client.ts` (browser) and `lib/supabase/server.ts` (server) — they're imported from different paths (`@/lib/supabase/client` in client components/AuthMenu; `@/lib/supabase/server` in route handlers), so no collision. Route handlers (Task 3) import the **server** `createClient`; pages/components import the **browser** one — verified per file. The `redirect` query param is used consistently: middleware sets `?redirect=`, login reads `redirect`, login's Google `redirectTo` forwards `?redirect=` to `/auth/callback`, which reads `redirect`. The email flow uses `next` (`/auth/confirm?next=/update-password`); callback uses `redirect`; both default to `/app`. AuthShell props (`children`) match usage in all 4 pages.

---

## Execution Handoff

Implement with **superpowers:subagent-driven-development** (fresh subagent per task + two-stage review) or **superpowers:executing-plans** (inline, batched). Tasks are mostly sequential; Task 1 (clients) must precede all; AuthShell (Task 4) must precede Tasks 5–6.
