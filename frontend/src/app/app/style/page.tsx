"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import {
  api,
  LANGS,
  type StyleAnalysis,
  type StyleDomain,
  type StyleMode,
  type StyleReference,
} from "@/lib/api";
import { AppHeader } from "@/components/AppHeader";
import { FilePicker } from "@/components/FilePicker";
import { BatchReviewTable, type RefRow } from "./BatchReviewTable";
import { PairListModal } from "./PairListModal";
import { deriveName, mapLimit } from "@/lib/batchUpload";

const LANG_OPTIONS = Object.entries(LANGS).map(([k, v]) => (
  <option key={k} value={k}>
    {v}
  </option>
));

const MODE_LABEL: Record<StyleMode, string> = {
  interleaved: "교차 이중언어 (원문↔번역 번갈아)",
  columns: "2열 표 / CSV",
  sequential: "순차 이중언어 (원문 후 번역)",
  paired_files: "원문 + 번역 2파일",
  target_only: "번역본만 (문체만)",
  source_only: "원문만 — 번역본 없음",
};
// Modes a user might correct a single-file auto-detection to.
const OVERRIDE_MODES: StyleMode[] = ["interleaved", "sequential", "columns", "target_only"];

// 스타일 라이브러리 / 번역 메모리: upload past translations into domain folders so future
// translations adopt their style (and, when the original is provided too, reuse exact matches).
export default function StyleLibraryPage() {
  const [domains, setDomains] = useState<StyleDomain[]>([]);
  const [selected, setSelected] = useState("");
  const [refs, setRefs] = useState<StyleReference[]>([]);

  // upload form
  const [domain, setDomain] = useState("");
  const [name, setName] = useState("");
  const [target, setTarget] = useState<File | null>(null);
  const [source, setSource] = useState<File | null>(null);
  const [showSource, setShowSource] = useState(false); // reveal the optional 원문 slot
  const [domainOpen, setDomainOpen] = useState(false);  // existing-set dropdown
  const [domainActive, setDomainActive] = useState(-1); // keyboard-highlighted option (aria-activedescendant)
  const domainBox = useRef<HTMLDivElement>(null);
  const domainInputRef = useRef<HTMLInputElement>(null);
  const domainListId = useId();
  const domainOptId = (i: number) => `${domainListId}-opt-${i}`;
  const [sourceLang, setSourceLang] = useState("ko");
  const [targetLang, setTargetLang] = useState("en");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [formKey, setFormKey] = useState(0); // bump to reset file inputs after upload
  const [analysis, setAnalysis] = useState<StyleAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [modeOverride, setModeOverride] = useState<string>("");

  // batch mode (2+ files): triage table that replaces the single-file detection card
  const [rows, setRows] = useState<RefRow[]>([]);
  const batch = rows.length > 0;
  const [refsInDomain, setRefsInDomain] = useState<StyleReference[]>([]);
  const [committing, setCommitting] = useState(false);
  const stopRef = useRef(false);
  const [committed, setCommitted] = useState(0);
  const [commitTotal, setCommitTotal] = useState(0);

  // "전체 N쌍 보기" — one page-owned modal shared by the single-file card + batch rows.
  const [pairsModal, setPairsModal] = useState<{ file: File; source: File | null; mode: "" | StyleMode } | null>(null);

  // Auto-detect the reference format whenever the chosen file(s) change (no storage/embedding).
  useEffect(() => {
    if (!target) {
      setAnalysis(null);
      setModeOverride("");
      return;
    }
    let cancelled = false;
    setAnalyzing(true);
    setModeOverride("");   // new file context → back to auto-detect (don't carry a stale override)
    api
      .analyzeStyleReference({ target, source })
      .then((a) => !cancelled && setAnalysis(a))
      .catch(() => !cancelled && setAnalysis(null))
      .finally(() => !cancelled && setAnalyzing(false));
    return () => {
      cancelled = true;
    };
  }, [target, source]);

  // Load existing reference names in the chosen set (for batch-mode dedup).
  useEffect(() => {
    const d = domain.trim();
    if (!d) {
      setRefsInDomain([]);
      return;
    }
    let cancelled = false;
    api.styleReferences(d).then((r) => !cancelled && setRefsInDomain(r.references)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [domain, formKey]);

  // Pick handler: 1 file → existing single-file card; 2+ files → batch triage table.
  function onPickFiles(files: File[]) {
    if (files.length <= 1) {
      setRows([]); // single-file path: existing detection card
      setTarget(files[0] ?? null);
      return;
    }
    setTarget(null); // 2+ files: batch table replaces the card
    const existing = new Set(refsInDomain.map((r) => r.name));
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

  // Batch analyze with concurrency 3; auto-excludes source_only + duplicate-name rows.
  async function analyzeAll(seeded: RefRow[]) {
    await mapLimit(seeded, 3, async (row) => {
      try {
        const a = await api.analyzeStyleReference({ target: row.file });
        setRows((rs) =>
          rs.map((r) =>
            r.id !== row.id
              ? r
              : {
                  ...r,
                  analysis: a,
                  status: a.mode === "source_only" ? "flagged" : r.flagReason ? "flagged" : "ready",
                  flagReason: a.mode === "source_only" ? "no_translation" : r.flagReason,
                  selected: a.mode !== "source_only" && !r.flagReason,
                },
          ),
        );
      } catch (e) {
        setRows((rs) =>
          rs.map((r) =>
            r.id !== row.id
              ? r
              : { ...r, status: "analyze_failed", error: e instanceof Error ? e.message : String(e) },
          ),
        );
      }
    });
  }

  const onToggle = (id: string, selected: boolean) =>
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, selected } : r)));
  const onRename = (id: string, name: string) =>
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, name } : r)));
  const onOverride = (id: string, mode: "" | StyleMode) =>
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, modeOverride: mode } : r)));

  // Commit selected rows one at a time (Voyage rate limit), with live progress + stop.
  async function commitSelected(ids?: string[]) {
    if (committing) return;   // ignore re-entrant calls (e.g. a row retry while a batch commit is running)
    const queue = rows.filter((r) =>
      (ids ? ids.includes(r.id) : r.selected) && (r.status === "ready" || r.status === "commit_failed"));
    if (!queue.length) return;
    stopRef.current = false;
    setCommitting(true);
    setCommitted(0);
    setCommitTotal(queue.length);
    setError(null);
    let ok = 0;
    for (const row of queue) {
      if (stopRef.current) break;
      setRows((rs) => rs.map((r) => (r.id === row.id ? { ...r, status: "committing" } : r)));
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
        setRows((rs) => rs.map((r) => (r.id === row.id ? { ...r, status: "done", selected: false } : r)));
      } catch (e) {
        setRows((rs) =>
          rs.map((r) =>
            r.id === row.id
              ? { ...r, status: "commit_failed", error: e instanceof Error ? e.message : String(e) }
              : r,
          ),
        );
      }
    }
    setCommitting(false);
    setMsg(stopRef.current
      ? `중지했어요 — ${ok}개까지 추가했어요.`
      : `'${domain.trim()}'에 ${ok}개를 추가했어요.`);
    loadDomains();
    api.styleReferences(domain.trim()).then((r) => setRefsInDomain(r.references)).catch(() => {});
  }

  function onRetry(id: string) {
    const row = rows.find((r) => r.id === id);
    if (!row) return;
    if (row.status === "analyze_failed") {
      setRows((rs) => rs.map((r) => (r.id === id ? { ...r, status: "analyzing", error: undefined } : r)));
      void analyzeAll([{ ...row, status: "analyzing" }]);
    } else {
      setRows((rs) => rs.map((r) => (r.id === id ? { ...r, selected: true } : r)));
      void commitSelected([id]);   // commit exactly this row, even if it was deselected
    }
  }

  // Close the set dropdown on an outside click.
  useEffect(() => {
    if (!domainOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (domainBox.current && !domainBox.current.contains(e.target as Node)) {
        setDomainOpen(false);
        setDomainActive(-1);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [domainOpen]);

  // Keep the keyboard-highlighted set option scrolled into view.
  useEffect(() => {
    if (!domainOpen || domainActive < 0) return;
    document.getElementById(domainOptId(domainActive))?.scrollIntoView({ block: "nearest" });
    // domainOptId is derived from the stable useId — safe to omit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domainActive, domainOpen]);

  // Open the set dropdown, pre-highlighting the currently chosen set (if any).
  function openDomainList() {
    setDomainOpen(true);
    const i = domains.findIndex((d) => d.domain === domain);
    setDomainActive(i);
  }

  // Commit a set choice and return focus to the input (aria-activedescendant model).
  function chooseDomain(value: string) {
    setDomain(value);
    setDomainOpen(false);
    setDomainActive(-1);
    domainInputRef.current?.focus();
  }

  // Editable-combobox keyboard model: ↑/↓ move the active option, Enter selects, Esc closes.
  function onDomainKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!domainOpen) return openDomainList();
      if (domains.length) setDomainActive((i) => (i + 1) % domains.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!domainOpen) return openDomainList();
      if (domains.length) setDomainActive((i) => (i <= 0 ? domains.length - 1 : i - 1));
    } else if (e.key === "Enter") {
      if (domainOpen && domainActive >= 0 && domainActive < domains.length) {
        e.preventDefault();
        chooseDomain(domains[domainActive].domain);
      }
    } else if (e.key === "Escape") {
      if (domainOpen) {
        e.preventDefault();
        setDomainOpen(false);
        setDomainActive(-1);
      }
    }
  }

  const loadDomains = useCallback(() => {
    api
      .styleDomains()
      .then((d) => setDomains(d.domains))
      .catch(() => setDomains([]));
  }, []);

  useEffect(() => {
    loadDomains();
  }, [loadDomains]);

  useEffect(() => {
    if (!selected) {
      setRefs([]);
      return;
    }
    api
      .styleReferences(selected)
      .then((r) => setRefs(r.references))
      .catch(() => setRefs([]));
  }, [selected, formKey]);

  async function onUpload() {
    const dom = domain.trim();
    if (!dom) return setError("레퍼런스 세트를 입력하세요 (예: 소방, 의료).");
    if (!target) return setError("과거 번역 파일을 선택하세요.");
    if (analysis?.mode === "source_only")
      return setError(analysis.message ?? "번역 내용이 없는 파일이에요 — 레퍼런스로 쓰려면 원문과 번역이 함께 있는 파일을 올려주세요.");
    setError(null);
    setMsg(null);
    setBusy(true);
    try {
      const res = await api.uploadStyleReference({
        domain: dom,
        target,
        source,
        sourceLang,
        targetLang,
        name: name.trim() || undefined,
        mode: modeOverride || undefined,
      });
      const tmMsg = res.tm?.indexed
        ? ` · 번역 메모리 ${res.tm.pairs}쌍도 함께 저장했어요.`
        : res.tm?.error
          ? " · 다만 번역 메모리(TM) 색인은 일시 오류로 실패했어요 — 다시 추가하면 채워집니다."
          : "";
      setMsg(`'${dom}'에 ${res.segment_count}개 문장을 추가했어요.${tmMsg}`);
      setName("");
      setTarget(null);
      setSource(null);
      setShowSource(false);
      setAnalysis(null);       // clear the detection card + override explicitly
      setModeOverride("");
      setRows([]);             // clear any batch state too
      setCommitted(0);
      setCommitTotal(0);
      setCommitting(false);
      setFormKey((k) => k + 1);
      setSelected(dom);
      loadDomains();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(refId: string) {
    try {
      await api.deleteStyleReference(refId);
      setFormKey((k) => k + 1); // reload refs
      loadDomains();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="flex flex-1 flex-col min-h-0">
      <AppHeader
        logoHref="/app"
        lead={
          <p className="mt-2 text-base text-text-2">
            과거에 직접 번역한 문서를 레퍼런스 세트로 모아두면, 새 문서를 번역할 때 같은 말투·용어를 따라갑니다
          </p>
        }
        actions={
          <Link
            href="/app"
            className="rounded-full border border-border-strong px-3 py-1.5 text-sm font-semibold text-text-2 transition-colors hover:text-primary"
          >
            ← 업로드로
          </Link>
        }
      />

      <main className="flex-1 overflow-auto px-6 py-10 sm:px-10">
        <section className="reveal reveal-2 mx-auto w-full max-w-3xl">
          <span className="eyebrow">세트별 문체 학습</span>
          <h2 className="mt-3 mb-6 font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.15 }}>
            스타일 <span className="text-gradient">라이브러리</span>
          </h2>

          {/* upload form */}
          <div className="ticket flex flex-col gap-5 p-6 sm:p-7" key={formKey}>
            <p className="slug">
              레퍼런스 세트는 번역물을 모아두는 묶음이에요. 같은 세트(예: 소방)에 모아두면, 그 세트의 문서를 번역할 때 함께 참고합니다.
            </p>
            <div className="flex flex-wrap gap-x-5 gap-y-4">
              <div className="flex min-w-[12rem] flex-1 flex-col gap-1.5" ref={domainBox}>
                <span className="label">레퍼런스 세트</span>
                <div className="relative">
                  <input
                    ref={domainInputRef}
                    className="field w-full pr-9"
                    placeholder="기존 세트 선택 또는 새로 입력"
                    value={domain}
                    role="combobox"
                    aria-expanded={domainOpen}
                    aria-controls={domainListId}
                    aria-autocomplete="list"
                    aria-activedescendant={
                      domainOpen && domainActive >= 0 && domainActive < domains.length
                        ? domainOptId(domainActive)
                        : undefined
                    }
                    onChange={(e) => {
                      setDomain(e.target.value);
                      setDomainOpen(true);
                      setDomainActive(-1);
                    }}
                    onFocus={() => setDomainOpen(true)}
                    onKeyDown={onDomainKeyDown}
                  />
                  <button
                    type="button"
                    aria-label="기존 세트 보기"
                    aria-expanded={domainOpen}
                    aria-controls={domainListId}
                    tabIndex={-1}
                    onClick={() => {
                      if (domainOpen) {
                        setDomainOpen(false);
                        setDomainActive(-1);
                      } else {
                        openDomainList();
                        domainInputRef.current?.focus();
                      }
                    }}
                    className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-text-3 transition-colors hover:text-primary"
                  >
                    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                      <path d="M2.5 4.5L6 8l3.5-3.5" fill="none" stroke="currentColor"
                        strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                  {domainOpen && (
                    <ul
                      id={domainListId}
                      role="listbox"
                      aria-label="레퍼런스 세트"
                      className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-[var(--r-md)] border border-border p-1 shadow-lg"
                      style={{ background: "var(--surface)" }}
                    >
                      {domains.length === 0 ? (
                        <li className="slug px-3 py-2">아직 만든 레퍼런스 세트가 없어요. 직접 입력해 새로 만드세요.</li>
                      ) : (
                        domains.map((d, i) => {
                          const isSelected = domain === d.domain;
                          const isActive = domainActive === i;
                          return (
                            <li
                              key={d.domain}
                              id={domainOptId(i)}
                              role="option"
                              aria-selected={isSelected}
                              onMouseDown={(e) => e.preventDefault()}
                              onMouseEnter={() => setDomainActive(i)}
                              onClick={() => chooseDomain(d.domain)}
                              className={`flex cursor-pointer items-center justify-between gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
                                isActive ? "bg-surface-2" : ""
                              }`}
                              style={
                                isSelected
                                  ? { background: "var(--primary-soft)", color: "var(--primary)" }
                                  : undefined
                              }
                            >
                              <span className="font-semibold">{d.domain}</span>
                              <span className="text-text-3">{d.references}건 · {d.segments}문장</span>
                            </li>
                          );
                        })
                      )}
                    </ul>
                  )}
                </div>
              </div>
              <label className="flex min-w-[12rem] flex-1 flex-col gap-1.5">
                <span className="label">레퍼런스 이름 (선택)</span>
                <input
                  className="field"
                  placeholder="예: 2024 소방 성능인증 기준"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
            </div>

            {/* 과거 번역 파일 — multi-select to support batch path */}
            <div className="flex flex-col gap-2">
              <span className="label">과거 번역 파일 (필수)</span>
              <span className="slug">
                원문과 번역이 한 파일에 같이 있어도 그대로 올리면 됩니다 — 형식은 자동으로 감지해요.
              </span>
              <FilePicker
                multiple
                accept=".pdf,.docx,.doc,.hwp,.txt,.csv,.md"
                onFile={onPickFiles}
                file={target ? [target] : rows.length > 0 ? rows.map((r) => r.file) : []}
                disabled={committing}
              />
            </div>

            {showSource ? (
              <div className="flex flex-col gap-2">
                <span className="label">원문 파일 (선택)</span>
                <span className="slug">
                  원문이 별도 파일로 있을 때만 추가하세요. 같은 문장이 다시 나오면 이 번역을 그대로 재사용합니다.
                </span>
                <FilePicker
                  accept=".pdf,.docx,.doc,.hwp,.txt,.csv,.md"
                  onFile={setSource}
                  file={source}
                />
              </div>
            ) : (
              <div className="flex flex-col gap-1">
                <button
                  type="button"
                  onClick={() => setShowSource(true)}
                  className="self-start text-sm font-semibold transition-colors hover:opacity-80"
                  style={{ color: "var(--primary)", fontSize: "var(--fs-label)" }}
                >
                  + 원문 파일 따로 올리기 (선택)
                </button>
                <span className="slug">
                  원문도 함께 올리면 같은 문장이 다시 나올 때 이전 번역을 그대로 재사용해요.
                </span>
              </div>
            )}

            {/* auto-detection preview (single-file path) */}
            {!batch && analyzing && <p className="slug">파일 분석 중…</p>}
            {!batch && analysis && (
              <div className="rounded-[var(--r-md)] border border-border p-4" style={{ background: "var(--surface-2)" }}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="label">감지된 형식:</span>
                  <span className="font-semibold">{MODE_LABEL[analysis.mode]}</span>
                  {analysis.mode !== "source_only" && (
                    <span className="ml-1 text-sm text-text-3">
                      문장 {analysis.style_count}개
                      {analysis.has_pairs &&
                        ` · 원문↔번역 짝 ${analysis.pair_count}개${analysis.estimated ? " (예상)" : ""}`}
                    </span>
                  )}
                  {analysis.mode !== "source_only" && (
                    <span className="ml-auto flex gap-1.5">
                      <span className="chip-soft">문체</span>
                      {analysis.has_pairs && (
                        <span className="chip-ok"
                          title="같은 원문이 다시 나오면 이전 번역을 그대로 재사용합니다">
                          재사용
                        </span>
                      )}
                    </span>
                  )}
                </div>
                {analysis.mode === "source_only" ? (
                  <p className="mt-2">
                    <span className="stamp stamp--alert">
                      <span className="stamp__dot" aria-hidden="true" />
                      {analysis.message ?? "번역 내용이 없는 파일이에요 — 레퍼런스로 쓰려면 원문과 번역이 함께 있는 파일을 올려주세요."}
                    </span>
                  </p>
                ) : (
                  <>
                    {analysis.sample_pairs.length > 0 && (
                      <ul className="mt-2 flex flex-col gap-2">
                        {analysis.sample_pairs.map((p, i) => (
                          <li key={i} className="slug flex flex-col gap-0.5 break-words">
                            <span className="line-clamp-2 text-text-2">{p.source}</span>
                            <span className="line-clamp-2" style={{ color: "var(--primary-hover)" }}>{p.target}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {analysis.pair_count > 0 && (
                      <button type="button"
                        onClick={() => setPairsModal({ file: target!, source, mode: modeOverride as "" | StyleMode })}
                        className="mt-1 inline-flex items-center gap-1.5 self-start rounded-full border border-border-strong px-3.5 py-1.5 text-sm font-semibold text-text-2 transition-colors hover:border-primary hover:text-primary">
                        전체 {analysis.pair_count}쌍 보기
                        <span aria-hidden>→</span>
                      </button>
                    )}
                    {!source && (
                      <label className="mt-3 flex flex-wrap items-center gap-2">
                        <span className="slug">형식이 다른가요?</span>
                        <select
                          className="field field--sm"
                          value={modeOverride}
                          onChange={(e) => setModeOverride(e.target.value)}
                        >
                          <option value="">자동 감지</option>
                          {OVERRIDE_MODES.map((m) => (
                            <option key={m} value={m}>{MODE_LABEL[m]}</option>
                          ))}
                        </select>
                      </label>
                    )}
                  </>
                )}
              </div>
            )}

            {/* batch triage table (2+ files) */}
            {batch && (
              <div className="flex flex-col gap-3">
                <div
                  className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-[var(--r-lg)] px-4 py-3"
                  style={{ background: "var(--gradient-brand-soft)" }}
                >
                  <span className="text-base text-text-2">
                    <span className="text-gradient font-bold">{rows.length}개</span> 분석
                    {rows.some((r) => r.status === "analyzing") && (
                      <span className="slug"> · 분석 중…</span>
                    )}
                  </span>
                  <span className="flex items-center gap-4 text-sm font-semibold">
                    <span className="inline-flex items-center gap-1.5" style={{ color: "var(--ok)" }}>
                      <span className="ledger-dot" style={{ color: "var(--ok)" }} aria-hidden />
                      {rows.filter((r) => r.selected).length} 선택
                    </span>
                    <span className="inline-flex items-center gap-1.5" style={{ color: "var(--danger)" }}>
                      <span className="ledger-dot" style={{ color: "var(--danger)" }} aria-hidden />
                      {rows.filter((r) => r.flagReason && !r.selected).length} 제외
                    </span>
                  </span>
                </div>
                <BatchReviewTable
                  rows={rows}
                  modeLabel={MODE_LABEL}
                  overrideModes={OVERRIDE_MODES}
                  onToggle={onToggle}
                  onRename={onRename}
                  onOverride={onOverride}
                  onRetry={onRetry}
                  onViewPairs={(id) => {
                    const r = rows.find((row) => row.id === id);
                    if (r) setPairsModal({ file: r.file, source: null, mode: r.modeOverride });
                  }}
                />
                <div className="flex flex-wrap items-center gap-3">
                  {!committing ? (
                    <button
                      className="btn-primary"
                      disabled={rows.every(
                        (r) => !(r.selected && (r.status === "ready" || r.status === "commit_failed")),
                      )}
                      onClick={() => commitSelected()}
                    >
                      선택한{" "}
                      {rows.filter((r) => r.selected && (r.status === "ready" || r.status === "commit_failed")).length}개
                      추가
                    </button>
                  ) : (
                    <>
                      <div
                        className="h-2 flex-1 overflow-hidden rounded-full"
                        style={{ background: "var(--surface-sunken)", minWidth: "8rem" }}
                      >
                        <div
                          className="h-full rounded-full transition-all duration-300"
                          style={{
                            width: `${commitTotal ? (committed / commitTotal) * 100 : 0}%`,
                            background: "var(--gradient-brand)",
                          }}
                        />
                      </div>
                      <span className="slug shrink-0">
                        추가 중… {committed}/{commitTotal}
                      </span>
                      <button className="btn-secondary shrink-0" onClick={() => { stopRef.current = true; }}>
                        중지
                      </button>
                    </>
                  )}
                </div>
              </div>
            )}

            <div className="flex flex-wrap items-end gap-x-5 gap-y-4">
              <label className="flex flex-col gap-1.5">
                <span className="label">원문 언어</span>
                <select className="field" value={sourceLang} onChange={(e) => setSourceLang(e.target.value)}>
                  {LANG_OPTIONS}
                </select>
              </label>
              <span className="pb-2 text-lg text-text-3 select-none">→</span>
              <label className="flex flex-col gap-1.5">
                <span className="label">번역 언어</span>
                <select className="field" value={targetLang} onChange={(e) => setTargetLang(e.target.value)}>
                  {LANG_OPTIONS}
                </select>
              </label>
              {!batch && (
                <button
                  onClick={onUpload}
                  disabled={busy || !target || analyzing || analysis?.mode === "source_only"}
                  className="btn-primary ml-auto"
                >
                  {busy ? "추가 중…" : "레퍼런스 추가"}
                </button>
              )}
            </div>

            {/* Success/error feedback via stamp tokens */}
            {msg && (
              <span className="stamp self-start">
                <span className="stamp__dot" aria-hidden="true" />
                {msg}
              </span>
            )}
            {error && (
              <span className="stamp stamp--alert self-start">
                <span className="stamp__dot" aria-hidden="true" />
                문제가 발생했어요 — {error}
              </span>
            )}
          </div>

          {/* domain folders — aligned to the same column with a divider above */}
          <section className="mt-10 border-t pt-6 px-1" style={{ borderColor: "var(--divider)" }}>
            <h3 className="label mb-3">레퍼런스 세트</h3>
            {domains.length === 0 ? (
              /* Soft dashed panel matching the card column */
              <div
                className="rounded-lg border border-dashed px-6 py-8 text-center"
                style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
              >
                <p className="label" style={{ color: "var(--text-2)" }}>
                  아직 레퍼런스가 없어요
                </p>
                <p className="slug mt-1" style={{ color: "var(--text-3)" }}>
                  위에서 과거 번역을 올려보세요
                </p>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {domains.map((d) => (
                  <button
                    key={d.domain}
                    onClick={() => setSelected(selected === d.domain ? "" : d.domain)}
                    className="rounded-full border px-3.5 py-1.5 text-sm font-semibold transition-colors"
                    style={
                      selected === d.domain
                        ? { borderColor: "var(--primary)", color: "var(--primary)", background: "var(--primary-soft)" }
                        : { borderColor: "var(--border-strong)", color: "var(--text-2)" }
                    }
                  >
                    {d.domain}
                    <span className="ml-2 text-sm text-text-3">· {d.references}건 · {d.segments}문장</span>
                  </button>
                ))}
              </div>
            )}
          </section>

          {/* references in the selected domain */}
          {selected && (
            <section className="mt-6 px-1">
              <h3 className="label mb-3">{selected} · 레퍼런스</h3>
              {refs.length === 0 ? (
                <p className="slug">이 세트에 레퍼런스가 없어요.</p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {refs.map((r) => (
                    <li key={r.id} className="ticket flex items-center justify-between gap-3 p-4">
                      <span className="min-w-0">
                        <span className="block truncate font-semibold">{r.name}</span>
                        <span className="slug">
                          {(LANGS[r.lang_tgt] ?? r.lang_tgt) || "번역본"} · 문장 {r.segment_count}개
                          {r.has_pairs && (
                            <span
                              className="chip-ok ml-2"
                              title="같은 원문이 다시 나오면 이전 번역을 그대로 재사용합니다"
                            >
                              재사용
                            </span>
                          )}
                        </span>
                      </span>
                      <button
                        onClick={() => onDelete(r.id)}
                        className="shrink-0 text-sm font-semibold transition-colors hover:underline"
                        style={{ color: "var(--danger)" }}
                      >
                        삭제
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </section>
      </main>

      {pairsModal && (
        <PairListModal
          file={pairsModal.file}
          source={pairsModal.source}
          mode={pairsModal.mode}
          onClose={() => setPairsModal(null)}
        />
      )}
    </div>
  );
}
