# Viewer Space-Efficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Step-3 번역 뷰어(`/c/[id]`)의 공간 효율을 높인다 — 헤더 1줄 통합, 원본│번역 비율 조절 + 원본 접기, 재번역/용어를 하나의 탭형 도구 독(폭 조절)으로, OnlyOffice 기본 줌 fit-to-width.

**Architecture:** 프론트 중심. 새 순수 헬퍼 모듈(`viewerLayout.ts`)이 비율/독폭/접힘 상태와 localStorage·CSS grid-template-columns를 담당하고, 새 `ResizeHandle` 컴포넌트가 포인터 드래그를 px 델타로 부모에 보고한다. `c/[id]/page.tsx`가 이 둘로 스프레드를 grid로 재구성하고, 머리글·액션바를 단일 상단 바로 합치며, 우측을 탭형 도구 독으로 통합한다. 백엔드는 OnlyOffice 줌 한 줄만 바뀐다.

**Tech Stack:** Next.js 16.2.6 (App Router) · React 19 · Tailwind v4(CSS-config 토큰, `globals.css`) · FastAPI(backend) · OnlyOffice DS 9.4.0 Community.

---

## 사전 메모 (읽고 시작)

- **브랜치:** `viewer-layout-efficiency-2026-06-15` (이미 생성됨, spec 커밋 `ee09a66` 포함).
- **로컬 전용 미커밋 파일 — 절대 커밋 금지:** `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts` (DEV_NO_AUTH 우회). 각 태스크의 `git add`는 **해당 파일 경로만** 지정한다(`git add -A` 금지).
- **프론트엔드 단위 테스트 러너 없음**(`package.json` scripts = `dev/build/start/lint`). 프론트 검증 게이트는 **`npm run lint && npm run build`**(타입·미사용 토큰·JSX 오류를 build가 잡음) + **수동 브라우저 확인**이다. Vitest 등 테스트 프레임워크는 **추가하지 않는다(범위 외, YAGNI)**. 백엔드는 pytest가 있으므로 Task 1만 TDD.
- **로컬 구동(수동 확인용):** 백엔드 :8001(이미 기동 중, DEV_NO_AUTH), 프론트 :3001(DEV_NO_AUTH, `BACKEND_URL=http://127.0.0.1:8001`로 빌드됨), OnlyOffice DS :8080. 테스트 문서: `a2i8TOoVVNPHRs7caCqinybs` (`lang=en`). 뷰어 URL: `http://127.0.0.1:3001/c/a2i8TOoVVNPHRs7caCqinybs?lang=en`.
  - 프론트 코드 변경 후엔 **재빌드**가 필요: `cd frontend && BACKEND_URL=http://127.0.0.1:8001 npm run build` 후 기존 :3001 프로세스를 TaskStop하고 `DEV_NO_AUTH=1 BACKEND_URL=http://127.0.0.1:8001 npm run start -- -p 3001` 재기동. (메모리: dev HMR 깨짐 → prod build 사용.)
- **OnlyOffice 줌 검증 주의:** `zoom:-2`는 **클린 프로필 첫 로드**의 기본값만 보장한다. 줌을 한 번이라도 바꾼 프로필은 localStorage 저장값이 config를 덮어쓴다. Task 1 수동 확인은 **incognito 새 창**에서 한다(아니면 거짓 음성).

---

## File Structure

| 파일 | 역할 | 변경 |
|---|---|---|
| `backend/onlyoffice.py` | OnlyOffice 에디터 config 빌드 | `customization`에 `"zoom": -2` 추가 (1줄) |
| `backend/tests/test_onlyoffice_config.py` | 위 동작 테스트 | **신규** |
| `frontend/src/lib/viewerLayout.ts` | 비율/독폭/접힘 상태 + clamp + localStorage + grid 컬럼 문자열 (순수 함수) | **신규** |
| `frontend/src/components/ResizeHandle.tsx` | 드래그 가능한 분할 핸들(포인터 → px 델타) | **신규** |
| `frontend/src/app/globals.css` | `.resize-handle` 스타일 | 추가 (`.gutter` 옆) |
| `frontend/src/app/c/[id]/page.tsx` | 상단 바 통합 · grid 스프레드 · 리사이즈/접기 · 탭형 도구 독 | 대폭 수정 |

---

## Task 1: 백엔드 — OnlyOffice 기본 줌 fit-to-width

**Files:**
- Test: `backend/tests/test_onlyoffice_config.py` (Create)
- Modify: `backend/onlyoffice.py` (customization dict, 현재 96–107행)

- [ ] **Step 1: 실패하는 테스트 작성**

Create `backend/tests/test_onlyoffice_config.py`:

```python
"""build_editor_config sets a fit-to-width default zoom for the embedded editor."""
from pathlib import Path

import onlyoffice


def test_editor_config_sets_fit_to_width_zoom(tmp_path: Path):
    docx = tmp_path / "doc.en.docx"
    docx.write_bytes(b"PK\x03\x04 stub docx")  # a real file so document_key() can stat it
    cfg = onlyoffice.build_editor_config(
        doc_id="abc123", lang="en", title="Sample", docx_path=str(docx),
    )
    customization = cfg["editorConfig"]["customization"]
    # -2 = OnlyOffice "fit page width to editor" (a true fit mode, not a fixed %),
    # honoured by the Community 9.4.0 build for the "word" documentType.
    assert customization["zoom"] == -2
    # the existing chrome-trimming flags must remain
    assert customization["compactHeader"] is True
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `cd backend && python -m pytest tests/test_onlyoffice_config.py -v`
Expected: FAIL — `KeyError: 'zoom'` (또는 assert 실패).

- [ ] **Step 3: 최소 구현 — customization에 zoom 추가**

In `backend/onlyoffice.py`, the `customization` dict (현재 96–107행) 의 `"uiTheme": "theme-light",` 다음 줄에 추가:

```python
                "compactHeader": True,
                "hideRightMenu": True,  # collapsed by default; user can reopen
                "uiTheme": "theme-light",
                # Open the doc fit to the editor's width so the gray "desk" margins around
                # the page shrink to fill our column. -2 = fit-page-width (not a fixed %);
                # honoured by Community 9.4.0 for "word". NOTE: a user-changed zoom is saved
                # to the browser's localStorage and overrides this — so -2 is the first-load
                # DEFAULT on a clean profile, not a forced value.
                "zoom": -2,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/test_onlyoffice_config.py -v`
Expected: PASS (2 assertions).

- [ ] **Step 5: 전체 백엔드 스위트 회귀 없음 확인**

Run: `cd backend && python -m pytest -q`
Expected: 기존과 동일하게 전부 통과(메모리 기준 245 passed, 1 skipped + 새 테스트 1 → 246 passed).

- [ ] **Step 6: 수동 확인 (incognito 필수)**

백엔드 :8001 재기동(줌 변경은 서명된 config에 들어가므로 백엔드 재시작 필요). **새 incognito 창**에서 뷰어 URL을 열고, OnlyOffice 문서가 칼럼 폭에 맞춰(회색 여백 최소) 열리는지 확인. (기존 프로필은 저장된 줌이 덮어써 거짓 음성이 날 수 있음.)
- 만약 incognito에서도 fit-to-width가 **안 되면**: 검증 노트대로 이 항목(D)만 제외하고 `zoom` 줄과 테스트의 zoom assert를 되돌린다(나머지 A·B·C는 독립). 결과를 보고에 남긴다.

- [ ] **Step 7: 커밋**

```bash
git add backend/onlyoffice.py backend/tests/test_onlyoffice_config.py
git commit -m "feat(backend): default OnlyOffice editor zoom to fit-page-width"
```

---

## Task 2: 프론트 — viewerLayout 순수 헬퍼 모듈

비율·독폭·접힘 상태와 clamp/localStorage/grid 컬럼 문자열을 순수 함수로 분리. React 없음.

**Files:**
- Create: `frontend/src/lib/viewerLayout.ts`

- [ ] **Step 1: 모듈 작성**

Create `frontend/src/lib/viewerLayout.ts`:

```ts
// Pure layout helpers for the Step-3 viewer's resizable spread. The split ratio, tool-dock
// width, and original-pane collapse persist to localStorage so a reviewer's preferred
// layout survives reloads. No React here — just math + storage + the grid column string.

export const MAIN_MIN = 0.25; // original pane never narrower/wider than these fractions
export const MAIN_MAX = 0.75;
export const DOCK_MIN = 280; // tool-dock width clamp (px)
export const DOCK_MAX = 560;
const STORAGE_KEY = "sb.viewer.layout.v1";

export interface ViewerLayout {
  /** original pane's fraction of the (original + main) area, before the dock. */
  originalRatio: number;
  /** tool-dock width in px (used only when a tool tab is open). */
  dockWidth: number;
  /** when true the original pane collapses to 0 (reopen from the top bar). */
  originalCollapsed: boolean;
}

export const DEFAULT_LAYOUT: ViewerLayout = {
  originalRatio: 0.5,
  dockWidth: 380,
  originalCollapsed: false,
};

export const clamp = (v: number, lo: number, hi: number): number =>
  Math.min(hi, Math.max(lo, v));

export function loadLayout(): ViewerLayout {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_LAYOUT;
    const p = JSON.parse(raw) as Partial<ViewerLayout>;
    return {
      originalRatio: clamp(Number(p.originalRatio) || DEFAULT_LAYOUT.originalRatio, MAIN_MIN, MAIN_MAX),
      dockWidth: clamp(Number(p.dockWidth) || DEFAULT_LAYOUT.dockWidth, DOCK_MIN, DOCK_MAX),
      originalCollapsed: Boolean(p.originalCollapsed),
    };
  } catch {
    return DEFAULT_LAYOUT; // private mode / corrupt value → defaults
  }
}

export function saveLayout(layout: ViewerLayout): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    /* private mode / quota — in-memory only */
  }
}

// CSS grid-template-columns for the spread (applied only at lg+). Child order:
// original, main-handle, main, [dock-handle, dock]. Collapsed → original+handle are 0.
// minmax(0,…) lets the editor/iframe shrink instead of forcing overflow.
export function gridColumns(layout: ViewerLayout, hasDock: boolean): string {
  const left = layout.originalCollapsed
    ? `0px 0px minmax(0, 1fr)`
    : `minmax(0, ${layout.originalRatio}fr) 6px minmax(0, ${1 - layout.originalRatio}fr)`;
  return hasDock ? `${left} 6px ${layout.dockWidth}px` : left;
}
```

- [ ] **Step 2: 타입 체크 통과 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음. (이 모듈은 아직 어디서도 import되지 않으므로 build 영향 없음.)

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/lib/viewerLayout.ts
git commit -m "feat(frontend): viewer layout helpers (clamp/persist/grid columns)"
```

---

## Task 3: 프론트 — ResizeHandle 컴포넌트 + CSS

포인터 드래그를 px 델타로 부모에 보고하는 얇은 분할 핸들. lg+에서만 활성.

**Files:**
- Create: `frontend/src/components/ResizeHandle.tsx`
- Modify: `frontend/src/app/globals.css` (`.gutter` 정의(267–270행) 바로 아래에 추가)

- [ ] **Step 1: 컴포넌트 작성**

Create `frontend/src/components/ResizeHandle.tsx`:

```tsx
"use client";

import { useCallback, useRef } from "react";

// A thin draggable divider for the viewer spread. A pure pointer sensor: it reports
// horizontal drag deltas (px) via onDelta and lets the parent map them to a ratio/width.
// Pointer capture keeps the drag alive even if the cursor outruns the 6px strip.
// Keyboard: ←/→ nudge by 16px (a11y). Hidden below lg (mobile stacks, no resizing).
export function ResizeHandle({
  onDelta,
  ariaLabel,
}: {
  onDelta: (dxPx: number) => void;
  ariaLabel: string;
}) {
  const dragging = useRef(false);

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  }, []);

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (dragging.current && e.movementX) onDelta(e.movementX);
    },
    [onDelta],
  );

  const stop = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
  }, []);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "ArrowLeft") onDelta(-16);
      else if (e.key === "ArrowRight") onDelta(16);
    },
    [onDelta],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      tabIndex={0}
      className="resize-handle hidden lg:block"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      onKeyDown={onKeyDown}
    />
  );
}
```

- [ ] **Step 2: CSS 추가**

In `frontend/src/app/globals.css`, `.gutter { … }` (267–270행) 정의 **바로 아래**에 추가:

```css
/* draggable divider between viewer panes (the grid gives it a 6px column). A hairline at
   rest, thickening to the primary tint on hover/focus; full-height via the grid stretch. */
.resize-handle {
  background: var(--divider);
  cursor: col-resize;
  touch-action: none;
  transition: background var(--dur-2) var(--ease);
}
.resize-handle:hover,
.resize-handle:focus-visible {
  background: var(--primary);
  outline: none;
}
@media (prefers-reduced-motion: reduce) {
  .resize-handle {
    transition: none;
  }
}
```

- [ ] **Step 3: 타입/빌드 확인**

Run: `cd frontend && npm run lint && npm run build`
Expected: 통과. (컴포넌트는 아직 미사용 — export만 존재하므로 빌드 영향 없음.)

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/components/ResizeHandle.tsx frontend/src/app/globals.css
git commit -m "feat(frontend): draggable ResizeHandle + .resize-handle styles"
```

---

## Task 4: 프론트 — 머리글 + 액션바 → 단일 상단 바

`c/[id]/page.tsx`의 `<header>`(116–141행)와 액션바 `<div>`(143–173행)를 **하나의 상단 바**로 합친다. 스프레드는 이 태스크에서 건드리지 않는다(여전히 `mode`로 동작 → 컴파일·기능 유지).

**Files:**
- Modify: `frontend/src/app/c/[id]/page.tsx` (116–173행 전체 교체)

- [ ] **Step 1: 머리글+액션바 두 블록을 단일 `<header>`로 교체**

`c/[id]/page.tsx`에서 아래 범위를 교체한다. **삭제 대상**: 현재 `{/* masthead */}` 주석부터 액션바 `<div>`의 닫는 `</div>`까지 (116–173행, 즉 `<header …>…</header>` 와 그 다음 `<div className="reveal reveal-2 …">…</div>` 두 블록 모두).

**교체 후 (단일 상단 바):**

```tsx
      {/* top bar — identity + status (left) · tools + actions (right), one row that
          wraps on narrow screens. Replaces the old masthead + action-bar stack. */}
      <header className="reveal reveal-1 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border px-4 py-2.5 sm:px-6">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
          <Logo href="/" size={22} />
          <span className="eyebrow">검토·편집</span>
          <span className="slug max-w-[14rem] truncate">
            {doc.title ? `${doc.title} · ` : ""}
            {doc.doc_id}
          </span>
          <span className="label">
            {LANGS[doc.source_lang] ?? doc.source_lang} → {LANGS[lang] ?? lang}
          </span>
          {gate && (
            <>
              <span className={`stamp${gateOk ? "" : " stamp--alert"}`}>
                <span className="stamp__dot" />
                {gateOk ? "누락 0" : `누락 ${missing}`}
              </span>
              <span className="slug">반영률 {(gate.coverage * 100).toFixed(0)}%</span>
            </>
          )}
          {layout.originalCollapsed && (
            <button onClick={toggleOriginal} className="slug hover:text-primary" title="원본 펼치기">
              ▶ 원본
            </button>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            className={mode === "retranslate" ? "btn-primary" : "btn-secondary"}
            onClick={() => setMode((m) => (m === "retranslate" ? "edit" : "retranslate"))}
          >
            재번역
          </button>
          <button
            className={mode === "manage" ? "btn-primary" : "btn-secondary"}
            onClick={() => setMode((m) => (m === "manage" ? "edit" : "manage"))}
          >
            용어
          </button>
          <span className="slug hidden lg:inline">자동 저장</span>
          <ThemeToggle />
          <AuthMenu />
          <Link href="/app" className="btn-secondary">
            새 문서
          </Link>
          <a href={api.downloadUrl(doc.doc_id, lang)} className="btn-primary">
            DOCX 내보내기
          </a>
        </div>
      </header>
```

> 참고: 이 JSX는 `layout.originalCollapsed`와 `toggleOriginal`을 참조한다 — 둘 다 **Task 5에서 추가**된다. Task 4를 독립 커밋하려면, 이 `{layout.originalCollapsed && …}` 블록만 Task 5까지 잠시 빼두고(아래 Step 2 참고), Task 5에서 다시 넣는다. **권장: Task 4와 Task 5를 연속 실행**하고 그 사이엔 빌드만 확인. subagent-driven으로 분리 실행할 경우 Step 2의 임시본을 사용.

- [ ] **Step 2: (분리 실행 시에만) 임시로 collapse 참조 제거**

Task 5를 아직 안 했다면, 위 블록에서 다음 6줄을 **임시 삭제**한다(Task 5 Step에서 복원):

```tsx
          {layout.originalCollapsed && (
            <button onClick={toggleOriginal} className="slug hover:text-primary" title="원본 펼치기">
              ▶ 원본
            </button>
          )}
```

- [ ] **Step 3: 빌드 확인**

Run: `cd frontend && npm run lint && npm run build`
Expected: 통과. (`reveal-2`가 사라져도 무방 — 클래스 미사용일 뿐. `gate`,`gateOk`,`missing`,`LANGS`,`api`,`Logo`,`ThemeToggle`,`AuthMenu` 모두 기존 import 유지.)

- [ ] **Step 4: 수동 확인**

재빌드·재기동 후 뷰어 URL 열기. 확인: 상단이 **한 줄**(넓은 화면)로 로고·검토·편집·제목·언어쌍·누락0·반영률 + 재번역/용어/테마/계정/새문서/DOCX내보내기. 창을 좁히면 자연스럽게 2줄로 줄바꿈. 재번역/용어 버튼이 기존처럼 우측 드로어를 토글하는지(스프레드는 아직 옛 구조).

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/app/c/[id]/page.tsx
git commit -m "feat(frontend): merge viewer masthead + action bar into one top bar"
```

---

## Task 5: 프론트 — grid 스프레드(리사이즈·접기) + 탭형 도구 독

스프레드 `<section>`(176–295행)을 grid로 재구성하고, 리사이즈 핸들/접기/도구 독 탭을 통합한다. 상태·핸들러·import도 이 태스크에서 추가한다.

**Files:**
- Modify: `frontend/src/app/c/[id]/page.tsx` (imports, 상태/핸들러, `<section>` 전체)

- [ ] **Step 1: import 추가**

상단 import 영역을 다음과 같이 보강한다.

react import 교체:
```tsx
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
```

컴포넌트 import 묶음에 추가:
```tsx
import { ResizeHandle } from "@/components/ResizeHandle";
import {
  DEFAULT_LAYOUT,
  loadLayout,
  saveLayout,
  gridColumns,
  clamp,
  MAIN_MIN,
  MAIN_MAX,
  DOCK_MIN,
  DOCK_MAX,
  type ViewerLayout,
} from "@/lib/viewerLayout";
```

- [ ] **Step 2: 상태·ref·핸들러 추가**

`const [glossaryReport, setGlossaryReport] = useState<GlossaryTermReport[]>([]);` (40행) **다음**에 추가:

```tsx
  // Resizable spread: split ratio + dock width + original collapse, persisted to
  // localStorage. SSR renders DEFAULT_LAYOUT (deterministic); the stored value is applied
  // after mount to avoid a hydration mismatch.
  const [layout, setLayout] = useState<ViewerLayout>(DEFAULT_LAYOUT);
  const spreadRef = useRef<HTMLElement>(null);

  useEffect(() => {
    setLayout(loadLayout());
  }, []);
  useEffect(() => {
    saveLayout(layout);
  }, [layout]);

  // Drag the original│main handle: convert the px delta to a ratio of the fr area
  // (container width minus the fixed handle + dock columns).
  const onMainDelta = useCallback(
    (dx: number) => {
      setLayout((l) => {
        const el = spreadRef.current;
        if (!el) return l;
        const dockPart = mode !== "edit" ? 6 + l.dockWidth : 0;
        const fr = el.clientWidth - 6 - dockPart;
        if (fr <= 0) return l;
        const curOriginalPx = l.originalRatio * fr;
        return { ...l, originalRatio: clamp((curOriginalPx + dx) / fr, MAIN_MIN, MAIN_MAX) };
      });
    },
    [mode],
  );

  // Drag the dock handle: moving left (negative dx) widens the dock.
  const onDockDelta = useCallback((dx: number) => {
    setLayout((l) => ({ ...l, dockWidth: clamp(l.dockWidth - dx, DOCK_MIN, DOCK_MAX) }));
  }, []);

  const toggleOriginal = useCallback(
    () => setLayout((l) => ({ ...l, originalCollapsed: !l.originalCollapsed })),
    [],
  );
```

- [ ] **Step 3: (Task 4를 분리 실행했다면) 상단 바의 collapse 버튼 복원**

Task 4 Step 2에서 뺐다면, 상단 바 좌측 그룹의 `반영률` `<span>` 다음에 다시 넣는다:

```tsx
          {layout.originalCollapsed && (
            <button onClick={toggleOriginal} className="slug hover:text-primary" title="원본 펼치기">
              ▶ 원본
            </button>
          )}
```

- [ ] **Step 4: `<section>` 전체 교체 (176–295행)**

현재 `{/* the spread */}` 주석부터 `</section>`까지를 아래로 통째 교체한다:

```tsx
      {/* the spread — at lg+ a resizable grid: original │ main │ [tool dock].
          Below lg it stacks (flex-col); handles hide and resizing is off. */}
      <section
        ref={spreadRef}
        className="reveal reveal-3 flex min-h-0 flex-1 flex-col lg:grid lg:[grid-template-columns:var(--cols)]"
        style={{ ["--cols" as string]: gridColumns(layout, mode !== "edit") } as CSSProperties}
      >
        {/* original — collapses to 0 width at lg (iframe stays mounted; reopen from top bar) */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-4 pt-3 lg:flex-none">
          <div className="mb-2 flex items-center justify-between px-1">
            <span className="label">원본 — {LANGS[doc.source_lang] ?? doc.source_lang}</span>
            <button
              onClick={toggleOriginal}
              className="slug hidden hover:text-primary lg:inline"
              title="원본 접기"
            >
              ◀ 접기
            </button>
          </div>
          {doc.source_html ? (
            <iframe
              src={doc.source_html}
              title={`원본 — ${doc.title ?? doc.doc_id}`}
              className="sheet min-h-0 w-full flex-1 bg-white"
            />
          ) : (
            <div className="min-h-0 flex-1 overflow-auto">
              {doc.page_images.map((name, i) => (
                <figure
                  key={name}
                  className="mx-auto mb-4"
                  style={{ width: "min(100%, calc(var(--page-w-mm) * 1mm))" }}
                >
                  <div
                    className="relative"
                    style={pageAspect ? { aspectRatio: String(pageAspect) } : undefined}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={`${doc.assets_prefix}/${name}`}
                      loading="lazy"
                      alt={`원본 ${i + 1}쪽`}
                      className="sheet w-full bg-white"
                    />
                    {mode === "retranslate" &&
                      activeSeg?.source_bbox &&
                      activeSeg.source_bbox.page === i && (
                        <div
                          id="active-source-highlight"
                          className="pointer-events-none absolute rounded-[3px]"
                          style={{
                            left: `${activeSeg.source_bbox.x * 100}%`,
                            top: `${activeSeg.source_bbox.y * 100}%`,
                            width: `${activeSeg.source_bbox.w * 100}%`,
                            height: `${activeSeg.source_bbox.h * 100}%`,
                            background: "var(--primary-soft)",
                            outline: "1.5px solid var(--primary)",
                          }}
                        />
                      )}
                    {mode === "manage" &&
                      glossaryReport.flatMap((t) =>
                        t.locations
                          .filter((loc) => loc.source_bbox && loc.source_bbox.page === i)
                          .map((loc) => (
                            <div
                              key={`${t.source}-${loc.segment_index}`}
                              className="pointer-events-none absolute rounded-[3px]"
                              style={{
                                left: `${loc.source_bbox!.x * 100}%`,
                                top: `${loc.source_bbox!.y * 100}%`,
                                width: `${loc.source_bbox!.w * 100}%`,
                                height: `${loc.source_bbox!.h * 100}%`,
                                background: loc.applied ? "var(--ok-bg)" : "var(--danger-bg)",
                                outline: `1.5px solid ${loc.applied ? "var(--ok)" : "var(--danger)"}`,
                              }}
                            />
                          )),
                      )}
                  </div>
                  <figcaption className="mt-1 text-center text-[var(--fs-caption)] opacity-50">
                    {i + 1}
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
        </div>

        <ResizeHandle onDelta={onMainDelta} ariaLabel="원본·번역본 폭 조절" />

        {/* main: editor (edit/retranslate) or highlighted preview (manage) */}
        <div className="relative flex min-h-0 flex-1 flex-col lg:flex-none">
          {mode === "manage" ? (
            <TranslatedPreview docId={doc.doc_id} lang={lang} reloadKey={manageKey} jumpBlock={jumpBlock} />
          ) : (
            <OnlyOfficeEditor
              docId={doc.doc_id}
              lang={lang}
              onConnector={setConnector}
              reloadKey={editorReloadKey}
              bookmark={editorBookmark}
            />
          )}
        </div>

        {/* unified tool dock — one panel, tabs switch mode; ✕ closes back to edit */}
        {mode !== "edit" && (
          <>
            <ResizeHandle onDelta={onDockDelta} ariaLabel="도구 패널 폭 조절" />
            <aside className="flex min-h-0 flex-1 flex-col border-t border-border lg:flex-none lg:border-l lg:border-t-0">
              <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                <button
                  onClick={() => setMode("retranslate")}
                  className={mode === "retranslate" ? "btn-primary" : "btn-secondary"}
                >
                  재번역
                </button>
                <button
                  onClick={() => setMode("manage")}
                  className={mode === "manage" ? "btn-primary" : "btn-secondary"}
                >
                  용어 관리
                </button>
                <button
                  onClick={() => setMode("edit")}
                  className="slug ml-auto hover:text-primary"
                  title="도구 닫기"
                  aria-label="도구 닫기"
                >
                  ✕
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                {mode === "retranslate" ? (
                  <RetranslatePanel
                    docId={doc.doc_id}
                    lang={lang}
                    connector={connector}
                    onActiveSegment={setActiveSeg}
                    onApplied={(bm) => {
                      setEditorBookmark(bm);
                      setEditorReloadKey((k) => k + 1);
                    }}
                  />
                ) : (
                  <GlossaryManagePanel
                    docId={doc.doc_id}
                    lang={lang}
                    reloadKey={manageKey}
                    onJump={(loc) => {
                      if (loc.block != null) setJumpBlock(loc.block);
                    }}
                    onChanged={() => {
                      setManageKey((k) => k + 1);
                      setEditorReloadKey((k) => k + 1);
                    }}
                  />
                )}
              </div>
            </aside>
          </>
        )}
      </section>
```

> 변경 핵심: (1) 옛 `.gutter` divider 와 `lg:w-[400px]` 고정 드로어 제거 → grid 컬럼 + `ResizeHandle`. (2) 도구 독은 항상 우측 한 칸이고 탭으로 `mode` 전환(편집기/프리뷰는 가운데 메인이 담당 — 현행 동작 유지). (3) 원본 헤더에 `◀ 접기`, 페이지 캡션은 `{i+1}`만 작게.

- [ ] **Step 5: 빌드 확인**

Run: `cd frontend && npm run lint && npm run build`
Expected: 통과. (미사용 import 없도록: `clamp/MAIN_*/DOCK_*`는 핸들러에서, `gridColumns/loadLayout/saveLayout/DEFAULT_LAYOUT/ViewerLayout`는 상태·스타일에서 모두 사용됨. 빌드가 미사용을 잡으면 제거.)

- [ ] **Step 6: 수동 확인 (라이트/다크 · 노트북1440/넓은화면)**

재빌드·재기동 후 뷰어 URL:
- `원본│번역본` 사이 핸들 드래그 → 비율 변함. **새로고침 후 비율 유지**(localStorage).
- 원본 헤더 `◀ 접기` → 원본 0폭, 번역본 전체폭. 상단 바 `▶ 원본` → 복원(편집기 리로드 없이).
- `재번역` 탭 → 우측 독 + 가운데 OnlyOffice. `용어 관리` 탭 → 가운데가 하이라이트 프리뷰로, **원본·독 위치 유지**(점프 최소). 독 핸들 드래그로 폭 변경 + 새로고침 유지.
- 독 헤더 `✕` → `edit`로 돌아가 원본·번역 전체폭.
- 창을 `lg` 미만으로 좁히면 세로 스택(핸들 사라짐), 원본·번역·독 모두 보임.

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/app/c/[id]/page.tsx
git commit -m "feat(frontend): resizable grid spread + collapsible original + tabbed tool dock"
```

---

## Task 6: 통합 수동 검증 + 마무리

**Files:** 없음 (검증/정리만).

- [ ] **Step 1: 정적 게이트**

Run: `cd frontend && npm run lint && npm run build` → 통과.
Run: `cd backend && python -m pytest -q` → 통과.

- [ ] **Step 2: 전체 워크 (라이트 + 다크 토글, 노트북·넓은 화면)**

뷰어에서 spec의 검증 항목을 한 번에 점검:
- 상단 바 1줄/2줄 줄바꿈 · 테마 토글로 라이트↔다크.
- 원본│번역 핸들 드래그·복원 · 원본 접기/펼치기.
- 재번역/용어 탭 전환(메인 한 칸만 교체) · 독 폭 드래그·복원 · ✕ 닫기.
- 재번역: 문장 선택 시 원본 하이라이트 스크롤 + 미리보기/적용 동작.
- 용어: 점프·하이라이트·용어 추가·직접 교체 동작.
- (D) incognito에서 OnlyOffice fit-to-width 첫 로드 — 안 되면 Task 1 Step 6대로 D 제외했음을 보고.
- HWP 문서(있으면)로 `source_html` iframe 원본도 접기/리사이즈 정상.

- [ ] **Step 3: 잔여 정리 grep**

Run (frontend): 옛 고정폭/divider 잔재 없음 확인 — `grep -rn "w-\[400px\]\|reveal-2\|className=\"gutter" frontend/src/app/c` → 결과 없음(또는 의도된 것만).

- [ ] **Step 4: finishing-a-development-branch 스킬로 마무리**

`superpowers:finishing-a-development-branch`를 호출해 테스트 통과 확인 후 옵션 제시(머지/PR/유지/폐기). **커밋 전 로컬 우회 3파일 미포함 재확인**(`git status`로 `auth.py`/`documents_store.py`/`proxy.ts`가 unstaged인지 확인).

---

## Self-Review (작성자 점검)

**Spec coverage:**
- A 헤더 압축 → Task 4. ✓
- B 비율 조절·접기·원본 chrome 다이어트 → Task 2(헬퍼)+Task 3(핸들)+Task 5(grid/접기/캡션·pt 축소). ✓
- C 통합 탭 도구 독 + 폭 조절 → Task 5(aside 탭 + onDockDelta). ✓ 결정 포인트(용어=프리뷰 유지, 메인 한 칸만 교체) 반영. ✓
- D OnlyOffice fit-to-width → Task 1. 검증으로 best-effort→확정(단 localStorage-override 캐비엇 명시). ✓
- 엣지(localStorage 불가/클램프/HWP·PDF/lg 미만 스택) → Task 2 loadLayout try/catch·clamp, Task 5 flex-col 폴백·HWP iframe 분기, Step 6 확인. ✓

**Placeholder scan:** 모든 코드 단계에 실제 코드 포함. "적절히 처리" 류 없음. (D는 검증 완료된 조건부 — 플레이스홀더 아님.) ✓

**Type consistency:** `ViewerLayout{originalRatio,dockWidth,originalCollapsed}` — Task 2 정의와 Task 5 사용 일치. `gridColumns(layout, hasDock)` / `clamp(v,lo,hi)` 시그니처 일치. `ResizeHandle{onDelta,ariaLabel}` — Task 3 정의 = Task 5 사용. `mode !== "edit"` = hasDock. ✓

**알려진 트레이드오프(의도):** 드래그 중 매 변경마다 `saveLayout` 호출(localStorage write) — 짧은 드래그라 허용. 필요시 pointerup에서만 저장으로 후속 최적화 가능.
