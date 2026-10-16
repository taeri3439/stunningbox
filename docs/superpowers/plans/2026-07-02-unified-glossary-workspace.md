# Unified 용어 Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold the 고유명사 (proper-noun review) tool into the 용어 (glossary) workspace as an on-demand "고유명사 자동 검출" section, so terminology control has a single entry point.

**Architecture:** Frontend-only information-architecture refactor. The proper-noun candidate list becomes a presentational sub-section at the top of `GlossaryManagePanel`; detection is triggered by a button (no LLM call on open). The separate `proper-nouns` mode/tab/sub-tab is removed. Backend engines, endpoints, and the glossary apply path are untouched.

**Tech Stack:** Next.js 16 (App Router, Turbopack), React, TypeScript, Tailwind v4 design tokens.

## Global Constraints

- **No backend changes.** `backend/pipeline/glossary_audit.py`, `backend/pipeline/proper_noun_audit.py`, all endpoints (`glossary-report`, `proper-nouns`, `glossary/term`), and the apply path (`saveGlossaryTerm` → retranslate/render) stay exactly as-is.
- **Do NOT stage or modify** `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts` (pre-existing dirty working-tree files, unrelated). Never `git add -A`; stage only the specific files each task changes.
- **Korean UI**, professional-translator audience. Reuse existing design tokens/classes only: `.label`, `.slug`, `.mono`, `.field`, `.btn-primary`, `.btn-secondary`, `.chip-jump`, `border-border`, `var(--primary)`, `var(--primary-soft)`, `var(--danger)`, `var(--ok)`, `var(--warn)`, `var(--muted)`, `var(--fs-label)`, `var(--fs-caption)`.
- **No FE unit-test harness exists** (established). Per-task verification = `npm run build` clean (types + lint). Final acceptance = a real-browser pass (prod build; HMR is broken — use `next build` + `next start`).
- Build must stay green after **every** task.
- Detection stays **on-demand** (button), never on panel open. Opening 용어 must fire **zero** `proper-nouns` requests.

---

### Task 1: `ProperNounCandidates` presentational component

**Files:**
- Create: `frontend/src/components/ProperNounCandidates.tsx`

**Interfaces:**
- Produces: `ProperNounCandidates` — a presentational component (no fetch, no `useEffect`), consumed by `GlossaryManagePanel` in Task 2. Props:
  - `state: "idle" | "loading" | "done" | "error"`
  - `terms: ProperNounReport[]`
  - `error: string | null`
  - `busy: string | null` (the `source` currently applying)
  - `onDetect: () => void`
  - `onApply: (source: string, target: string) => void`
  - `onJump: (loc: GlossaryLocation) => void`
- Consumes: `ProperNounReport`, `GlossaryLocation` from `@/lib/types`.

- [ ] **Step 1: Create the component**

Create `frontend/src/components/ProperNounCandidates.tsx`:

```tsx
"use client";

import { useState } from "react";
import type { GlossaryLocation, ProperNounReport } from "@/lib/types";

const CATEGORY_KO: Record<string, string> = {
  brand: "브랜드",
  product: "제품",
  organization: "기관",
  person: "인명",
  place: "지명",
  other: "기타",
};

// 고유명사 자동 검출 (presentational). Detection is triggered by the parent (GlossaryManagePanel)
// on demand — this component owns no fetch, only the per-candidate target drafts + row UI.
export function ProperNounCandidates({
  state,
  terms,
  error,
  busy,
  onDetect,
  onApply,
  onJump,
}: {
  state: "idle" | "loading" | "done" | "error";
  terms: ProperNounReport[];
  error: string | null;
  busy: string | null;
  onDetect: () => void;
  onApply: (source: string, target: string) => void;
  onJump: (loc: GlossaryLocation) => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  return (
    <div className="mb-4">
      <div className="mb-2 flex items-center justify-between px-1">
        <h2 className="label">고유명사 자동 검출</h2>
        <button className="btn-secondary" disabled={state === "loading"} onClick={onDetect}>
          {state === "loading" ? "분석 중…" : state === "idle" ? "검출" : "다시 검출"}
        </button>
      </div>

      {state === "idle" && (
        <p className="slug px-1">용어집에 없이 번역된 고유명사를 찾아 용어집 후보로 제안합니다.</p>
      )}
      {state === "error" && (
        <p className="px-1 text-[var(--fs-caption)]" style={{ color: "var(--danger)" }}>
          고유명사 검출 오류 — {error}
        </p>
      )}
      {state === "done" && terms.length === 0 && (
        <p className="slug px-1">번역된·용어집 외 고유명사가 없습니다.</p>
      )}
      {state === "done" && terms.length > 0 && (
        <p className="slug mb-2 px-1">검토 대상 {terms.length}건 — 처리하면 용어집으로 이동합니다.</p>
      )}

      {terms.length > 0 && (
        <ul className="space-y-2">
          {terms.map((t) => (
            <li
              key={t.source}
              className="rounded-lg border border-border p-2.5"
              style={{ background: "var(--primary-soft)" }}
            >
              <div className="flex items-center gap-2">
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
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <input
                  value={drafts[t.source] ?? ""}
                  onChange={(e) => setDrafts((d) => ({ ...d, [t.source]: e.target.value }))}
                  placeholder="번역어 지정 (비우면 원문 유지)"
                  aria-label={`${t.source} 처리`}
                  className="field w-full"
                />
                <button
                  className="btn-secondary"
                  disabled={busy === t.source}
                  onClick={() => onApply(t.source, t.source)}
                  title="번역하지 않고 원문 그대로 두기 (용어집에 추가)"
                >
                  {busy === t.source ? "…" : "원문 유지"}
                </button>
                <button
                  className="btn-primary"
                  disabled={busy === t.source || !(drafts[t.source] ?? "").trim()}
                  onClick={() => onApply(t.source, drafts[t.source] ?? "")}
                >
                  번역어 지정
                </button>
              </div>
              {t.locations.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {t.locations.map((loc, i) => (
                    <button key={i} onClick={() => onJump(loc)} className="chip-jump mono" title="위치로 이동">
                      #{loc.segment_index}
                    </button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: PASS (compiles; the new file is unused for now — that is fine, no lint error for an exported-but-unused module).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ProperNounCandidates.tsx
git commit -m "feat(glossary): presentational ProperNounCandidates section"
```

---

### Task 2: Own proper-noun detection state inside `GlossaryManagePanel`

**Files:**
- Modify: `frontend/src/components/GlossaryManagePanel.tsx`

**Interfaces:**
- Consumes: `ProperNounCandidates` (Task 1); `api.properNounReport`, `api.saveGlossaryTerm`; `ProperNounReport` from `@/lib/types`.
- Produces: `GlossaryManagePanel` gains one **optional** prop `onPnTermsLoaded?: (terms: ProperNounReport[]) => void` (kept optional so Task 2 builds green before Task 3 wires it). Behavior: renders the candidate section at the top; detection is button-triggered; applying a candidate drops it locally and calls `onChanged()`.

- [ ] **Step 1: Add imports**

In `frontend/src/components/GlossaryManagePanel.tsx`, add to the imports:

```tsx
import { ProperNounCandidates } from "@/components/ProperNounCandidates";
import type { GlossaryLocation, GlossaryTermReport, ProperNounReport } from "@/lib/types";
```

(Replace the existing `import type { GlossaryLocation, GlossaryTermReport } from "@/lib/types";` line so `ProperNounReport` is included.)

- [ ] **Step 2: Add the prop**

Add `onPnTermsLoaded` to the component's prop list and its type:

```tsx
export function GlossaryManagePanel({
  docId,
  lang,
  reloadKey,
  onJump,
  onChanged,
  onPnTermsLoaded,
}: {
  docId: string;
  lang: string;
  reloadKey: number;
  onJump: (loc: GlossaryLocation) => void;
  onChanged: () => void;
  onPnTermsLoaded?: (terms: ProperNounReport[]) => void;
}) {
```

- [ ] **Step 3: Add proper-noun state + handlers + sync effect**

Immediately after the existing `useState` declarations (after `const [showReplace, setShowReplace] = useState(false);`), add:

```tsx
  // 고유명사 자동 검출 (on-demand). No LLM call until the user presses 검출.
  const [pnState, setPnState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [pnTerms, setPnTerms] = useState<ProperNounReport[]>([]);
  const [pnError, setPnError] = useState<string | null>(null);
  const [pnBusy, setPnBusy] = useState<string | null>(null);

  // Keep the page's pnReport (original-pane outlines) in lockstep with the candidate list.
  useEffect(() => {
    onPnTermsLoaded?.(pnTerms);
  }, [pnTerms, onPnTermsLoaded]);

  // Reset detection when the doc/lang changes (do NOT auto-re-run the LLM).
  useEffect(() => {
    setPnState("idle");
    setPnTerms([]);
    setPnError(null);
  }, [docId, lang]);

  async function detectProperNouns() {
    setPnState("loading");
    setPnError(null);
    try {
      const r = await api.properNounReport(docId, lang);
      setPnTerms(r.terms);
      setPnState("done");
    } catch (e) {
      setPnError(e instanceof Error ? e.message : String(e));
      setPnState("error");
    }
  }

  async function applyCandidate(source: string, target: string) {
    if (!target.trim()) return;
    setPnBusy(source);
    setPnError(null);
    try {
      await api.saveGlossaryTerm(docId, lang, { source, target: target.trim(), apply: true });
      setPnTerms((prev) => prev.filter((t) => t.source !== source)); // folded into glossary
      onChanged(); // refresh preview + editor + glossary report
    } catch (e) {
      setPnError(e instanceof Error ? e.message : String(e));
      setPnState("error");
    } finally {
      setPnBusy(null);
    }
  }
```

Note: `onPnTermsLoaded` must be a stable reference from the parent (Task 3 passes the `setPnReport` state setter directly) so the sync effect doesn't thrash.

- [ ] **Step 4: Render the candidate section at the top of the panel**

In the returned JSX, immediately after the opening `<div className="min-h-0 flex-1 overflow-auto px-4 py-4">` and BEFORE `<h2 className="label mb-2 px-1">용어 관리 — 업로드 용어집</h2>`, insert:

```tsx
      <ProperNounCandidates
        state={pnState}
        terms={pnTerms}
        error={pnError}
        busy={pnBusy}
        onDetect={detectProperNouns}
        onApply={applyCandidate}
        onJump={onJump}
      />
      <div className="mb-4 border-t border-border" />
```

(The existing early `if (error) return …용어집 오류…` guard for the glossary-report fetch is unchanged; a glossary-report failure still shows only that error. Detection failure is isolated to the candidate section and does not affect the glossary list — satisfied because `pnError`/`pnState` are separate from the glossary `error`.)

- [ ] **Step 5: Verify build**

Run: `cd frontend && npm run build`
Expected: PASS. (`onPnTermsLoaded` is optional; the page does not pass it yet — still compiles.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/GlossaryManagePanel.tsx
git commit -m "feat(glossary): on-demand proper-noun detection inside 용어 panel"
```

---

### Task 3: Remove the standalone 고유명사 mode from the page; wire the unified panel

**Files:**
- Modify: `frontend/src/app/c/[id]/page.tsx`
- Delete: `frontend/src/components/ProperNounReviewPanel.tsx`

**Interfaces:**
- Consumes: `GlossaryManagePanel` with the new `onPnTermsLoaded` prop (Task 2).
- Produces: top tabs `문서 편집 · 재번역 · 용어`; dock sub-tabs `재번역 · 용어 관리`; `mode` type no longer includes `"proper-nouns"`.

- [ ] **Step 1: Remove the `ProperNounReviewPanel` import**

Delete this line (currently line 11):

```tsx
import { ProperNounReviewPanel } from "@/components/ProperNounReviewPanel";
```

- [ ] **Step 2: Drop `"proper-nouns"` from the `mode` union (3 sites)**

- The `mode` state:
  ```tsx
  const [mode, setMode] = useState<"edit" | "retranslate" | "manage">("edit");
  ```
- The `prevModeRef`:
  ```tsx
  const prevModeRef = useRef<"edit" | "retranslate" | "manage">("edit");
  ```
- The `requestMode` parameter:
  ```tsx
  async function requestMode(target: "edit" | "retranslate" | "manage") {
  ```

- [ ] **Step 3: Remove the 고유명사 top tab**

Delete the top-header button (currently lines 342–347):

```tsx
            <button
              className={mode === "proper-nouns" ? "btn-primary" : "btn-secondary"}
              onClick={() => requestMode(mode === "proper-nouns" ? "edit" : "proper-nouns")}
            >
              고유명사
            </button>
```

- [ ] **Step 4: Fix the right-pane label + preview condition**

- Label (currently line 552):
  ```tsx
              {mode === "manage" ? "용어 검수" : "편집 가능"}
  ```
- Preview branch condition (currently line 556): change `mode === "manage" || mode === "proper-nouns"` to:
  ```tsx
            {mode === "manage" ? (
  ```

- [ ] **Step 5: Show proper-noun outlines in `manage` mode on the original pane**

The original-pane overlay currently has a `mode === "manage"` glossary-outline block followed by a `mode === "proper-nouns"` pn-outline block. Change the pn block's guard from `mode === "proper-nouns"` to `mode === "manage"` so both render together in the unified mode (pn outlines appear only once `pnReport` is populated by detection):

```tsx
                    {mode === "manage" &&
                      pnReport.flatMap((t) =>
                        t.locations
                          .filter((loc) => loc.source_bbox && loc.source_bbox.page === i)
                          .map((loc) => (
                            <div
                              key={`pn-${t.source}-${loc.segment_index}`}
                              className="pointer-events-none absolute rounded-[3px]"
                              style={{
                                left: `${loc.source_bbox!.x * 100}%`,
                                top: `${loc.source_bbox!.y * 100}%`,
                                width: `${loc.source_bbox!.w * 100}%`,
                                height: `${loc.source_bbox!.h * 100}%`,
                                background: "var(--primary-soft)",
                                outline: "1.5px solid var(--primary)",
                              }}
                            />
                          )),
                      )}
```

(The existing `mode === "manage"` glossary-outline block just above it is unchanged. Glossary outlines use ok/danger colors; proper-noun outlines use the primary accent — they remain visually distinct.)

- [ ] **Step 6: Remove the 고유명사 검수 sub-tab in the dock**

Delete the dock sub-tab button (currently lines 607–612):

```tsx
                <button
                  onClick={() => setMode("proper-nouns")}
                  className={mode === "proper-nouns" ? "btn-primary" : "btn-secondary"}
                >
                  고유명사 검수
                </button>
```

- [ ] **Step 7: Remove the `ProperNounReviewPanel` render branch + wire the unified `GlossaryManagePanel`**

In the dock body, the render is currently `mode === "retranslate" ? <RetranslatePanel/> : mode === "proper-nouns" ? <ProperNounReviewPanel/> : <GlossaryManagePanel/>`. Remove the middle branch so it reads `mode === "retranslate" ? <RetranslatePanel/> : <GlossaryManagePanel/>`, and update the `GlossaryManagePanel` props to pass `onPnTermsLoaded={setPnReport}` and an `onJump` that also scrolls the original pane (superset that serves both glossary chips and candidate chips):

```tsx
                ) : (
                  <GlossaryManagePanel
                    docId={doc.doc_id}
                    lang={lang}
                    reloadKey={manageKey}
                    onPnTermsLoaded={setPnReport}
                    onJump={(loc) => {
                      if (loc.block_id != null) setJumpBlock(loc.block_id); // scroll translation preview
                      if (loc.source_bbox != null)
                        // scroll the ORIGINAL pane to that page (the box is outlined there).
                        requestAnimationFrame(() =>
                          document
                            .getElementById(`pn-page-${loc.source_bbox!.page}`)
                            ?.scrollIntoView({ behavior: "smooth", block: "center" }),
                        );
                    }}
                    onChanged={() => {
                      setManageKey((k) => k + 1);
                      setEditorReloadKey((k) => k + 1);
                    }}
                  />
                )}
```

The entire `mode === "proper-nouns" ? ( <ProperNounReviewPanel ... /> ) :` block (currently lines 641–663) is deleted by this replacement.

- [ ] **Step 8: Delete the now-unused component**

```bash
git rm frontend/src/components/ProperNounReviewPanel.tsx
```

- [ ] **Step 9: Confirm no stragglers**

Run a search for leftovers:
Grep for `proper-nouns` and `ProperNounReviewPanel` under `frontend/src`.
Expected: no matches in `frontend/src/app/c/[id]/page.tsx`; `ProperNounReviewPanel` has zero references. (`pnReport`/`setPnReport`, `properNounReport`, `ProperNounReport`, and `ProperNounCandidates` are expected and fine.)

- [ ] **Step 10: Verify build**

Run: `cd frontend && npm run build`
Expected: PASS (types + lint clean; no unused-import/var errors).

- [ ] **Step 11: Commit**

```bash
git add frontend/src/app/c/[id]/page.tsx
git commit -m "feat(glossary): unify 고유명사 into the 용어 workspace; remove separate tab"
```

---

## Verification (final browser acceptance — prod build)

Backend already runs on `:8012` (DEV_NO_AUTH + OnlyOffice env); rebuild the frontend prod bundle (`BACKEND_URL=http://127.0.0.1:8012 DEV_NO_AUTH=1 npm run build` then `next start` on a fresh port) and open `http://127.0.0.1:<port>/c/test-watergun?lang=en`. Then:

1. **Single entry point:** top tabs show only `문서 편집 · 재번역 · 용어`; the dock sub-tabs show only `재번역 · 용어 관리`. No 고유명사 tab anywhere.
2. **No LLM on open:** click 용어; confirm the backend log shows **no** `GET /proper-nouns` request on open (only `glossary-report` + preview). The glossary section renders immediately; the top shows "고유명사 자동 검출" with a **검출** button.
3. **On-demand detect:** click **검출** → exactly one `GET /proper-nouns` fires; `분석 중…` then candidates render with the `✦ 제안` accent; the original pane outlines their locations (primary accent).
4. **Fold into glossary:** handle one candidate (번역어 지정 or 원문 유지) → applies (`glossary/term?apply` → render), editor + preview reflect it, the candidate leaves the list, and the term appears in the "내 용어집" list below.
5. **Glossary unchanged:** existing edit/add/status dots/location chips + `직접 교체` all still work.
6. **Regression:** 재번역 and 문서 편집 bidirectional sync still pass (edit in editor → 재번역; retranslate → editor).

Capture a screenshot of the unified 용어 panel (candidates + glossary in one scroll) as evidence.

## Self-Review

- **Spec coverage:** IA change (Task 3), on-demand detection (Task 2 + `ProperNounCandidates`), candidate→glossary fold (Task 2 `applyCandidate`), preserved engines/endpoints/apply path (no backend tasks), original-pane outlines in manage mode (Task 3 Step 5). Covered.
- **Type consistency:** `onPnTermsLoaded?: (terms: ProperNounReport[]) => void` matches `setPnReport` (page state `ProperNounReport[]`). `ProperNounCandidates` prop types match `pnState`/`pnTerms`/`pnError`/`pnBusy`. `api.saveGlossaryTerm(docId, lang, {source, target, apply})` and `api.properNounReport(docId, lang)` match existing signatures.
- **Placeholders:** none — full component code + exact edits given.
- **Build-green invariant:** Task 1 adds an unused file (OK); Task 2 uses it with an optional prop (OK before page wiring); Task 3 wires + deletes the dead file in one task. Each ends on `npm run build`.
