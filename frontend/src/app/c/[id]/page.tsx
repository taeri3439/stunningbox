"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, LANGS, type TBbox, type THighlight } from "@/lib/api";
import type {
  GlossaryLocation,
  GlossaryTermReport,
  ProperNounReport,
  ResultResponse,
  Segment,
} from "@/lib/types";
import { OnlyOfficeEditor } from "@/components/OnlyOfficeEditor";
import { RetranslatePanel } from "@/components/RetranslatePanel";
import { GlossaryManagePanel } from "@/components/GlossaryManagePanel";
import TranslatedPages from "@/components/TranslatedPages";
import { SegmentPreview } from "@/components/SegmentPreview";
import { ThemeToggle } from "@/components/ThemeToggle";
import { AppHeader } from "@/components/AppHeader";
import { AuthMenu } from "@/components/AuthMenu";
import { PaneLabel } from "@/components/PaneLabel";
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

// Step 3 of 3 — review & edit. Original document on the left, the translation as an
// editable Word-like document in the center (the OnlyOffice editor, editing the .docx
// directly). The .docx on the backend is the source of truth; the Document Server
// autosaves edits via its callback, and "DOCX 내보내기" downloads that file.
export default function ComparePage() {
  const { id } = useParams<{ id: string }>();
  const lang = useSearchParams().get("lang") ?? "en";

  const [doc, setDoc] = useState<ResultResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  /* eslint-disable-next-line @typescript-eslint/no-explicit-any */
  const [connector, setConnector] = useState<any | null>(null);
  const [mode, setMode] = useState<"edit" | "retranslate" | "manage">("edit");
  // Gating an edit→tool switch: true while we force-save + wait for the model to sync, so the
  // tool view opens already showing the edit instead of stale-then-refresh.
  const [syncing, setSyncing] = useState(false);
  const [activeSeg, setActiveSeg] = useState<Segment | null>(null);
  // Bumped after an "적용" so the editor re-inits and reloads the freshly rendered .docx.
  const [editorReloadKey, setEditorReloadKey] = useState(0);
  // The block bookmark ("blk{n}") the editor should open at after an apply.
  const [editorBookmark, setEditorBookmark] = useState<string | null>(null);
  // 용어 관리: bump to refresh preview + re-fetch the glossary report after an apply.
  const [manageKey, setManageKey] = useState(0);
  // 용어 관리: term → translated-page hit boxes (tfind), for the amber/indigo overlays and for
  // resolving a jump click ("#N" chip) to a scroll target without a per-click fetch.
  const [manageHits, setManageHits] = useState<Record<string, TBbox[]>>({});
  // 용어 관리: the bbox TranslatedPages should scroll to (active proper-noun selection or a
  // jump-chip click) — kept as component state (not rebuilt per render) so its identity only
  // changes when the actual target changes.
  const [manageScrollTo, setManageScrollTo] = useState<TBbox | null>(null);
  // 고유명사 검수: terms shared up from the panel (single fetch) so the original pane can
  // outline each flagged proper noun's location(s).
  const [pnReport, setPnReport] = useState<ProperNounReport[]>([]);
  // 고유명사 클릭 → 원본·번역본 동시 강조: the currently selected proper-noun term.
  const [activePnTerm, setActivePnTerm] = useState<ProperNounReport | null>(null);
  const togglePn = (term: ProperNounReport) => {
    const activating = activePnTerm?.source !== term.source;
    setActivePnTerm(activating ? term : null);
    // Scroll the translated pane to this term's first tfind hit (a real user action, so this
    // belongs in the event handler, not a reactive effect that re-derives it from state).
    if (activating) {
      const hits = manageHits[term.translated.trim()];
      if (hits && hits.length) setManageScrollTo(hits[0]);
    }
  };
  // 용어 관리: glossary report so the original (left) pane can outline terms.
  const [glossaryReport, setGlossaryReport] = useState<GlossaryTermReport[]>([]);
  // 재번역: the page now owns the segments list (fetched once when retranslate opens) so
  // the center HTML preview and the panel share one source of truth; bump previewReloadKey
  // to refresh the preview iframe after an applied re-translation.
  const [segments, setSegments] = useState<Segment[]>([]);
  const [previewReloadKey, setPreviewReloadKey] = useState(0);
  // 재번역: translated-page hit boxes for the segments above (bbox lives on the translated
  // page, joined server-side by segment id) — feeds TranslatedPages' hotspot layer.
  const [tSegments, setTSegments] = useState<{ id: string; bbox: TBbox | null }[]>([]);
  // Track the previous mode to detect the edit→tool transition (when the editor unmounted).
  const prevModeRef = useRef<"edit" | "retranslate" | "manage">("edit");

  // Resizable spread: split ratio + dock width + original collapse, persisted to
  // localStorage. SSR renders DEFAULT_LAYOUT (deterministic); the stored value is applied
  // after mount to avoid a hydration mismatch.
  const [layout, setLayout] = useState<ViewerLayout>(DEFAULT_LAYOUT);
  const spreadRef = useRef<HTMLElement>(null);
  // Animate grid-template-columns ONLY during a collapse/expand toggle — never during a
  // resize drag (a transition there would lag the pane behind the cursor).
  const [animating, setAnimating] = useState(false);
  const animTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (animTimer.current) window.clearTimeout(animTimer.current);
    },
    [],
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLayout(loadLayout()); // post-mount: read localStorage and apply stored layout (SSR-safe)
  }, []);
  useEffect(() => {
    saveLayout(layout);
  }, [layout]);

  // 재번역 highlights the matching sentence in the original pane, so entering 재번역 mode
  // auto-reveals the original (the user can still collapse it again afterward).
  useEffect(() => {
    if (mode !== "retranslate") return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLayout((l) => (l.originalCollapsed ? { ...l, originalCollapsed: false } : l));
  }, [mode]);

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

  const toggleOriginal = useCallback(() => {
    setAnimating(true); // enable the column transition just for this toggle
    if (animTimer.current) window.clearTimeout(animTimer.current);
    animTimer.current = window.setTimeout(() => setAnimating(false), 300);
    setLayout((l) => ({ ...l, originalCollapsed: !l.originalCollapsed }));
  }, []);

  useEffect(() => {
    api
      .result(id, lang)
      .then(setDoc)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id, lang]);

  // In 재번역 모드, scroll the original (left) pane to the selected sentence's highlight.
  useEffect(() => {
    if (mode !== "retranslate" || !activeSeg?.source_bbox) return;
    document
      .getElementById("active-source-highlight")
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [activeSeg, mode]);

  // 재번역: fetch the segments when retranslate opens. The page owns them so the center HTML
  // preview and the panel stay in sync.
  useEffect(() => {
    if (mode !== "retranslate" || !doc) return;
    api
      .segments(doc.doc_id, lang)
      .then((r) => setSegments(r.segments))
      .catch(() => setSegments([]));
  }, [mode, doc, lang]);

  // 재번역: fetch the translated-page hit boxes for the hotspots above; re-fetch when
  // previewReloadKey bumps (after a re-render lands) so the boxes track the latest layout.
  useEffect(() => {
    if (mode !== "retranslate" || !doc) return;
    let alive = true;
    api
      .tsegments(doc.doc_id, lang)
      .then((r) => {
        if (alive) setTSegments(r.segments);
      })
      .catch(() => {
        if (alive) setTSegments([]);
      });
    return () => {
      alive = false;
    };
  }, [mode, doc, lang, previewReloadKey]);

  // Bumps the reload keys the tool panes (재번역 preview + 용어 관리/proper-noun report) key
  // their data off. The tool-mode version watcher further down is the sole trigger for this
  // once a tool mode is open — its docx-version comparison is an exact per-version dedup, so
  // no timestamp guard belongs here. requestMode (below) calls this once more, right after
  // the gated edit→tool switch, so the panes open already showing whatever the gate's
  // forcesave just landed instead of waiting for the watcher's next poll tick.
  const bumpToolKeys = useCallback(() => {
    setManageKey((k) => k + 1); // reload manage/proper-noun preview + reports
    setPreviewReloadKey((k) => k + 1); // reload the 재번역 preview iframe
  }, []);

  // Leaving the OnlyOffice editor (edit → a tool mode): the editor saves on unmount and the
  // server syncs that edit into the model asynchronously — the DS disconnect-save can take
  // several seconds. Poll the model after leaving edit so `segments`' translated text (the
  // 재번역 list) tracks a late-landing body-text edit for its ~15.5s window. (Structure/table/
  // figure/formatting edits are out of Phase 1 scope and won't reflect.) This poll only
  // maintains segment text — it never bumps the manage/preview reload keys itself; the
  // tool-mode version watcher below is the single authority for those (keyed on the docx
  // version, so it can't miss a distinct edit and can't double-bump the same one).
  useEffect(() => {
    const prev = prevModeRef.current;
    prevModeRef.current = mode;
    if (prev !== "edit" || mode === "edit" || !doc) return;
    let stop = false;
    let tries = 0;
    let timer: number;
    const tick = async () => {
      if (stop) return;
      try {
        const r = await api.segments(doc.doc_id, lang);
        setSegments(r.segments); // reflect in the 재번역 list (cheap; no iframe reload)
      } catch {
        /* keep polling */
      }
      if (!stop && ++tries < 7) timer = window.setTimeout(tick, 2000);
    };
    timer = window.setTimeout(tick, 1500);
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [mode, doc, lang]);

  // Gate the edit→tool transition: force-save the live editor session, wait (bounded) for the
  // saved .docx's VERSION to change, THEN switch — so 재번역/용어/고유명사 open fresh instead of
  // showing a stale translated pane. Polls the cheap docx-version signal (api.tversion), not
  // segment text: a formatting-only edit (bold, etc.) never changes segment text — that old
  // poll always burned its full window and could never confirm anything — while the version
  // key is exactly what gates the translated pane's own cache, so "version changed" is a
  // direct, correct proxy for "the pane will now be fresh". Every path ends by switching, so
  // it never hangs; if the DS save lands AFTER this gate gives up, the tool-mode watcher below
  // self-corrects.
  async function requestMode(
    target: "edit" | "retranslate" | "manage",
  ) {
    if (mode !== "edit" || target === "edit" || !doc) {
      setMode(target); // tool→edit, tool→tool, or no doc: no gate
      return;
    }
    setSyncing(true);
    // Baseline read in its own try/catch: this GET is just a freshness signal, not the save
    // itself, so a transient failure here must never skip the forcesave call below — that
    // would silently regress to "never force-save", which is the whole point of this gate.
    let v0: string | null = null;
    try {
      v0 = (await api.tversion(doc.doc_id, lang)).version;
    } catch {
      /* no baseline to compare against — fall back to a fixed wait below */
    }
    try {
      const res = await api.forcesave(doc.doc_id, lang);
      // Only error 0 means the DS actually accepted a save worth waiting for. Any other code
      // (4 no unsaved changes, 1 no live session, 6 bad token, -1 DS unreachable, ...) means
      // nothing will be (re)saved, so burning the poll window would just delay the switch.
      if (res.error === 0) {
        if (v0 === null) {
          // The baseline read above failed, so a later version can't be compared against
          // anything — skip version-matching and just give the callback + model-sync a short,
          // fixed head start before switching.
          await new Promise((r) => setTimeout(r, 1200));
        } else {
          // ~12s ceiling: generous because version-polling is cheap and exits as soon as the
          // callback lands (the common case), unlike the old segment-text poll which always
          // spent its whole budget on formatting-only edits.
          for (let i = 0; i < 30; i++) {
            await new Promise((r) => setTimeout(r, 400));
            try {
              const { version } = await api.tversion(doc.doc_id, lang);
              if (version !== null && version !== v0) {
                // One extra tick before switching. This tick alone doesn't close the
                // docx-write → model-sync race — the real margin comes from tsegments/tpages:
                // the tool panes' first read after switching triggers a DS conversion that
                // round-trips for ~1-2s before the model reflects the new .docx, and THAT is
                // what gives _sync_model_from_saved_docx time to finish. This tick is just a
                // small, cheap top-up on top of that round-trip.
                await new Promise((r) => setTimeout(r, 400));
                break;
              }
            } catch {
              /* keep waiting */
            }
          }
        }
      }
    } catch {
      /* forcesave threw → plain switch; the tool-mode watcher will catch up */
    } finally {
      setMode(target);
      bumpToolKeys();
      setSyncing(false);
    }
  }

  // Tool-mode self-correction: while 재번역/용어 is open, poll the cheap docx-version signal so
  // a save that lands AFTER requestMode's gate gave up (or that races it — e.g. the DS's own
  // disconnect-save) still refreshes the translated pane, instead of leaving it stale until the
  // next manual mode switch. No loop hazard: reconversion triggered by the reloadKey bumps below
  // only ever (re)renders the derived PDF/pages from the CURRENT .docx — it never writes
  // output.{lang}.docx itself — so the version this watcher reads stays stable across a refresh
  // it causes, and it can't re-trigger itself.
  const toolVersionRef = useRef<string | null>(null);
  const toolVersionInFlight = useRef(false);
  useEffect(() => {
    if ((mode !== "retranslate" && mode !== "manage") || !doc) return;
    let alive = true;
    let initialized = false;
    const tick = async () => {
      if (!alive || toolVersionInFlight.current) return; // one request at a time
      toolVersionInFlight.current = true;
      try {
        const { version } = await api.tversion(doc.doc_id, lang);
        if (!alive) return;
        if (!initialized) {
          initialized = true; // baseline on entering the mode — never counts as a change
          toolVersionRef.current = version;
        } else if (version !== null && version !== toolVersionRef.current) {
          // Ref update and bump happen together, unconditionally: this comparison is an
          // exact per-version dedup (can't double-bump the version just recorded, can't miss
          // a distinct one landing before the next tick), so this is the sole trigger for
          // manage/preview reload-key bumps once a tool mode is open — no extra guard here.
          toolVersionRef.current = version;
          bumpToolKeys();
          if (mode === "retranslate") {
            // Also refresh the 재번역 list's segment text, mirroring the mode-entry fetch
            // above — closes the gap for a save landing after the legacy poll's ~15.5s
            // window has already ended. Fire-and-forget: doesn't hold the in-flight guard,
            // which only serializes the tversion fetch itself.
            api
              .segments(doc.doc_id, lang)
              .then((r) => {
                if (alive) setSegments(r.segments);
              })
              .catch(() => {
                /* keep the current list; the next version change will retry */
              });
          }
        }
      } catch {
        /* keep polling */
      } finally {
        toolVersionInFlight.current = false;
      }
    };
    tick(); // establish the baseline right away
    const interval = window.setInterval(tick, 4000);
    return () => {
      alive = false;
      window.clearInterval(interval);
    };
  }, [mode, doc, lang, bumpToolKeys]);

  // 용어 관리: load the report so the original (left) pane can outline glossary terms.
  useEffect(() => {
    if (mode !== "manage") return;
    let alive = true;
    api
      .glossaryReport(id, lang)
      .then((r) => {
        if (alive) setGlossaryReport(r.terms);
      })
      .catch(() => {
        if (alive) setGlossaryReport([]);
      });
    return () => {
      alive = false;
    };
  }, [mode, id, lang, manageKey]);

  // 용어 관리: locate every glossary/proper-noun target string on the translated pages in one
  // batched tfind call (api.tfind itself chunks into ≤50-term backend requests). Feeds both
  // the amber/indigo overlays and jump-chip scroll lookups.
  useEffect(() => {
    if (mode !== "manage" || !doc) return;
    // pn candidate terms first: not a resilience mechanism — api.tfind's per-batch fetches run
    // via Promise.all (all-or-nothing) and the .catch below clears every hit, amber and indigo
    // alike, if any batch fails. This ordering is a harmless priority convention: it keeps pn
    // terms within the backend's per-request 50-term cap (tfind's terms[:50] in main.py) should
    // a future caller ever skip api.tfind's chunking and hit the endpoint directly.
    const targets = new Set<string>();
    for (const t of pnReport) if (t.translated.trim()) targets.add(t.translated.trim());
    for (const t of glossaryReport) if (t.target.trim()) targets.add(t.target.trim());
    // No terms to look up → resolve to an empty map via the same .then() path as a real fetch
    // (rather than setState synchronously in the effect body).
    const req = targets.size > 0 ? api.tfind(doc.doc_id, lang, Array.from(targets)) : Promise.resolve({ hits: {} });
    let alive = true;
    req
      .then((r) => {
        if (alive) setManageHits(r.hits);
      })
      .catch(() => {
        if (alive) setManageHits({});
      });
    return () => {
      alive = false;
    };
  }, [mode, doc, lang, manageKey, glossaryReport, pnReport]);

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

  // 재번역: the active segment's translated-page bbox (from tSegments) — memoized so its
  // identity only changes when the active segment or the fetched boxes actually change,
  // not on every unrelated re-render (TranslatedPages' scroll effect keys off this reference).
  const retranslateScrollTo = useMemo<TBbox | null>(() => {
    if (!activeSeg) return null;
    return tSegments.find((s) => s.id === activeSeg.id)?.bbox ?? null;
  }, [tSegments, activeSeg]);

  // 용어 관리: amber = every glossary target term's hits; indigo = the active proper noun's
  // hits (excluded from amber so it isn't double-drawn). Memoized on the same grounds as above.
  const manageHighlights = useMemo<THighlight[]>(() => {
    const activeTarget = activePnTerm?.translated.trim() || null;
    const amberBoxes: TBbox[] = [];
    const seen = new Set<string>();
    for (const t of glossaryReport) {
      const term = t.target.trim();
      if (!term || term === activeTarget || seen.has(term)) continue;
      seen.add(term);
      const hits = manageHits[term];
      if (hits) amberBoxes.push(...hits);
    }
    const indigoBoxes = activeTarget ? manageHits[activeTarget] ?? [] : [];
    const out: THighlight[] = [];
    if (amberBoxes.length) out.push({ color: "amber", boxes: amberBoxes });
    if (indigoBoxes.length) out.push({ color: "indigo", boxes: indigoBoxes });
    return out;
  }, [manageHits, activePnTerm, glossaryReport]);

  // 용어 관리: resolve a jump-chip's location to the term it belongs to (value-matched against
  // the reports we already hold — GlossaryManagePanel/ProperNounCandidates fetch their own copy
  // of the same data, so we can't match by reference), so the click can look up its tfind hit.
  function findJumpTerm(loc: GlossaryLocation): string | null {
    const sameSpot = (l: GlossaryLocation) =>
      l.segment_index === loc.segment_index && l.block_id === loc.block_id;
    const term = glossaryReport.find((t) => t.locations.some(sameSpot));
    // Trim: manageHits' keys are built from trimmed strings (see the tfind-batching effect
    // above), so an untrimmed target/translated with stray whitespace would silently fail to
    // look up its hit and the jump would no-op.
    if (term) return term.target.trim();
    const pn = pnReport.find((t) => t.locations.some(sameSpot));
    return pn ? pn.translated.trim() : null;
  }

  if (error) {
    return (
      <div className="flex flex-1 items-center justify-center p-12 text-center">
        <div>
          <p className="text-base" style={{ color: "var(--danger)" }}>
            오류 — {error}
          </p>
          <Link href="/app" className="btn-secondary mt-6 inline-flex">
            처음으로
          </Link>
        </div>
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="flex flex-1 items-center justify-center p-12">
        <p className="label" style={{ color: "var(--text-3)" }}>불러오는 중…</p>
      </div>
    );
  }

  // Only the PDF (vision) path carries a coverage/gate report; office & HWP ingest report
  // a lightweight {ingest, blocks} summary with no spans. Narrow before touching gate fields.
  const gate =
    doc.report && "missing_span_ids" in doc.report ? doc.report : null;
  const gateOk = gate ? gate.missing_span_ids.length === 0 : false;
  const missing = gate ? gate.missing_span_ids.length : 0;

  // Render both panes' pages at the source PDF's physical size so they match.
  const pageVars = {
    "--page-w-mm": String(doc.page_size?.width_mm ?? 210),
    "--page-h-mm": String(doc.page_size?.height_mm ?? 297),
  } as CSSProperties;

  const pageAspect = doc.page_size
    ? doc.page_size.width_mm / doc.page_size.height_mm
    : undefined;

  return (
    <div className="flex flex-1 flex-col min-h-0" style={pageVars}>
      {/* top bar — identity + status (left) · tools + actions (right), one row that
          wraps on narrow screens. Replaces the old masthead + action-bar stack. */}
      <AppHeader
        logoHref="/"
        dense
        lead={
          <>
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
          </>
        }
        actions={
          <div className="flex flex-wrap items-center justify-end gap-x-2.5 gap-y-2">
          {/* zone 1 — editor view switches + autosave status */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              className={mode === "edit" ? "btn-primary" : "btn-secondary"}
              onClick={() => requestMode("edit")}
              title="문서 편집기로 돌아가기"
            >
              문서 편집
            </button>
            <button
              className={mode === "retranslate" ? "btn-primary" : "btn-secondary"}
              onClick={() => requestMode(mode === "retranslate" ? "edit" : "retranslate")}
            >
              재번역
            </button>
            <button
              className={mode === "manage" ? "btn-primary" : "btn-secondary"}
              onClick={() => requestMode(mode === "manage" ? "edit" : "manage")}
            >
              용어
            </button>
            <span className="stamp hidden lg:inline-flex">
              <span className="stamp__dot" />
              저장됨
            </span>
          </div>

          {/* divider — only on xl, where the bar fits on one row */}
          <span aria-hidden className="hidden h-5 w-px bg-border xl:block" />

          {/* zone 2 — app chrome */}
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <AuthMenu />
          </div>

          {/* divider */}
          <span aria-hidden className="hidden h-5 w-px bg-border xl:block" />

          {/* zone 3 — document output */}
          <div className="flex items-center gap-2">
            <Link href="/app" className="btn-secondary">
              새 문서
            </Link>
            <a href={api.downloadUrl(doc.doc_id, lang)} className="btn-primary">
              DOCX 내보내기
            </a>
          </div>
        </div>
        }
      />

      {/* the spread — at lg+ a resizable grid: original │ main │ [tool dock].
          Below lg it stacks (flex-col); handles hide and resizing is off. */}
      <section
        ref={spreadRef}
        className={`reveal reveal-3 flex min-h-0 flex-1 flex-col lg:grid${
          animating ? " viewer-spread-anim" : ""
        }`}
        // grid-template-columns is runtime-computed (resize ratio / collapse) → set inline.
        // Ignored below lg (flex-col); `lg:grid` turns the grid on. At lg the tracks apply.
        style={{ gridTemplateColumns: gridColumns(layout, mode !== "edit") }}
      >
        {/* original — collapses at lg to a thin rail (not 0) so the reopen control stays
            where the pane lives, instead of jumping to the top bar. Content stays mounted
            (lg:hidden) so reopening doesn't reload the iframe/images. */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-none">
          {layout.originalCollapsed && !animating && (
            <div className="hidden min-h-0 flex-col items-center gap-3 border-r border-border pt-3 lg:flex">
              <button
                onClick={toggleOriginal}
                className="icon-btn inline-flex items-center justify-center"
                title="원본 펼치기"
                aria-label="원본 펼치기"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="m9 18 6-6-6-6" />
                </svg>
              </button>
              <span
                aria-hidden
                className="font-semibold [writing-mode:vertical-rl]"
                style={{ fontSize: "var(--fs-caption)", color: "var(--text-3)", letterSpacing: "0.1em" }}
              >
                원본
              </span>
            </div>
          )}
          <div
            className={`flex min-h-0 flex-1 flex-col px-4 pt-3${
              layout.originalCollapsed && !animating ? " lg:hidden" : ""
            }`}
          >
            <div className="mb-2 flex items-center justify-between px-1">
            <PaneLabel title="원본" lang={LANGS[doc.source_lang] ?? doc.source_lang} tone="source" />
            <button
              onClick={toggleOriginal}
              className="icon-btn hidden items-center justify-center lg:inline-flex"
              title="원본 접기"
              aria-label="원본 접기"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="m15 18-6-6 6-6" />
              </svg>
            </button>
          </div>
          {doc.source_html ? (
            mode === "retranslate" ? (
              <SegmentPreview
                src={api.sourcePreviewUrl(doc.doc_id)}
                activeSeg={activeSeg?.id ?? null}
                onSelectSeg={(id) => setActiveSeg(segments.find((s) => s.id === id) ?? null)}
                reloadKey={0}
                jumpBlock={activeSeg?.block_id ?? null}
              />
            ) : (
              <iframe
                src={doc.source_html}
                title={`원본 — ${doc.title ?? doc.doc_id}`}
                className="sheet min-h-0 w-full flex-1 bg-white"
              />
            )
          ) : (
            <div className="min-h-0 flex-1 overflow-auto">
              {doc.page_images.map((name, i) => (
                <figure
                  key={name}
                  id={`pn-page-${i}`}
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
                      segments
                        .filter((s) => s.source_bbox && s.source_bbox.page === i)
                        .map((s) => {
                          const b = s.source_bbox!;
                          const isActive = activeSeg?.id === s.id;
                          return (
                            <button
                              key={`hot-${s.index}`}
                              id={isActive ? "active-source-highlight" : undefined}
                              type="button"
                              aria-label={`문장 ${s.index + 1} 선택`}
                              onClick={() => setActiveSeg(s)}
                              className="absolute rounded-[3px]"
                              style={{
                                left: `${b.x * 100}%`,
                                top: `${b.y * 100}%`,
                                width: `${b.w * 100}%`,
                                height: `${b.h * 100}%`,
                                background: isActive ? "var(--primary-soft)" : "transparent",
                                outline: isActive ? "1.5px solid var(--primary)" : "none",
                                cursor: "pointer",
                              }}
                            />
                          );
                        })}
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
                  </div>
                  <figcaption className="mt-1 text-center text-[var(--fs-caption)] opacity-50">
                    {i + 1}
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
          </div>
        </div>

        <ResizeHandle onDelta={onMainDelta} ariaLabel="원본·번역본 폭 조절" />

        {/* main: editor (edit/retranslate) or highlighted preview (manage) — headed by a
            번역본 label that mirrors the original's, marking this column as the translation. */}
        <div className="flex min-h-0 flex-1 flex-col lg:flex-none">
          <div className="mb-2 flex items-center justify-between px-5 pt-3">
            <PaneLabel title="번역본" lang={LANGS[lang] ?? lang} tone="target" />
            <span className="slug hidden lg:inline">
              {mode === "manage" ? "용어 검수" : "편집 가능"}
            </span>
          </div>
          <div className="relative flex min-h-0 flex-1 flex-col">
            {mode === "manage" ? (
              <TranslatedPages
                docId={doc.doc_id}
                lang={lang}
                reloadKey={manageKey}
                highlights={manageHighlights}
                scrollTo={manageScrollTo}
              />
            ) : mode === "retranslate" ? (
              <TranslatedPages
                docId={doc.doc_id}
                lang={lang}
                reloadKey={previewReloadKey}
                segments={tSegments}
                activeSeg={activeSeg?.id ?? null}
                onSelectSeg={(segId) => setActiveSeg(segments.find((s) => s.id === segId) ?? null)}
                scrollTo={retranslateScrollTo}
              />
            ) : (
              <OnlyOfficeEditor
                docId={doc.doc_id}
                lang={lang}
                onConnector={setConnector}
                reloadKey={editorReloadKey}
                bookmark={editorBookmark}
              />
            )}
            {syncing && (
              <div
                className="absolute inset-0 z-10 flex items-center justify-center gap-2"
                style={{ background: "color-mix(in srgb, var(--surface) 72%, transparent)" }}
                aria-live="polite"
              >
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
                      style={{ color: "var(--primary)" }} />
                <span className="text-sm" style={{ color: "var(--primary)" }}>변경사항 반영 중…</span>
              </div>
            )}
          </div>
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
                  className="icon-btn ml-auto inline-flex items-center justify-center"
                  title="도구 닫기"
                  aria-label="도구 닫기"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                {mode === "retranslate" ? (
                  <RetranslatePanel
                    docId={doc.doc_id}
                    lang={lang}
                    segments={segments}
                    activeSeg={activeSeg}
                    onSelect={setActiveSeg}
                    onApplied={(bm) => {
                      setEditorBookmark(bm);
                      setEditorReloadKey((k) => k + 1);
                      // The translated pages are rendered (via the DS ConvertService) from the
                      // .docx that api.render() just wrote — only NOW is a re-fetch guaranteed
                      // to see the new content, so this bump (not the earlier one below) is what
                      // actually refreshes TranslatedPages after an apply.
                      setPreviewReloadKey((k) => k + 1);
                    }}
                    onSegmentText={(index, text) => {
                      setSegments((prev) => prev.map((s) => (s.index === index ? { ...s, translated: text } : s)));
                      setPreviewReloadKey((k) => k + 1); // reflect the new text in the segment list right away
                    }}
                  />
                ) : (
                  <GlossaryManagePanel
                    docId={doc.doc_id}
                    lang={lang}
                    reloadKey={manageKey}
                    onPnTermsLoaded={setPnReport}
                    activePnSource={activePnTerm?.source ?? null}
                    onActivatePn={togglePn}
                    onJump={(loc) => {
                      // scroll the translated pane to that term's first tfind hit.
                      const term = findJumpTerm(loc);
                      const hits = term ? manageHits[term] : undefined;
                      if (hits && hits.length) setManageScrollTo(hits[0]);
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
              </div>
            </aside>
          </>
        )}
      </section>
    </div>
  );
}
