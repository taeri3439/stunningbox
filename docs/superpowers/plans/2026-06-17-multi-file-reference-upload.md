# Multi-File Reference Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user upload many reference files at once into one domain, review each file's auto-detected format in a status-based triage table, and commit the good ones sequentially with live progress.

**Architecture:** Frontend-only; no backend changes. `frontend/src/app/app/style/page.tsx` makes its file input `multiple`. 1 file → the existing detail card (unchanged). 2+ files → a new `BatchReviewTable` that (a) batch-analyzes via the existing `api.analyzeStyleReference` with concurrency 3, (b) auto-excludes `source_only` and duplicate-name rows, (c) lets the user expand rows (sample pairs + format override + rename) and select, then (d) commits selected rows **sequentially** via the existing `api.uploadStyleReference` with per-row progress, retry, and stop. Pure helpers live in `frontend/src/lib/batchUpload.ts`.

**Tech Stack:** Next 16.2.6 (React 19), TypeScript, Tailwind v4 design tokens. **No frontend test runner exists** (package.json has only `dev`/`build`/`lint`) — verification is `next build` (typecheck + compile) per task and a **browser walkthrough** at integration milestones, matching this repo's established frontend practice. Backend is untouched (existing 318-test suite still applies).

**Build/verify command (run from repo root):**
```bash
cd "frontend" && BACKEND_URL=http://127.0.0.1:8002 node ./node_modules/next/dist/bin/next build 2>&1 | tail -20
```
Expected: `✓ Compiled successfully`, `Finished TypeScript`, and `/app/style` listed under Route (app).

**Local run for browser checks (servers already used this session):**
- Backend: `cd backend && DEV_NO_AUTH=1 STORAGE_DIR=/tmp/styledemo .venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8002 --log-level warning`
- Frontend: `cd frontend && BACKEND_URL=http://127.0.0.1:8002 DEV_NO_AUTH=1 node ./node_modules/next/dist/bin/next start -p 3010` (rebuild before restart; `BACKEND_URL` must be set at **build** time — it is baked into routes-manifest.json).
- Test files: `test-pdfs/style-refs/*.hwp` (17 bilingual), `test-pdfs/*.hwp` (Korean-only → source_only).

**Reused API (frontend/src/lib/api.ts — do not change):**
- `api.analyzeStyleReference({ target: File }) => Promise<StyleAnalysis>`
- `api.uploadStyleReference({ domain, target, name?, mode? }) => Promise<{ id; domain; segment_count }>`
- `api.styleReferences(domain) => Promise<{ domain; references: StyleReference[] }>`
- Types: `StyleMode` (`interleaved|columns|sequential|paired_files|target_only|source_only`), `StyleAnalysis { mode, style_count, pair_count, sample_pairs[], sample_style[], has_pairs, estimated?, message? }`, `StyleReference { id, name, segment_count, has_pairs, lang_tgt, ... }`.
- Existing constants in page.tsx: `MODE_LABEL: Record<StyleMode,string>`, `OVERRIDE_MODES: StyleMode[] = ["interleaved","sequential","columns","target_only"]`.

---

## Task 1: Pure helpers (`batchUpload.ts`)

**Files:**
- Create: `frontend/src/lib/batchUpload.ts`

- [ ] **Step 1: Implement the helpers**

```ts
// Pure helpers for the Style Library batch upload (no React, no network).

/** Derive a readable reference name from a filename: prefer the first [bracketed] segment
 *  (these reference files embed an English product name in brackets), else the stem. */
export function deriveName(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "");
  const bracket = stem.match(/\[([^\]]+)\]/);
  return (bracket ? bracket[1] : stem).trim().slice(0, 80);
}

/** Run `fn` over `items` with at most `limit` in flight; resolves to results in input order.
 *  Never rejects — `fn` is expected to capture its own errors into the result value. */
export async function mapLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i], i);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}
```

- [ ] **Step 2: Typecheck**

Run the build/verify command. Expected: compiles, no TS errors. (`batchUpload.ts` is not imported yet — that's fine; Next build still typechecks it.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/batchUpload.ts
git commit -m "feat(style): batch-upload pure helpers (deriveName, mapLimit)"
```

---

## Task 2: Row model + `BatchReviewTable` (presentational)

A single source of truth for a row, and a component that renders rows, handles select/expand/override/rename, and shows status — but does NOT analyze or commit (the page drives those and passes data + callbacks in).

**Files:**
- Create: `frontend/src/app/app/style/BatchReviewTable.tsx`

- [ ] **Step 1: Define the row type + component**

```tsx
"use client";

import { useState } from "react";
import { type StyleAnalysis, type StyleMode } from "@/lib/api";

export type RowStatus =
  | "analyzing"        // analyze in flight
  | "ready"            // analyzed, committable
  | "flagged"          // analyzed but auto-excluded (source_only or duplicate)
  | "analyze_failed"   // analyze threw
  | "queued"           // selected, waiting to commit
  | "committing"       // commit in flight
  | "done"             // committed
  | "commit_failed";   // commit threw

export type RefRow = {
  id: string;              // stable key (file name + index)
  file: File;
  name: string;            // editable display name (deriveName default)
  status: RowStatus;
  selected: boolean;
  analysis: StyleAnalysis | null;
  modeOverride: "" | StyleMode;  // "" = use detected
  flagReason?: "no_translation" | "duplicate";
  error?: string;          // analyze_failed / commit_failed message
};

const FLAG_TEXT: Record<NonNullable<RefRow["flagReason"]>, string> = {
  no_translation: "원문만 — 번역 내용 없음",
  duplicate: "이미 있음 (같은 분야)",
};

const STATUS_DOT: Record<RowStatus, { icon: string; color: string }> = {
  analyzing: { icon: "…", color: "var(--text-3)" },
  ready: { icon: "✓", color: "var(--ok)" },
  flagged: { icon: "⚠", color: "var(--danger)" },
  analyze_failed: { icon: "!", color: "var(--danger)" },
  queued: { icon: "•", color: "var(--text-3)" },
  committing: { icon: "⏳", color: "var(--primary)" },
  done: { icon: "✓", color: "var(--ok)" },
  commit_failed: { icon: "⚠", color: "var(--danger)" },
};

export function BatchReviewTable({
  rows,
  modeLabel,
  overrideModes,
  onToggle,
  onRename,
  onOverride,
  onRetry,
}: {
  rows: RefRow[];
  modeLabel: Record<StyleMode, string>;
  overrideModes: StyleMode[];
  onToggle: (id: string, selected: boolean) => void;
  onRename: (id: string, name: string) => void;
  onOverride: (id: string, mode: "" | StyleMode) => void;
  onRetry: (id: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <ul className="flex flex-col divide-y divide-border rounded-[var(--r-md)] border border-border">
      {rows.map((r) => {
        const dot = STATUS_DOT[r.status];
        const a = r.analysis;
        const expandable = r.status !== "analyzing" && r.status !== "analyze_failed";
        return (
          <li key={r.id} className="flex flex-col">
            <div className="flex items-center gap-3 px-3 py-2.5">
              <input
                type="checkbox"
                checked={r.selected}
                disabled={r.flagReason === "no_translation" || r.status === "analyzing"}
                onChange={(e) => onToggle(r.id, e.target.checked)}
                aria-label={`${r.name} 선택`}
              />
              <span className="shrink-0 font-semibold" style={{ color: dot.color }}>{dot.icon}</span>
              <span className="min-w-0 flex-1 truncate text-sm" title={r.file.name}>{r.name}</span>
              <span className="shrink-0 text-sm text-text-3">
                {r.status === "analyzing" && "분석 중…"}
                {r.status === "analyze_failed" && "분석 실패"}
                {r.flagReason && FLAG_TEXT[r.flagReason]}
                {!r.flagReason && a && r.status !== "analyzing" && (
                  <>
                    {modeLabel[(r.modeOverride || a.mode) as StyleMode]}
                    {" · "}짝 {a.pair_count}{a.estimated ? "(추정)" : ""}·문체 {a.style_count}
                  </>
                )}
                {r.status === "committing" && " · 추가 중…"}
                {r.status === "done" && " · 추가됨"}
                {r.status === "commit_failed" && ` · 실패: ${r.error ?? ""}`}
              </span>
              {(r.status === "analyze_failed" || r.status === "commit_failed") && (
                <button type="button" onClick={() => onRetry(r.id)}
                  className="slug shrink-0 transition-colors hover:text-primary">재시도</button>
              )}
              {expandable && (
                <button type="button" aria-label="자세히"
                  onClick={() => setOpen(open === r.id ? null : r.id)}
                  className="shrink-0 text-text-3 transition-colors hover:text-primary">
                  {open === r.id ? "▾" : "▸"}
                </button>
              )}
            </div>

            {open === r.id && a && (
              <div className="flex flex-col gap-2 px-10 pb-3" style={{ background: "var(--surface-2)" }}>
                <label className="flex flex-wrap items-center gap-2 pt-2">
                  <span className="slug">이름</span>
                  <input className="field" style={{ padding: "0.3rem 0.6rem" }}
                    value={r.name} onChange={(e) => onRename(r.id, e.target.value)} />
                </label>
                {a.mode !== "source_only" && (
                  <label className="flex flex-wrap items-center gap-2">
                    <span className="slug">형식</span>
                    <select className="field" style={{ padding: "0.3rem 0.6rem", width: "auto" }}
                      value={r.modeOverride} onChange={(e) => onOverride(r.id, e.target.value as "" | StyleMode)}>
                      <option value="">자동 감지 ({modeLabel[a.mode]})</option>
                      {overrideModes.map((m) => (
                        <option key={m} value={m}>{modeLabel[m]}</option>
                      ))}
                    </select>
                  </label>
                )}
                {a.sample_pairs.length > 0 && (
                  <ul className="flex flex-col gap-1">
                    {a.sample_pairs.slice(0, 3).map((p, i) => (
                      <li key={i} className="slug break-words">
                        <span className="text-text-2">{p.source.slice(0, 48)}</span>
                        <span className="mx-1.5 text-text-3">→</span>
                        <span style={{ color: "var(--primary-hover)" }}>{p.target.slice(0, 48)}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {a.mode === "source_only" && (
                  <p className="slug" style={{ color: "var(--danger)" }}>
                    {a.message ?? "번역 내용이 없는 파일이에요."}
                  </p>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
```

- [ ] **Step 2: Typecheck** — run the build/verify command. Expected: compiles (component unused yet is fine). Fix any TS errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/app/style/BatchReviewTable.tsx
git commit -m "feat(style): BatchReviewTable presentational component + RefRow model"
```

---

## Task 3: Wire batch analyze into the page (multiple input + mode switch + dedup)

**Files:**
- Modify: `frontend/src/app/app/style/page.tsx`

- [ ] **Step 1: Imports + state**

At the top imports, add:
```tsx
import { BatchReviewTable, type RefRow } from "./BatchReviewTable";
import { deriveName, mapLimit } from "@/lib/batchUpload";
```
(`page.tsx` already imports `api`, `LANGS`, and the types `StyleAnalysis`, `StyleDomain`, `StyleMode`, `StyleReference` from `@/lib/api` — no new type imports are needed for Tasks 3–4.)

In the component state block (near the other upload state), add:
```tsx
const [rows, setRows] = useState<RefRow[]>([]);   // batch mode (2+ files)
const batch = rows.length > 0;
```

- [ ] **Step 2: Make the primary file input `multiple` and branch on count**

In the "과거 번역 파일 (필수)" `<input type="file" …>`, add `multiple` and replace its `onChange` with a handler that branches:
```tsx
onChange={(e) => onPickFiles(Array.from(e.target.files ?? []))}
```
Add the handler in the component:
```tsx
function onPickFiles(files: File[]) {
  if (files.length <= 1) {
    setRows([]);                                  // single-file path: existing card
    setTarget(files[0] ?? null);
    return;
  }
  setTarget(null);                                // 2+ files: batch table replaces the card
  const existing = new Set(refsInDomain.map((r) => r.name));   // see Step 3
  const seeded: RefRow[] = files.map((file, i) => ({
    id: `${file.name}#${i}`,
    file,
    name: deriveName(file.name),
    status: "analyzing",
    selected: false,
    analysis: null,
    modeOverride: "",
    flagReason: existing.has(deriveName(file.name)) ? "duplicate" : undefined,
  }));
  setRows(seeded);
  void analyzeAll(seeded);
}
```

- [ ] **Step 3: Track existing reference names in the chosen domain (for dedup)**

The page already loads `domains`. Add a fetch of the chosen domain's references so dedup can compare names. Add state + effect:
```tsx
const [refsInDomain, setRefsInDomain] = useState<StyleReference[]>([]);
useEffect(() => {
  const d = domain.trim();
  if (!d) { setRefsInDomain([]); return; }
  let cancelled = false;
  api.styleReferences(d).then((r) => !cancelled && setRefsInDomain(r.references)).catch(() => {});
  return () => { cancelled = true; };
}, [domain, formKey]);
```
(`StyleReference` is already imported in this file.)

- [ ] **Step 4: Batch analyze with concurrency 3**

```tsx
async function analyzeAll(seeded: RefRow[]) {
  await mapLimit(seeded, 3, async (row) => {
    try {
      const a = await api.analyzeStyleReference({ target: row.file });
      setRows((rs) => rs.map((r) => r.id !== row.id ? r : {
        ...r,
        analysis: a,
        status: a.mode === "source_only" ? "flagged" : (r.flagReason ? "flagged" : "ready"),
        flagReason: a.mode === "source_only" ? "no_translation" : r.flagReason,
        // auto-select only clean, non-duplicate, translatable rows
        selected: a.mode !== "source_only" && !r.flagReason,
      }));
    } catch (e) {
      setRows((rs) => rs.map((r) => r.id !== row.id ? r
        : { ...r, status: "analyze_failed", error: e instanceof Error ? e.message : String(e) }));
    }
  });
}
```

- [ ] **Step 5: Row callbacks**

```tsx
const onToggle = (id: string, selected: boolean) =>
  setRows((rs) => rs.map((r) => r.id === id ? { ...r, selected } : r));
const onRename = (id: string, name: string) =>
  setRows((rs) => rs.map((r) => r.id === id ? { ...r, name } : r));
const onOverride = (id: string, mode: "" | StyleMode) =>
  setRows((rs) => rs.map((r) => r.id === id ? { ...r, modeOverride: mode } : r));
```
(`StyleMode` is already imported in `page.tsx` — no new import.)

- [ ] **Step 6: Render the table when `batch` is true**

Wrap the existing detection-card block so it only renders when `!batch && analysis`. Immediately after it, add:
```tsx
{batch && (
  <div className="flex flex-col gap-3">
    <div className="flex flex-wrap items-center gap-2">
      <span className="label">{rows.length}개 분석</span>
      <span className="slug">
        ✓ {rows.filter((r) => r.selected).length} 선택 ·
        ⚠ {rows.filter((r) => r.flagReason).length} 제외
      </span>
    </div>
    <BatchReviewTable
      rows={rows}
      modeLabel={MODE_LABEL}
      overrideModes={OVERRIDE_MODES}
      onToggle={onToggle}
      onRename={onRename}
      onOverride={onOverride}
      onRetry={onRetry}   // defined in Task 4
    />
  </div>
)}
```

- [ ] **Step 7: Build + browser verify**

Run the build/verify command (expect success). Then restart the prod server, open `http://localhost:3010/app/style`, choose domain `소방`, and select **3 files at once** from `test-pdfs/style-refs/` plus one Korean-only file from `test-pdfs/`. Confirm: table appears (not the single card), rows fill from "분석 중…" to detected mode + counts, the Korean-only row shows "원문만 — 번역 내용 없음" unchecked, a duplicate of an existing 소방 name shows "이미 있음" unchecked, expanding a row shows sample pairs + override + rename. (`onRetry` is added next task; pass a temporary `() => {}` to build this task if needed, then replace.)

- [ ] **Step 8: Commit**

```bash
git add frontend/src/app/app/style/page.tsx
git commit -m "feat(style): multi-file picker, batch analyze (concurrency 3), dedup + triage table"
```

---

## Task 4: Sequential commit with progress, retry, and stop

**Files:**
- Modify: `frontend/src/app/app/style/page.tsx`

- [ ] **Step 1: Commit state**

```tsx
const [committing, setCommitting] = useState(false);
const stopRef = useRef(false);
const [committed, setCommitted] = useState(0);
```

- [ ] **Step 2: Sequential commit loop (one file at a time — Voyage rate limit)**

```tsx
async function commitSelected() {
  const queue = rows.filter((r) => r.selected && (r.status === "ready" || r.status === "commit_failed"));
  if (!queue.length) return;
  stopRef.current = false;
  setCommitting(true);
  setCommitted(0);
  setError(null);
  let ok = 0;
  for (const row of queue) {
    if (stopRef.current) break;
    setRows((rs) => rs.map((r) => r.id === row.id ? { ...r, status: "committing" } : r));
    try {
      await api.uploadStyleReference({
        domain: domain.trim(),
        target: row.file,
        name: row.name || undefined,
        mode: row.modeOverride || undefined,
        sourceLang,
        targetLang,
      });
      ok += 1;
      setCommitted(ok);
      setRows((rs) => rs.map((r) => r.id === row.id ? { ...r, status: "done", selected: false } : r));
    } catch (e) {
      setRows((rs) => rs.map((r) => r.id === row.id
        ? { ...r, status: "commit_failed", error: e instanceof Error ? e.message : String(e) } : r));
    }
  }
  setCommitting(false);
  setMsg(`'${domain.trim()}'에 ${ok}개를 추가했어요.`);
  loadDomains();
  api.styleReferences(domain.trim()).then((r) => setRefsInDomain(r.references)).catch(() => {});
}

function onRetry(id: string) {
  const row = rows.find((r) => r.id === id);
  if (!row) return;
  if (row.status === "analyze_failed") {
    setRows((rs) => rs.map((r) => r.id === id ? { ...r, status: "analyzing", error: undefined } : r));
    void analyzeAll([{ ...row, status: "analyzing" }]);
  } else {
    setRows((rs) => rs.map((r) => r.id === id ? { ...r, selected: true } : r));
    void commitSelected();
  }
}
```

- [ ] **Step 3: Commit/stop controls under the table**

Inside the `{batch && (…)}` block, after `<BatchReviewTable …/>`:
```tsx
<div className="flex flex-wrap items-center gap-3">
  {!committing ? (
    <button className="btn-primary"
      disabled={rows.every((r) => !(r.selected && (r.status === "ready" || r.status === "commit_failed")))}
      onClick={commitSelected}>
      선택한 {rows.filter((r) => r.selected && (r.status === "ready" || r.status === "commit_failed")).length}개 추가
    </button>
  ) : (
    <>
      <span className="slug">추가 중… {committed}/{rows.filter((r) => r.selected || r.status === "committing" || r.status === "done").length}</span>
      <button className="btn-secondary" onClick={() => { stopRef.current = true; }}>중지</button>
    </>
  )}
</div>
```

- [ ] **Step 4: Reset batch state on a fresh pick / domain change**

Ensure `onPickFiles` (Task 3) and the form reset (`setFormKey`) also clear `rows`, `committed`, `committing`. In the existing post-upload reset and when files are cleared, add `setRows([])`.

- [ ] **Step 5: Build + browser verify**

Build, restart, open `/app/style`, domain `소방`, select ~4 files from `test-pdfs/style-refs/`. Click "선택한 N개 추가": rows go ⏳ → ✓ one at a time, counter advances, the 소방 chip count grows. Mid-run click **중지** → stops after the current file. Force a failure (e.g. select many to hit a Voyage timeout) and confirm the row shows ⚠ + [재시도], and [재시도] re-commits just that row. Re-open the domain list and confirm the new references appear once each (no duplicate from retry).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/app/style/page.tsx
git commit -m "feat(style): sequential batch commit with progress, retry, and stop"
```

---

## Task 5: Final verification + polish

- [ ] **Step 1: Full build** — run the build/verify command; confirm clean compile + TypeScript pass.
- [ ] **Step 2: Backend regression** — `cd backend && ./.venv/Scripts/python.exe -m pytest -q` → expect `318 passed, 1 skipped` (no backend change).
- [ ] **Step 3: Browser walkthrough (controller)** — single-file path still shows the detail card unchanged; 2+ files show the triage table; source_only + duplicate auto-excluded; commit/stop/retry all work; counts correct. Capture a screenshot.
- [ ] **Step 4: Confirm local-only files stay uncommitted** — `git status --short` must still show ` M backend/auth.py`, ` M backend/documents_store.py`, ` M frontend/src/proxy.ts` as **unstaged**. Never stage them.

---

## Notes / constraints
- **Do NOT** commit `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts` (local DEV_NO_AUTH bypass), the scratch files (`backend/_ingest_style_refs.py`, `_retry_failed_refs.py`, `_verify_classify.py`), `test-pdfs/`, or screenshots. `git add` only the specific feature files per task.
- **No backend changes** — reuse `analyze` + `references` endpoints; dedup is client-side.
- Known follow-up (out of scope): ingest is not atomic — a mid-commit failure can leave partial/duplicate data; retry can re-add. Tracked separately.
- `BACKEND_URL` must be set at **build** time for the `/api` proxy (baked into routes-manifest.json), not just `next start`.
