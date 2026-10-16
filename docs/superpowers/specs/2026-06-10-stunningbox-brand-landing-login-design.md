# StunningBox — Brand Identity + Landing + Login Design

**Date:** 2026-06-10
**Scope:** Frontend-only. Establish the **StunningBox (스터닝박스)** brand, add a marketing **landing page** (`/`) and a **login page UI** (`/login`), move the existing upload tool to `/app`, and roll the brand across the app (replacing "PDF 구조 보존 번역기"). No backend changes.

---

## 1. Context (current state — verified)

- App = layout-preserving **PDF/HWP/DOCX → translated-DOCX** tool.
- **No user auth exists.** The only repo "auth" is OnlyOffice document-server JWT (callback signing). Greenfield login.
- Routes today: `/` = upload tool (step 1) · `/p/[id]` = processing · `/c/[id]` = viewer.
- Design system (already built, just type-scale-refreshed) in `frontend/src/app/globals.css`: indigo `--primary #5b5bf0`, cyan→purple `--gradient-brand`, neutral surfaces, soft shadows, pills, light+dark, ambient brand glow, type scale tokens (`--fs-caption/-label/-body/-base/-lead/-h2/-h1/-hero`), classes `.eyebrow .label .slug .text-gradient .btn-primary .btn-secondary .btn-ghost .field .ticket .stamp`. Fonts: Inter (Latin) + Pretendard (KO).
- The name "PDF 구조 보존 번역기" appears in **4 places**: `layout.tsx:21` (metadata title), and the masthead on `page.tsx`, `p/[id]/page.tsx`, `c/[id]/page.tsx`.

## 2. Decisions (agreed)

- **Login:** UI-only now (no real auth backend). **All landing CTAs (무료로 시작 / 무료로 시작하기) funnel to `/login` first** — login is the entry gate. The login form submit (and 가입) → `/app`. Real auth = future separate project.
- **Routing:** Landing at `/` (Canva-style). Move the upload tool to `/app`. Add `/login`.
- **Landing:** full marketing page.
- **Logo mark:** **layered box (transform)** — a rounded-square box containing two offset document layers (원본 → 번역본), brand-gradient filled.

## 3. Brand identity

- **Name:** StunningBox · Korean **스터닝박스**. Story: *drop a document in the box → it comes back stunning, layout untouched.*
- **Wordmark:** "Stunning" in `--text` + **"Box" in `.text-gradient`** (cyan→purple), heavy weight (Inter/Pretendard 700–800), tracking-tight on the Latin wordmark only.
- **Logo mark (layered box):** rounded square (`--r-md`-ish corners), two offset rounded rectangles inside suggesting a back sheet (원본) and a front sheet (번역본) with a small transform/→ cue; filled with `--gradient-brand`. Must read at 20px (favicon) in a single-color fallback. Final SVG: render **2–3 variants at build, confirm with user** before wiring everywhere.
- **Palette / type / voice:** unchanged — formalize the existing tokens as the brand. Calm, friendly Korean voice (proofreader metaphor already dropped).
- **Tagline:** **"레이아웃은 그대로, 번역은 완벽하게"** (primary). Hero gradient keyword on "완벽하게".

## 4. `<Logo/>` component — `frontend/src/components/Logo.tsx`

A shared, theme-aware brand lockup. Props:
- `size?: number` (mark height in px; default 28)
- `wordmark?: boolean` (default `true`) — `false` renders mark only (favicon/compact).
- `href?: string` — when set, wraps in a Next `<Link>` (e.g. masthead → `/`); when unset, renders plain (e.g. inside the landing nav which already links).
- `className?: string`.

Render: inline SVG mark (gradient via an SVG `<linearGradient>` referencing the brand stops, with `currentColor` fallback for forced-colors) + wordmark `Stunning<span className="text-gradient">Box</span>`. The wordmark uses the sans stack, weight 700.

## 5. Landing page (`/`) — `frontend/src/app/page.tsx` (new)

Full-height scrollable marketing page (note: `layout.tsx` body is `h-dvh overflow-hidden flex flex-col`; the landing's root must allow internal scroll — use a scroll container `flex-1 overflow-y-auto`). Sections top→bottom:

1. **Sticky nav** (`sticky top-0`, glass/`--surface` blur, bottom border): `<Logo/>` left · center anchors `기능 · 작동 방식 · 지원 형식` (smooth-scroll to `#features`/`#how`/`#formats`; hidden on mobile) · right `로그인` (`.btn-ghost` → `/login`) + `무료로 시작` (`.btn-primary` → `/login`) + `<ThemeToggle/>`.
2. **Hero** (centered, over ambient glow): `.eyebrow` "AI 문서 번역" · H1 `var(--fs-hero)` **"레이아웃은 그대로, 번역은 `완벽하게`"** (gradient on 완벽하게) · sub `var(--fs-base)` "PDF·Word·HWP를 올리면 구조·표·그림·번호는 그대로, 본문만 번역해 DOCX로 내보냅니다." · CTAs `무료로 시작하기 →` (`.btn-primary` → `/login`) + `작동 방식 보기` (`.btn-secondary`, scroll `#how`). Visual: a **box motif card** — 원본 문서 → `<Logo mark>` → 번역본 문서 (simple three-up with a → between; reuse `.sheet`/`.ticket`). Staggered `.reveal` entrance.
3. **작동 방식** (`#how`) — heading + 3 numbered step cards: ① **문서 업로드** "PDF·Word·HWP 파일을 올립니다" · ② **AI 구조 분석 + 번역** "제목·표·그림·번호를 식별하고 본문만 번역" · ③ **레이아웃 그대로 내보내기** "원본과 똑같은 구조의 DOCX로 다운로드". Each: gradient number/icon, title (`--fs-h2`-ish), 1-line.
4. **기능 카드 ×4** (`#features`) — grid (1/2/4 cols responsive), each `.ticket` with icon + title + 1-line: **구조·레이아웃 보존** / **표·그림·번호 유지** / **용어집 관리** (업로드한 용어집을 일관 적용) / **실시간 편집** (브라우저에서 바로 수정·내보내기).
5. **지원 형식 + 언어** (`#formats`) — format badges **PDF · Word(DOCX) · HWP**; language pairs from `LANGS` (한국어·English·日本語·中文·Español·Français…), shown as pills, "양방향 번역" note.
6. **마감 CTA** — full-width `--gradient-brand` panel, white text "지금 바로 번역을 시작하세요" + `무료로 시작` (`.btn-secondary` on the gradient, or a white pill) → `/login`.
7. **Footer** — `<Logo/>` (mono/small) · 간단 링크(기능/작동 방식/로그인) · "© 2026 StunningBox" · 한 줄 "레이아웃을 지키는 AI 문서 번역, 스터닝박스".

Copy is calm/friendly Korean. No fabricated stats/testimonials (YAGNI; we have no real numbers).

## 6. Login page (`/login`) — `frontend/src/app/login/page.tsx` (new, `"use client"`)

**Split layout** (lg+), stacks on mobile:
- **Left brand panel** (`lg:w-1/2`): `--gradient-brand` (or tinted glass over glow), white `<Logo/>` (large), value line "레이아웃은 그대로, 번역은 완벽하게", a subtle box motif. Hidden/condensed to a top strip on mobile.
- **Right form card** (centered, `max-w-sm`): heading "다시 오신 걸 환영해요" · `.field` 이메일 + 비밀번호 · `로그인` (`.btn-primary`, full-width) · "또는" divider · `Google로 계속` (`.btn-secondary` full-width, `disabled` + title "준비 중") · "계정이 없으신가요? **가입**" link · `<ThemeToggle/>` (top-right corner of the page).
- **Behavior:** form `onSubmit` → `preventDefault` → `router.push("/app")` (no validation/auth yet — login is the entry gate, any input proceeds). "가입" → `/app`. (No "skip login" bypass note — the funnel routes everyone through this page.)

## 7. Routing migration

- **Move:** current `frontend/src/app/page.tsx` (the `UploadPage`) → `frontend/src/app/app/page.tsx` (route `/app`), unchanged except it gets the `<Logo/>` masthead (§8). Its `router.push('/p/[id]…')` stays.
- **New:** `frontend/src/app/page.tsx` = Landing (§5); `frontend/src/app/login/page.tsx` = Login (§6).
- **Link updates:**
  - Landing CTAs (무료로 시작 · 무료로 시작하기 · 마감 CTA) and nav 로그인 → **`/login`**; login submit + 가입 → **`/app`** (the funnel: `/` → `/login` → `/app`).
  - `p/[id]/page.tsx`: masthead `<Logo href="/">`; error "처음으로" `Link href="/"` → **`/app`**.
  - `c/[id]/page.tsx`: masthead `<Logo href="/">`; error "처음으로" → **`/app`**; **"새 문서" `Link href="/"` → `/app`**.
  - `/p/[id]`, `/c/[id]` route paths unchanged.

## 8. Rebrand sweep

- `layout.tsx` metadata: `title: "StunningBox · 레이아웃 보존 문서 번역"`, description updated to the StunningBox value line.
- Replace the 3 masthead text usages of "PDF 구조 보존 번역기" with `<Logo href="/">` + keep the descriptive `.label`/eyebrow tagline beneath (home/`/app`, processing, viewer).
- **Favicon:** add `frontend/src/app/icon.svg` = the layered-box mark (App Router auto-registers it).
- Korean plain-text app-name references use **스터닝박스** (footer, copy).

## 9. File structure

- **Create:** `components/Logo.tsx`, `app/page.tsx` (landing), `app/login/page.tsx`, `app/app/page.tsx` (moved tool), `app/icon.svg`.
- **Modify:** `app/layout.tsx` (metadata), `app/p/[id]/page.tsx` + `app/c/[id]/page.tsx` (Logo + links), `app/globals.css` (only if a landing-specific helper/animation is needed — prefer existing tokens/classes).
- Landing sections live inline in `app/page.tsx` as local components (Nav/Hero/How/Features/Formats/CTA/Footer) for one-file cohesion; `<Logo/>` and `<ThemeToggle/>` are the only shared imports. If `page.tsx` grows unwieldy (>~250 lines), extract sections to `components/landing/`.

## 10. Out of scope

- Real authentication (user DB, sessions, social login, protected endpoints) — future project.
- The OnlyOffice editor iframe and backend-served preview iframes (stay as-is).
- Any backend change. New pages are static/client; no new API.

## 11. Verification

- `cd frontend && npm run lint && BACKEND_URL=http://127.0.0.1:8001 npm run build` — pass, no new errors.
- Serve (`next start`) + chrome-devtools walk in **light AND dark**: landing (`/`) all sections + sticky nav + CTAs; `/login` split + mobile stack + submit→`/app`; `/app` tool works as before with new Logo masthead; `/p/[id]`,`/c/[id]` Logo + "새 문서"→`/app`; favicon renders. Confirm nav anchors smooth-scroll; reduced-motion respected; focus rings visible; AA contrast on gradient panels (white text on `--gradient-brand`).
- Responsive: nav collapses, hero/cards reflow, login stacks at < lg.

## 12. Branch

New frontend effort off `main`: `stunningbox-brand-2026.06.10`.
