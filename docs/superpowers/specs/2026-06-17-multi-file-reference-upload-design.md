# Multi-File Reference Upload — Design

**Date:** 2026-06-17
**Surface:** Style Library (`/app/style`) — `frontend/src/app/app/style/page.tsx`
**Status:** Approved (design); pending implementation plan.

## Goal
Let a user upload **many reference files at once** into one domain, **review/evaluate** each
file's auto-detected format in a compact triage table, and **commit the good ones** with live
progress — without losing the existing single-file experience.

## Context
Today `/app/style` handles **one** file: pick file → `POST /api/style/references/analyze`
(embedding-free) → detection card (mode / counts / sample pairs / [문체][재사용] badges /
format override) → `POST /api/style/references` commits one (embeds via Voyage; backend has
retry/backoff on rate-limit/5xx and timeouts). Translators in practice have **dozens** of past
translations per domain (we ingested 17 소방 HWPs this way, one at a time). Uploading them
individually is tedious, and each file's detected format differs, so the user needs to scan and
vet many detections quickly. Source-only (no-translation) files are already rejected with a
warning.

## Locked decisions (from brainstorming)
1. **Domain:** chosen **once for the whole batch** (no per-file domain). The triage table has no
   domain column.
2. **Review UI:** **status-based triage table** — one row per file; auto-exclude flagged files;
   expand a row for samples + format override; checkbox-select; commit selected.
3. **Commit model:** **sequential commit with live progress** (one file at a time — required by
   Voyage's free-tier rate cap; parallel would worsen 429s). Per-file success/failure; retry.
4. **Mode switch:** **1 file → existing detail card** (keeps "원문 따로 올리기" pairing); **2+ files
   → triage table** (no per-file pairing).
5. **Duplicates:** a file whose derived name already exists in the chosen domain is flagged
   "이미 있음" and **auto-excluded** (user may re-check to add anyway).

## Architecture — frontend-orchestrated, reuse existing endpoints
No backend changes. The page orchestrates the batch over the existing per-file endpoints; the
browser holds the `File` objects between analyze and commit (no server-side temp storage).

- **Analyze (preview):** for each selected file call the existing
  `api.analyzeStyleReference({ target })` with a **concurrency limit of 3**. Rows fill in as
  results return; embedding-free, so it is fast. A failed analyze marks that row "분석 실패 ·
  [다시 분석]".
- **Duplicate check:** on entering batch mode, fetch the domain's references once
  (`api.styleReferences(domain)`); compare each file's derived name against existing names
  client-side. No backend change.
- **Commit:** iterate the **selected** rows **sequentially** (await one before the next),
  calling the existing `api.uploadStyleReference({ domain, target, name, mode })`. The backend's
  timeout-retry/backoff handles rate limiting. Update each row's status live; on failure mark the
  row and offer per-row [재시도]. A **[중지]** stops after the current file.
- *Rejected alternative:* a backend batch endpoint + SSE progress stream. Fewer round-trips but
  adds a new endpoint and SSE plumbing; reusing the tested per-file endpoints is simpler and
  lower-risk, and per-file calls give natural per-row progress for free.

## UX flow
1. **Domain** (existing combobox) + **file input made `multiple`**.
2. **1 file** → unchanged detail card (incl. optional 원문 pairing).
3. **2+ files** → triage table replaces the card:
   - Header summary: `분야 소방 · 17개 분석   ✓14 추가가능   ⚠3 제외`.
   - Row: `☑ | 파일명 | 감지형식 | 짝N·문체N | 상태 | ▸`.
   - Auto-unchecked + reason: `원문만 — 번역없음` (source_only) and `이미 있음` (duplicate).
   - Expand (▸): sample KO→EN pairs + a **형식 수정** dropdown (same OVERRIDE_MODES as single) +
     editable **이름**.
   - Footer: **[ 선택한 N개 추가 ]**.
4. **Commit:** button → progress (`추가 중… 6/14`, bar, per-row ✓/⏳/⚠) + **[중지]**.
5. **Done:** rows show final ✓/⚠; success summary (`소방에 N개 추가`), the domain folder list
   refreshes; failed rows remain with [재시도].

## Components & state
- All in `frontend/src/app/app/style/page.tsx`; extract a `BatchReviewTable` component (and a
  `RefRow` type) to keep the page focused.
- Per-file row state machine: `analyzing → ready | flagged(reason) | analyze_failed`, then on
  commit `queued → committing → done | failed`.
- Selection defaults: `ready` checked; `flagged` (source_only / duplicate) unchecked.
- A small concurrency helper (`mapLimit(items, n, fn)`) for the analyze fan-out.
- Name derivation reused from the single flow (filename → bracketed English name fallback).

## Error handling
- Analyze failure (extraction/HTTP) → row "분석 실패 · [다시 분석]"; never blocks other rows.
- Commit failure (timeout after retries, 4xx) → row ⚠ with the message + [재시도]; batch continues.
- `source_only` is uncommittable by definition → always excluded (cannot be re-checked).
- **Known follow-up (out of scope):** ingest is not atomic — a mid-commit failure can leave
  partial/duplicate data. The backend timeout-retry makes this unlikely; full rollback/idempotency
  is tracked separately.

## Testing
- Frontend unit/component tests with `api.analyzeStyleReference` / `api.uploadStyleReference`
  mocked: rows fill as analyses resolve; source_only and duplicate rows auto-excluded; selecting
  + sequential commit updates progress; a failing commit marks the row and continues; [재시도]
  re-commits one row; [중지] halts after the current file.
- No backend tests needed (no backend change); the existing 318-test suite still applies.

## Out of scope
- Per-file domain assignment; pairing two separate files inside a batch (single-file only).
- Backend batch/SSE endpoint; ingest atomicity fix.
