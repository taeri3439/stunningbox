# StunningBox Brand + Landing + Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the StunningBox brand, ship a marketing landing (`/`) + UI-only login (`/login`), move the upload tool to `/app`, and rebrand the 4 "PDF 구조 보존 번역기" spots. Funnel: `/` → `/login` → `/app`.

**Architecture:** Frontend-only (Next 16 App Router / React 19 / Tailwind v4). A shared `<Logo/>` component carries the brand. The current upload page moves from `app/page.tsx` to `app/app/page.tsx`; a new landing takes `/` and a new login takes `/login`. Everything reuses the existing design-system tokens/classes in `globals.css` (`.eyebrow .label .slug .text-gradient .btn-* .field .ticket`, `--fs-*`, `--gradient-brand`, ambient glow). No backend changes.

**Tech Stack:** Next.js 16.2.6, React 19, Tailwind v4 (CSS-config in `globals.css`), Pretendard (KO) + Inter (Latin).

**Spec:** `docs/superpowers/specs/2026-06-10-stunningbox-brand-landing-login-design.md`

**Branch:** `stunningbox-brand-2026.06.10` (already created/checked out).

**Testing note (read first):** the frontend has **no unit-test runner** (only `eslint` + `next build`). So "verify" in every task = `npm run lint` (must introduce **no new** errors — 3 are pre-existing and expected: `GlossaryManagePanel.tsx:40` set-state-in-effect, `onlyofficeConnector.ts:28-29` no-var ×2) and `BACKEND_URL=http://127.0.0.1:8001 npm run build` (must succeed). Visual checks are batched into the final task via chrome-devtools. Do not invent unit tests for static marketing markup (YAGNI).

**Build/run commands (all run from `frontend/`):**
- Lint: `npm run lint`
- Build: `BACKEND_URL=http://127.0.0.1:8001 npm run build`  ← the `/api/*` proxy is baked at build time; the env var MUST be set during build.
- Serve a built app on a free port: `BACKEND_URL=http://127.0.0.1:8001 npx next start -p 3015`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `frontend/src/components/Logo.tsx` | **Create.** Shared brand lockup: layered-box SVG mark + "Stunning**Box**" wordmark. Props: `size`, `wordmark`, `href`, `tone` (`"brand"`\|`"onAccent"`), `className`. |
| `frontend/src/app/icon.svg` | **Create.** Favicon = the layered-box mark (App Router auto-registers). |
| `frontend/src/app/app/page.tsx` | **Create (moved).** The existing `UploadPage`, masthead swapped to `<Logo/>`. Route `/app`. |
| `frontend/src/app/page.tsx` | **Replace.** New marketing landing (local section components). Route `/`. |
| `frontend/src/app/login/page.tsx` | **Create.** UI-only login (split layout). Route `/login`. |
| `frontend/src/app/layout.tsx` | **Modify.** Metadata title/description → StunningBox. |
| `frontend/src/app/p/[id]/page.tsx` | **Modify.** `<Logo/>` masthead; "처음으로" → `/app`. |
| `frontend/src/app/c/[id]/page.tsx` | **Modify.** `<Logo/>` masthead; "새 문서" + "처음으로" → `/app`. |

---

## Task 1: `<Logo/>` component + favicon

**Files:**
- Create: `frontend/src/components/Logo.tsx`
- Create: `frontend/src/app/icon.svg`

- [ ] **Step 1: Create the Logo component**

Create `frontend/src/components/Logo.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useId } from "react";

// The StunningBox brand lockup: a layered-box mark (two offset document sheets with a
// transform arrow — 원본 → 번역본, brand-gradient filled) + the "StunningBox" wordmark.
// tone="onAccent" renders white-on-gradient (for the gradient brand panels); default
// "brand" renders the gradient mark + gradient "Box" on a neutral surface.
export function Logo({
  size = 28,
  wordmark = true,
  href,
  tone = "brand",
  className = "",
}: {
  size?: number;
  wordmark?: boolean;
  href?: string;
  tone?: "brand" | "onAccent";
  className?: string;
}) {
  const gid = useId();
  const onAccent = tone === "onAccent";

  const mark = (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden
      role="img"
      style={{ flex: "none" }}
    >
      {!onAccent && (
        <defs>
          <linearGradient id={gid} x1="2" y1="2" x2="30" y2="30" gradientUnits="userSpaceOnUse">
            <stop stopColor="#07b9ce" />
            <stop offset="0.5" stopColor="#3969e7" />
            <stop offset="1" stopColor="#7d2ae7" />
          </linearGradient>
        </defs>
      )}
      {/* back sheet (원본) */}
      <rect x="6" y="4.5" width="15" height="20" rx="3" fill={onAccent ? "#fff" : `url(#${gid})`} opacity={onAccent ? 0.5 : 0.32} />
      {/* front sheet (번역본) */}
      <rect x="11" y="7.5" width="15" height="20" rx="3" fill={onAccent ? "#fff" : `url(#${gid})`} />
      {/* transform cue (→) on the front sheet */}
      <path
        d="M15 17.5h6M19 14.5l3 3-3 3"
        stroke={onAccent ? "#3969e7" : "#fff"}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.95"
      />
    </svg>
  );

  const content = (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      {mark}
      {wordmark &&
        (onAccent ? (
          <span className="font-bold tracking-tight text-white" style={{ fontSize: size * 0.62 }}>
            StunningBox
          </span>
        ) : (
          <span className="font-bold tracking-tight" style={{ fontSize: size * 0.62 }}>
            Stunning<span className="text-gradient">Box</span>
          </span>
        ))}
    </span>
  );

  return href ? (
    <Link href={href} aria-label="StunningBox 홈" className="inline-flex">
      {content}
    </Link>
  ) : (
    content
  );
}
```

- [ ] **Step 2: Create the favicon**

Create `frontend/src/app/icon.svg` (static mark — fixed gradient id, no React):

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <defs>
    <linearGradient id="g" x1="2" y1="2" x2="30" y2="30" gradientUnits="userSpaceOnUse">
      <stop stop-color="#07b9ce"/>
      <stop offset=".5" stop-color="#3969e7"/>
      <stop offset="1" stop-color="#7d2ae7"/>
    </linearGradient>
  </defs>
  <rect x="6" y="4.5" width="15" height="20" rx="3" fill="url(#g)" opacity=".32"/>
  <rect x="11" y="7.5" width="15" height="20" rx="3" fill="url(#g)"/>
  <path d="M15 17.5h6M19 14.5l3 3-3 3" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" opacity=".95"/>
</svg>
```

- [ ] **Step 3: Verify build**

Run: `cd frontend && npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build`
Expected: lint shows only the 3 known pre-existing errors; build succeeds (new component is unused so far — fine).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Logo.tsx frontend/src/app/icon.svg
git commit -m "feat(web): StunningBox <Logo/> component + favicon mark"
```

---

## Task 2: Move the upload tool to `/app`

**Files:**
- Create (move): `frontend/src/app/app/page.tsx`
- Delete: `frontend/src/app/page.tsx` (recreated as landing in Task 3)

- [ ] **Step 1: Move the file**

```bash
cd frontend
git mv src/app/page.tsx src/app/app/page.tsx
```

(`app/app/page.tsx` ⇒ route `/app`. All `@/...` imports are path-absolute and unaffected by the move.)

- [ ] **Step 2: Add the Logo import**

In `frontend/src/app/app/page.tsx`, add to the imports (just after the existing `ThemeToggle` import):

```tsx
import { Logo } from "@/components/Logo";
```

- [ ] **Step 3: Swap the masthead heading for the Logo**

In `frontend/src/app/app/page.tsx`, replace:

```tsx
            <h1 className="text-2xl font-bold leading-tight sm:text-[1.75rem]">
              PDF 구조 보존 번역기
            </h1>
            <p className="mt-1.5 text-base text-text-2">
              레이아웃은 그대로 두고 본문만 번역합니다
            </p>
```

with:

```tsx
            <Logo href="/" size={30} />
            <p className="mt-2 text-base text-text-2">
              레이아웃은 그대로 두고 본문만 번역합니다
            </p>
```

- [ ] **Step 4: Verify build** (note: `/` will 404 until Task 3 — expected)

Run: `cd frontend && npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build`
Expected: build succeeds; routes show `/app`, `/p/[id]`, `/c/[id]` (no `/` yet).

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src/app
git commit -m "feat(web): move upload tool to /app with Logo masthead"
```

---

## Task 3: Marketing landing at `/`

**Files:**
- Create: `frontend/src/app/page.tsx`

- [ ] **Step 1: Create the landing page**

Create `frontend/src/app/page.tsx` with the full content below:

```tsx
"use client";

import Link from "next/link";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { LANGS } from "@/lib/api";

const STEPS = [
  { n: "1", title: "문서 업로드", body: "PDF · Word · HWP 파일을 끌어다 놓으면 끝." },
  { n: "2", title: "AI 구조 분석 + 번역", body: "제목·표·그림·번호를 식별하고 본문만 번역합니다." },
  { n: "3", title: "레이아웃 그대로 내보내기", body: "원본과 똑같은 구조의 DOCX로 다운로드." },
];

const FEATURES = [
  { title: "구조·레이아웃 보존", body: "원본의 페이지 구성을 그대로 유지합니다." },
  { title: "표·그림·번호 유지", body: "표 구조와 그림, 번호 매김이 흐트러지지 않아요." },
  { title: "용어집 관리", body: "업로드한 용어집을 문서 전체에 일관되게 적용." },
  { title: "실시간 편집", body: "브라우저에서 바로 수정하고 내보낼 수 있어요." },
];

function Nav() {
  return (
    <nav className="sticky top-0 z-20 border-b border-border bg-surface/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3 sm:px-8">
        <Logo size={28} />
        <div className="hidden items-center gap-6 md:flex">
          <a href="#how" className="label transition-colors hover:text-text">작동 방식</a>
          <a href="#features" className="label transition-colors hover:text-text">기능</a>
          <a href="#formats" className="label transition-colors hover:text-text">지원 형식</a>
        </div>
        <div className="flex items-center gap-2.5">
          <Link href="/login" className="btn-ghost">로그인</Link>
          <Link href="/login" className="btn-primary">무료로 시작</Link>
          <ThemeToggle />
        </div>
      </div>
    </nav>
  );
}

function DocCard({ label, sample, accent }: { label: string; sample: string; accent?: boolean }) {
  return (
    <div className="ticket w-32 p-4 text-left sm:w-40">
      <span className="slug">{label}</span>
      <div className="mt-2 space-y-1.5">
        <div className="h-2 rounded-full" style={{ width: "85%", background: accent ? "var(--gradient-brand)" : "var(--surface-sunken)" }} />
        <div className="h-2 rounded-full bg-surface-sunken" style={{ width: "70%" }} />
        <div className="h-2 rounded-full bg-surface-sunken" style={{ width: "78%" }} />
      </div>
      <span className="slug mt-3 block">{sample}</span>
    </div>
  );
}

function Hero() {
  return (
    <header className="relative px-6 pt-20 pb-16 text-center sm:px-8">
      <span className="eyebrow reveal reveal-1">AI 문서 번역</span>
      <h1 className="reveal reveal-2 mx-auto mt-5 max-w-3xl font-bold" style={{ fontSize: "var(--fs-hero)", lineHeight: 1.12 }}>
        레이아웃은 그대로, 번역은 <span className="text-gradient">완벽하게</span>
      </h1>
      <p className="reveal reveal-2 mx-auto mt-4 max-w-xl text-text-2" style={{ fontSize: "var(--fs-base)" }}>
        PDF·Word·HWP를 올리면 구조·표·그림·번호는 그대로 두고, 본문만 번역해 DOCX로 내보냅니다.
      </p>
      <div className="reveal reveal-3 mt-8 flex flex-wrap items-center justify-center gap-3">
        <Link href="/login" className="btn-primary">무료로 시작하기 →</Link>
        <a href="#how" className="btn-secondary">작동 방식 보기</a>
      </div>
      <div className="reveal reveal-3 mx-auto mt-14 flex max-w-3xl items-center justify-center gap-4 sm:gap-6">
        <DocCard label="원본" sample="가 나 다" />
        <Logo size={44} wordmark={false} />
        <DocCard label="번역본" sample="A B C" accent />
      </div>
    </header>
  );
}

function HowItWorks() {
  return (
    <section id="how" className="border-t border-border px-6 py-20 sm:px-8">
      <div className="mx-auto max-w-5xl">
        <h2 className="text-center font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.15 }}>이렇게 동작해요</h2>
        <p className="mt-3 text-center text-text-2" style={{ fontSize: "var(--fs-base)" }}>세 단계면 충분합니다.</p>
        <div className="mt-12 grid gap-6 sm:grid-cols-3">
          {STEPS.map((s) => (
            <div key={s.n} className="ticket p-6">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-full font-bold" style={{ background: "var(--gradient-brand)", color: "var(--text-on-accent)" }}>{s.n}</span>
              <h3 className="mt-4 text-lg font-semibold">{s.title}</h3>
              <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>{s.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section id="features" className="border-t border-border px-6 py-20 sm:px-8">
      <div className="mx-auto max-w-5xl">
        <h2 className="text-center font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.15 }}>레이아웃을 지키는 번역</h2>
        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((f) => (
            <div key={f.title} className="ticket p-6">
              <span className="block h-9 w-9 rounded-lg" style={{ background: "var(--gradient-brand)" }} />
              <h3 className="mt-4 font-semibold">{f.title}</h3>
              <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>{f.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Formats() {
  const langs = Object.values(LANGS);
  return (
    <section id="formats" className="border-t border-border px-6 py-20 text-center sm:px-8">
      <div className="mx-auto max-w-3xl">
        <h2 className="font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.15 }}>지원 형식 & 언어</h2>
        <div className="mt-8 flex flex-wrap justify-center gap-2.5">
          {["PDF", "Word (DOCX)", "HWP"].map((f) => (
            <span key={f} className="rounded-full border border-border-strong px-4 py-2 font-semibold" style={{ fontSize: "var(--fs-body)" }}>{f}</span>
          ))}
        </div>
        <p className="slug mt-8">번역 언어 (양방향)</p>
        <div className="mt-3 flex flex-wrap justify-center gap-2">
          {langs.map((l) => (
            <span key={l} className="rounded-full bg-surface-2 px-3 py-1 text-text-2" style={{ fontSize: "var(--fs-body)" }}>{l}</span>
          ))}
        </div>
      </div>
    </section>
  );
}

function FinalCTA() {
  return (
    <section className="px-6 py-20 sm:px-8">
      <div className="mx-auto max-w-4xl rounded-[var(--r-xl)] p-12 text-center" style={{ background: "var(--gradient-brand)" }}>
        <h2 className="font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.15, color: "var(--text-on-accent)" }}>지금 바로 번역을 시작하세요</h2>
        <p className="mx-auto mt-3 max-w-md" style={{ fontSize: "var(--fs-base)", color: "rgba(255,255,255,0.92)" }}>몇 초면 시작할 수 있어요.</p>
        <Link href="/login" className="mt-7 inline-flex rounded-full bg-white px-6 py-3 font-semibold shadow-sm transition-transform hover:-translate-y-0.5" style={{ color: "var(--primary)" }}>무료로 시작</Link>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-border px-6 py-10 sm:px-8">
      <div className="mx-auto flex max-w-6xl flex-col items-center gap-3 text-center sm:flex-row sm:justify-between sm:text-left">
        <Logo size={24} />
        <p className="slug">레이아웃을 지키는 AI 문서 번역, 스터닝박스</p>
        <p className="slug">© 2026 StunningBox</p>
      </div>
    </footer>
  );
}

export default function LandingPage() {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <Nav />
      <main className="flex-1">
        <Hero />
        <HowItWorks />
        <Features />
        <Formats />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build`
Expected: lint = only the 3 known pre-existing errors; build succeeds; routes now include `/` (Static).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/page.tsx
git commit -m "feat(web): StunningBox marketing landing at /"
```

---

## Task 4: UI-only login at `/login`

**Files:**
- Create: `frontend/src/app/login/page.tsx`

- [ ] **Step 1: Create the login page**

Create `frontend/src/app/login/page.tsx`:

```tsx
"use client";

import type { FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";

// UI only — there is no real auth yet. Login is the funnel's entry gate: any submit
// proceeds to the tool. Real authentication is a future, separate project.
export default function LoginPage() {
  const router = useRouter();

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    router.push("/app");
  }

  return (
    <div className="relative flex min-h-0 flex-1">
      <div className="absolute right-4 top-4 z-10">
        <ThemeToggle />
      </div>

      {/* brand panel (lg+) */}
      <div className="relative hidden flex-col justify-between p-12 lg:flex lg:w-1/2" style={{ background: "var(--gradient-brand)" }}>
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

      {/* form */}
      <div className="flex flex-1 items-center justify-center overflow-y-auto p-6 lg:w-1/2">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex justify-center lg:hidden">
            <Logo size={30} href="/" />
          </div>
          <h1 className="text-2xl font-bold leading-tight">다시 오신 걸 환영해요</h1>
          <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>스터닝박스에 로그인하세요.</p>

          <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="label">이메일</span>
              <input type="email" className="field" placeholder="you@example.com" autoComplete="email" />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="label">비밀번호</span>
              <input type="password" className="field" placeholder="••••••••" autoComplete="current-password" />
            </label>
            <button type="submit" className="btn-primary mt-1 w-full">로그인</button>
          </form>

          <div className="my-5 flex items-center gap-3">
            <span className="h-px flex-1 bg-border" />
            <span className="slug">또는</span>
            <span className="h-px flex-1 bg-border" />
          </div>

          <button type="button" disabled className="btn-secondary w-full" title="준비 중">
            Google로 계속
          </button>

          <p className="mt-6 text-center text-text-2" style={{ fontSize: "var(--fs-body)" }}>
            계정이 없으신가요?{" "}
            <Link href="/app" className="font-semibold" style={{ color: "var(--primary-hover)" }}>가입</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build`
Expected: lint = only the 3 known pre-existing errors; build succeeds; routes include `/login`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/login/page.tsx
git commit -m "feat(web): UI-only login page at /login (funnel to /app)"
```

---

## Task 5: Rebrand mastheads, metadata, and link funnel

**Files:**
- Modify: `frontend/src/app/layout.tsx`
- Modify: `frontend/src/app/p/[id]/page.tsx`
- Modify: `frontend/src/app/c/[id]/page.tsx`

- [ ] **Step 1: Update metadata** in `frontend/src/app/layout.tsx`

Replace:

```tsx
export const metadata: Metadata = {
  title: "PDF 구조 보존 번역기",
  description: "레이아웃은 그대로 두고 본문만 번역해 docx로 내보냅니다.",
};
```

with:

```tsx
export const metadata: Metadata = {
  title: "StunningBox · 레이아웃 보존 문서 번역",
  description: "PDF·Word·HWP의 구조를 그대로 유지한 채 본문만 번역해 DOCX로 내보내는 AI 문서 번역, 스터닝박스.",
};
```

- [ ] **Step 2: Rebrand the processing masthead** in `frontend/src/app/p/[id]/page.tsx`

Add the import after the `ThemeToggle` import:

```tsx
import { Logo } from "@/components/Logo";
```

Replace:

```tsx
            <h1 className="text-2xl font-bold leading-tight sm:text-[1.75rem]">
              PDF 구조 보존 번역기
            </h1>
            <span className="eyebrow">2단계 / 3 · 변환 중</span>
```

with:

```tsx
            <Logo href="/" size={30} />
            <span className="eyebrow">2단계 / 3 · 변환 중</span>
```

Then replace the error "처음으로" link:

```tsx
              <Link href="/" className="btn-secondary mt-6 inline-flex">
                처음으로
              </Link>
```

with:

```tsx
              <Link href="/app" className="btn-secondary mt-6 inline-flex">
                처음으로
              </Link>
```

- [ ] **Step 3: Rebrand the viewer masthead** in `frontend/src/app/c/[id]/page.tsx`

Add the import after the `ThemeToggle` import:

```tsx
import { Logo } from "@/components/Logo";
```

Replace:

```tsx
            <Link href="/" className="text-2xl font-bold leading-tight sm:text-[1.625rem]">
              PDF 구조 보존 번역기
            </Link>
```

with:

```tsx
            <Logo href="/" size={30} />
```

- [ ] **Step 4: Funnel the viewer links to `/app`** in `frontend/src/app/c/[id]/page.tsx`

Replace the error "처음으로" link:

```tsx
          <Link href="/" className="btn-secondary mt-6 inline-flex">
            처음으로
          </Link>
```

with:

```tsx
          <Link href="/app" className="btn-secondary mt-6 inline-flex">
            처음으로
          </Link>
```

Then replace the "새 문서" link:

```tsx
          <Link href="/" className="btn-secondary">
            새 문서
          </Link>
```

with:

```tsx
          <Link href="/app" className="btn-secondary">
            새 문서
          </Link>
```

- [ ] **Step 5: Verify build + confirm the name is gone**

Run: `cd frontend && npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build`
Expected: lint = only the 3 known pre-existing errors; build succeeds.
Run a grep to confirm zero remaining occurrences: search `PDF 구조 보존 번역기` under `frontend/src` → expect **0 matches**.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/layout.tsx "frontend/src/app/p/[id]/page.tsx" "frontend/src/app/c/[id]/page.tsx"
git commit -m "feat(web): rebrand mastheads/metadata to StunningBox + funnel links to /app"
```

---

## Task 6: Full visual verification (light + dark + responsive)

**Files:** none (verification only).

- [ ] **Step 1: Serve the built app**

Run (background): `cd frontend && BACKEND_URL=http://127.0.0.1:8001 npx next start -p 3015`
Confirm "Ready" on `http://localhost:3015`. (Backend should be up on :8001 for `/app` to fetch health/upload; the landing + login render without it.)

- [ ] **Step 2: Walk the funnel in the browser (chrome-devtools), light theme**

At 1440px width, verify:
- `/` — sticky nav (Logo + anchors + 로그인/무료로 시작 + ThemeToggle); hero gradient keyword "완벽하게"; box motif (원본 → mark → 번역본); 작동 방식 3 cards; 기능 4 cards; 지원 형식 badges + language pills; gradient 마감 CTA (white text legible); footer. Nav anchors smooth-scroll to `#how/#features/#formats`.
- `무료로 시작` / `무료로 시작하기` / 마감 CTA → `/login`; `로그인` → `/login`.
- `/login` — split brand panel (white Logo on gradient, readable) + form; `로그인` submit → `/app`; `가입` → `/app`; `Google로 계속` disabled.
- `/app` — upload tool with Logo masthead, unchanged behavior; favicon (tab icon) is the box mark.

- [ ] **Step 3: Dark theme + responsive**

Toggle dark; re-check `/`, `/login`, `/app` (tokens flip; gradient panels keep white text; Logo wordmark "Box" gradient readable). Resize to ~480px: nav anchors hide, hero/cards reflow to single column, login collapses to form-only (brand panel hidden, top Logo shows).

- [ ] **Step 4: Record results**

Note any visual defects; fix inline (small CSS/class tweaks) and re-verify. No commit needed unless a fix was made (then commit `fix(web): …`).

---

## Self-Review (against the spec)

**Spec coverage:**
- §3 brand / §4 `<Logo/>` → Task 1. ✓
- §5 landing (all 7 sections) → Task 3. ✓
- §6 login (split, UI-only, submit→/app) → Task 4. ✓
- §7 routing migration (tool→/app, link updates) → Tasks 2 & 5. ✓
- §8 rebrand (metadata, 3 mastheads, favicon, 스터닝박스 copy) → Tasks 1 (favicon), 2 (/app masthead), 5 (metadata + 2 mastheads + grep check). ✓
- §2 funnel (/ → /login → /app) → landing CTAs → /login (Task 3), login submit → /app (Task 4). ✓
- §11 verification (light/dark/responsive) → Task 6. ✓

**Placeholder scan:** No TBD/TODO. Feature "icon" is a real gradient chip (intentional design element, not a stand-in). No fabricated stats/testimonials (per decision). The logo is one concrete SVG (the "render 2-3 variants" was a brainstorming nicety; one strong mark is specified and can be visually iterated in Task 6 Step 4).

**Type/name consistency:** `<Logo/>` props (`size/wordmark/href/tone/className`) used consistently — `tone="onAccent"` only on the login brand panel; `href="/"` on mastheads/nav-less contexts; `wordmark={false}` only in the hero motif. Route strings consistent: CTAs→`/login`, login submit + 가입 + 새 문서 + 처음으로→`/app`, masthead Logo→`/`. `LANGS` imported from `@/lib/api` (existing export). `var(--text-on-accent)` used for text on gradient (token exists in `globals.css`).

---

## Execution Handoff

Implement with **superpowers:subagent-driven-development** (fresh subagent per task + two-stage review) or **superpowers:executing-plans** (inline, batched).
