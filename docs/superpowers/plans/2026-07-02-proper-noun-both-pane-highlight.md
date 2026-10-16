# Proper-noun Both-Pane Highlight — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Clicking a 고유명사 candidate highlights that term in both the 원본 (source, PDF sentence-box) and 번역본 (translation, word-level) panes.

**Architecture:** Frontend-only. Lift `activePnTerm` to the page; `TranslatedPreview` injects an indigo `<mark class="pn-active">` into its same-origin iframe (no re-fetch); the PDF source overlays become interactive buttons with active/dim styling; the candidate row toggles activation.

**Tech Stack:** Next.js 16, React, TypeScript, Tailwind v4 tokens.

## Global Constraints

- **No backend changes.** No new API calls. All data comes from the already-fetched `pnReport` / `ProperNounReport`.
- **Do NOT stage or modify** `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, `backend/_*.py`, `test-pdfs/`, or root images/logs. Never `git add -A`; stage only the file(s) each task changes.
- Reuse existing tokens/classes: `var(--primary)`, `var(--primary-soft)`, `.slug`, `.mono`, `.field`, `.btn-*`, `.chip-jump`, `border-border`. The in-iframe `pn-active` mark uses hardcoded hex (`#c7d2fe` bg + `#4f46e5` ring) — distinct from the amber glossary `mark.gloss` (`#fde68a`) — matching the existing in-iframe hardcoded-hex convention.
- **No FE unit-test harness** (established). Per-task verification = `cd frontend && npm run build` clean (types + lint). Final = browser acceptance (prod build; HMR broken).
- Build green after every task.
- Highlight injection must be **client-side only** (no `/preview` re-fetch on click).

---

### Task 1: `TranslatedPreview` — inject active-term highlight

**Files:**
- Modify: `frontend/src/components/TranslatedPreview.tsx`

**Interfaces:**
- Produces: `TranslatedPreview` gains optional prop `highlightTerm?: string | null`. When set, every case-insensitive occurrence of the term in the preview iframe is wrapped in `<mark class="pn-active">` (indigo) and the first is scrolled into view; when null/empty, prior marks are removed. Backward compatible (callers that omit it are unaffected).

- [ ] **Step 1: Rewrite the component**

Replace the entire contents of `frontend/src/components/TranslatedPreview.tsx` with:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

// Read-only highlighted translation (served HTML) for 용어 관리 mode. `reloadKey` bumps force a
// refresh after an apply; `jumpBlock` scrolls to a block anchor; `highlightTerm` wraps every
// occurrence of a proper-noun's translated form in an indigo <mark> (client-side, no re-fetch).
export function TranslatedPreview({
  docId,
  lang,
  reloadKey,
  jumpBlock,
  highlightTerm = null,
}: {
  docId: string;
  lang: string;
  reloadKey: number;
  jumpBlock: string | null;
  highlightTerm?: string | null;
}) {
  const ref = useRef<HTMLIFrameElement | null>(null);
  const [loaded, setLoaded] = useState(false);

  // Same-origin (/api proxy) → we can set the inner hash to scroll to #blk{n}.
  function scrollToJump() {
    if (jumpBlock == null) return;
    const win = ref.current?.contentWindow;
    if (win) win.location.hash = `blk${jumpBlock}`;
  }

  // Inject/refresh the active proper-noun highlight inside the same-origin iframe (no re-fetch).
  function applyHighlight() {
    const doc = ref.current?.contentDocument;
    if (!doc || !doc.body) return;
    // 1. unwrap any prior highlights (restore original text nodes)
    doc.querySelectorAll("mark.pn-active").forEach((m) => {
      const parent = m.parentNode;
      if (!parent) return;
      while (m.firstChild) parent.insertBefore(m.firstChild, m);
      parent.removeChild(m);
      parent.normalize();
    });
    const needle = (highlightTerm ?? "").trim().toLowerCase();
    if (!needle) return;
    // 2. ensure the highlight style exists (indigo — distinct from amber glossary mark)
    if (!doc.getElementById("pn-active-style")) {
      const style = doc.createElement("style");
      style.id = "pn-active-style";
      style.textContent =
        "mark.pn-active{background:#c7d2fe;color:inherit;border-radius:2px;box-shadow:0 0 0 1.5px #4f46e5;}";
      doc.head.appendChild(style);
    }
    // 3. collect matching text nodes first (mutating during a TreeWalker walk is unsafe)
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
    const targets: Text[] = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const tn = node as Text;
      const tag = tn.parentElement?.tagName;
      if (tag !== "SCRIPT" && tag !== "STYLE" && (tn.textContent ?? "").toLowerCase().includes(needle)) {
        targets.push(tn);
      }
    }
    // 4. wrap each occurrence
    let first: HTMLElement | null = null;
    for (const tn of targets) {
      const text = tn.textContent ?? "";
      const lower = text.toLowerCase();
      const frag = doc.createDocumentFragment();
      let cursor = 0;
      let idx = lower.indexOf(needle);
      while (idx >= 0) {
        if (idx > cursor) frag.appendChild(doc.createTextNode(text.slice(cursor, idx)));
        const mark = doc.createElement("mark");
        mark.className = "pn-active";
        mark.textContent = text.slice(idx, idx + needle.length);
        frag.appendChild(mark);
        if (!first) first = mark;
        cursor = idx + needle.length;
        idx = lower.indexOf(needle, cursor);
      }
      if (cursor < text.length) frag.appendChild(doc.createTextNode(text.slice(cursor)));
      tn.parentNode?.replaceChild(frag, tn);
    }
    first?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // Live jump: jumpBlock changed on an already-loaded iframe (chip click, no reload).
  useEffect(() => {
    scrollToJump();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpBlock]);

  // Re-apply the highlight when the term changes or once the (re)loaded iframe is ready.
  useEffect(() => {
    if (loaded) applyHighlight();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightTerm, loaded]);

  // A reload (key remount) drops the loaded gate until onLoad fires again.
  useEffect(() => {
    setLoaded(false);
  }, [reloadKey]);

  return (
    <iframe
      ref={ref}
      key={reloadKey}
      src={`${api.previewUrl(docId, lang)}&v=${reloadKey}`}
      onLoad={() => {
        setLoaded(true);
        scrollToJump();
      }}
      title="번역본 미리보기 (용어 하이라이트)"
      className="sheet min-h-0 w-full flex-1 bg-white"
    />
  );
}
```

- [ ] **Step 2: Build**

Run: `cd "c:/Users/danie/OneDrive/문서/바탕 화면/stunningboxreflow/stunningboxreflow/frontend" && npm run build`
Expected: exit 0. (No caller passes `highlightTerm` yet — optional, so existing usage compiles.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TranslatedPreview.tsx
git commit -m "feat(glossary): TranslatedPreview highlights an active proper-noun term

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `ProperNounCandidates` — selectable row

**Files:**
- Modify: `frontend/src/components/ProperNounCandidates.tsx`

**Interfaces:**
- Consumes: nothing new external.
- Produces: two optional props — `activeSource?: string | null` and `onActivate?: (term: ProperNounReport) => void`. The header row (source→translated line) is clickable and calls `onActivate(t)`; the `<li>` shows a selected ring when `t.source === activeSource`. Backward compatible.

- [ ] **Step 1: Add the props**

In the destructured props and the type, add `activeSource` and `onActivate` (after `onJump`):

```tsx
export function ProperNounCandidates({
  state,
  terms,
  error,
  busy,
  onDetect,
  onApply,
  onJump,
  activeSource = null,
  onActivate,
}: {
  state: "idle" | "loading" | "done" | "error";
  terms: ProperNounReport[];
  error: string | null;
  busy: string | null;
  onDetect: () => void;
  onApply: (source: string, target: string) => void;
  onJump: (loc: GlossaryLocation) => void;
  activeSource?: string | null;
  onActivate?: (term: ProperNounReport) => void;
}) {
```

- [ ] **Step 2: Make the `<li>` show a selected ring and the header row activate**

Replace the `<li ...>` opening tag and its header `<div className="flex items-center gap-2">` block (the source→translated line) with:

```tsx
            <li
              key={t.source}
              className="rounded-lg border p-2.5"
              style={{
                background: "var(--primary-soft)",
                borderColor: t.source === activeSource ? "var(--primary)" : "var(--border)",
                boxShadow: t.source === activeSource ? "0 0 0 1.5px var(--primary)" : undefined,
              }}
            >
              <div
                className="flex items-center gap-2"
                style={{ cursor: onActivate ? "pointer" : undefined }}
                onClick={() => onActivate?.(t)}
                title={onActivate ? "원본·번역본에서 이 고유명사 강조" : undefined}
              >
                <span className="slug shrink-0" style={{ color: "var(--primary)" }}>
                  ✦ 제안
                </span>
                <span className="mono flex-1 text-[var(--fs-label)]">
                  {t.source} → <span style={{ color: "var(--danger)" }}>{t.translated}</span>
                </span>
                <span className="slug">
                  {CATEGORY_KO[t.category] ?? t.category} · {t.count}
                </span>
              </div>
```

(The `border border-border` classes move to inline `borderColor` so the active ring can override; keep `rounded-lg ... p-2.5`. The 처리 inputs/buttons row and the #N chip row below are unchanged and are NOT inside this clickable header div, so activating never interferes with applying.)

- [ ] **Step 3: Build**

Run: `cd "c:/Users/danie/OneDrive/문서/바탕 화면/stunningboxreflow/stunningboxreflow/frontend" && npm run build`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ProperNounCandidates.tsx
git commit -m "feat(glossary): selectable proper-noun candidate row (active ring)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `page.tsx` + `GlossaryManagePanel` — activation state, source overlays, wiring

**Files:**
- Modify: `frontend/src/components/GlossaryManagePanel.tsx`
- Modify: `frontend/src/app/c/[id]/page.tsx`

**Interfaces:**
- Consumes: `TranslatedPreview.highlightTerm` (Task 1); `ProperNounCandidates.activeSource`/`onActivate` (Task 2).
- Produces: the full cross-pane behavior wired end-to-end.

- [ ] **Step 1: Thread props through `GlossaryManagePanel`**

Add two optional props. In the destructure + type (after `onPnTermsLoaded`):

```tsx
  onPnTermsLoaded,
  activePnSource = null,
  onActivatePn,
}: {
  docId: string;
  lang: string;
  reloadKey: number;
  onJump: (loc: GlossaryLocation) => void;
  onChanged: () => void;
  onPnTermsLoaded?: (terms: ProperNounReport[]) => void;
  activePnSource?: string | null;
  onActivatePn?: (term: ProperNounReport) => void;
}) {
```

Then pass them into the `<ProperNounCandidates .../>` render:

```tsx
      <ProperNounCandidates
        state={pnState}
        terms={pnTerms}
        error={pnError}
        busy={pnBusy}
        onDetect={detectProperNouns}
        onApply={applyCandidate}
        onJump={onJump}
        activeSource={activePnSource}
        onActivate={onActivatePn}
      />
```

- [ ] **Step 2: Build (interim)**

Run: `cd "c:/Users/danie/OneDrive/문서/바탕 화면/stunningboxreflow/stunningboxreflow/frontend" && npm run build`
Expected: exit 0 (page.tsx doesn't pass the new props yet — optional, compiles).

- [ ] **Step 3: Add page state + effects**

In `frontend/src/app/c/[id]/page.tsx`, after the `pnReport` state (line ~59) add:

```tsx
  // 고유명사 클릭 → 원본·번역본 동시 강조: the currently selected proper-noun term.
  const [activePnTerm, setActivePnTerm] = useState<ProperNounReport | null>(null);
  const togglePn = (term: ProperNounReport) =>
    setActivePnTerm((prev) => (prev?.source === term.source ? null : term));
```

Then, near the other effects (e.g. just after the glossary-report effect around line 249), add:

```tsx
  // Clear the active proper noun once it's folded into the glossary (no longer a candidate).
  useEffect(() => {
    setActivePnTerm((prev) => (prev && pnReport.some((t) => t.source === prev.source) ? prev : null));
  }, [pnReport]);

  // Scroll the source pane to the active term's first located page (PDF only).
  useEffect(() => {
    if (!activePnTerm) return;
    const loc = activePnTerm.locations.find((l) => l.source_bbox);
    if (loc?.source_bbox)
      requestAnimationFrame(() =>
        document
          .getElementById(`pn-page-${loc.source_bbox!.page}`)
          ?.scrollIntoView({ behavior: "smooth", block: "center" }),
      );
  }, [activePnTerm]);
```

- [ ] **Step 4: Make the source-pane proper-noun overlays interactive with active/dim styling**

Replace the `mode === "manage"` proper-noun overlay block (currently the `pnReport.flatMap(...)` that renders `<div className="pointer-events-none absolute rounded-[3px]" ...>`, lines ~507-525) with:

```tsx
                    {mode === "manage" &&
                      pnReport.flatMap((t) => {
                        const isActive = activePnTerm?.source === t.source;
                        const dimmed = activePnTerm != null && !isActive;
                        return t.locations
                          .filter((loc) => loc.source_bbox && loc.source_bbox.page === i)
                          .map((loc) => (
                            <button
                              key={`pn-${t.source}-${loc.segment_index}`}
                              type="button"
                              aria-label={`고유명사 ${t.source} 강조`}
                              onClick={() => togglePn(t)}
                              className="absolute rounded-[3px]"
                              style={{
                                left: `${loc.source_bbox!.x * 100}%`,
                                top: `${loc.source_bbox!.y * 100}%`,
                                width: `${loc.source_bbox!.w * 100}%`,
                                height: `${loc.source_bbox!.h * 100}%`,
                                background: "var(--primary-soft)",
                                outline: `${isActive ? "2px" : "1.5px"} solid var(--primary)`,
                                opacity: dimmed ? 0.3 : 1,
                                cursor: "pointer",
                              }}
                            />
                          ));
                      })}
```

- [ ] **Step 5: Pass `highlightTerm` to `TranslatedPreview` and the activation props to `GlossaryManagePanel`**

- Update the manage-mode `TranslatedPreview` (line ~550):

```tsx
              <TranslatedPreview
                docId={doc.doc_id}
                lang={lang}
                reloadKey={manageKey}
                jumpBlock={jumpBlock}
                highlightTerm={activePnTerm?.translated ?? null}
              />
```

- Update the `<GlossaryManagePanel .../>` render (line ~629) to add the two props (keep the existing `onPnTermsLoaded`, `onJump`, `onChanged`):

```tsx
                    onPnTermsLoaded={setPnReport}
                    activePnSource={activePnTerm?.source ?? null}
                    onActivatePn={togglePn}
```

- [ ] **Step 6: Build**

Run: `cd "c:/Users/danie/OneDrive/문서/바탕 화면/stunningboxreflow/stunningboxreflow/frontend" && npm run build`
Expected: exit 0. Ensure no unused-var/lint errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/GlossaryManagePanel.tsx "frontend/src/app/c/[id]/page.tsx"
git commit -m "feat(glossary): click a proper noun to highlight it in both source and translation panes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Verification (final browser acceptance — prod build)

Backend :8012 unchanged; rebuild the frontend (`BACKEND_URL=http://127.0.0.1:8012 DEV_NO_AUTH=1 npm run build`) + `next start` on a fresh port; open `/c/test-watergun?lang=en` (a PDF doc):

1. 용어 → 검출 → click the **방수총** candidate row.
2. **번역본:** every `Water Cannon` (its translated form) is wrapped in an **indigo** highlight and the first is scrolled into view — visually distinct from the **amber** glossary mark.
3. **원본:** 방수총's source sentence box(es) render solid/strong while the other proper-noun boxes **dim**; the pane scrolls to the first.
4. The candidate row shows a **selected ring**.
5. Click the row (or an active box) again → both panes clear.
6. Confirm (devtools/network) **no `/preview` re-fetch** fired on click — the highlight is client-side injection.
7. Regression: 재번역 (segment click still highlights) + 문서 편집 (editor loads) unaffected; the amber glossary highlight still works.

Capture a screenshot showing both panes highlighted for one clicked proper noun.

## Self-Review

- **Spec coverage:** 번역본 word-highlight (Task 1), selectable row (Task 2), source-overlay active/dim + state + wiring (Task 3). Covered.
- **Type consistency:** `highlightTerm?: string \| null` fed `activePnTerm?.translated ?? null` (string). `onActivate?: (term: ProperNounReport) => void` = `togglePn`. `activeSource`/`activePnSource` = `activePnTerm?.source ?? null` (string|null). Consistent across the three files.
- **Build-green invariant:** Task 1 adds an optional prop (unused → compiles); Task 2 adds optional props (unused → compiles); Task 3 Step 2 builds before the page wires them; Step 6 builds the full wiring.
- **No backend / no re-fetch:** highlight is pure client-side DOM injection into the same-origin iframe; no new API calls.
