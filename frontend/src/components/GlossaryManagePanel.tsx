"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { GlossaryLocation, GlossaryTermReport, ProperNounReport } from "@/lib/types";
import { BulkReplacePanel } from "@/components/BulkReplacePanel";
import { ProperNounCandidates } from "@/components/ProperNounCandidates";

// status → token color for the dot (replaces the old emoji indicators)
const STATUS_COLOR: Record<string, string> = {
  applied: "var(--ok)",
  partial: "var(--warn)",
  missed: "var(--danger)",
  absent: "var(--muted)",
};

export function GlossaryManagePanel({
  docId,
  lang,
  reloadKey,
  onJump,
  onChanged,
  onPnTermsLoaded,
  activePnSource = null,
  onActivatePn,
}: {
  docId: string;
  lang: string;
  reloadKey: number; // bump to re-fetch the report after an apply
  onJump: (loc: GlossaryLocation) => void;
  onChanged: () => void; // after any glossary/replace apply: refresh preview + editor + report
  onPnTermsLoaded?: (terms: ProperNounReport[]) => void;
  activePnSource?: string | null;
  onActivatePn?: (term: ProperNounReport) => void;
}) {
  const [terms, setTerms] = useState<GlossaryTermReport[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({}); // source → edited target
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [newSource, setNewSource] = useState("");
  const [newTarget, setNewTarget] = useState("");
  const [showReplace, setShowReplace] = useState(false);

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

  useEffect(() => {
    let alive = true;
    setError(null); // clear any stale error before re-fetching (e.g. after a reloadKey bump)
    api
      .glossaryReport(docId, lang)
      .then((r) => {
        if (alive) setTerms(r.terms);
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, [docId, lang, reloadKey]);

  async function saveEdit(source: string) {
    const existing = terms.find((t) => t.source === source)?.target ?? "";
    const target = (drafts[source] ?? existing).trim();
    if (!target) return;
    setBusy(source);
    setNote(null);
    try {
      await api.saveGlossaryTerm(docId, lang, { source, target });
      // drop the local draft so the input reflects the server value on re-fetch
      setDrafts((d) => {
        const next = { ...d };
        delete next[source];
        return next;
      });
      onChanged();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function addTerm(applyNow: boolean) {
    const source = newSource.trim();
    const target = newTarget.trim();
    if (!source || !target) return;
    setBusy("__add__");
    setNote(null);
    try {
      await api.saveGlossaryTerm(docId, lang, { source, target, apply: applyNow });
      setNewSource("");
      setNewTarget("");
      onChanged();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (error) {
    return (
      <p className="p-6 text-base" style={{ color: "var(--danger)" }}>
        용어집 오류 — {error}
      </p>
    );
  }

  const summary = terms.reduce(
    (a, t) => {
      a[t.status] = (a[t.status] ?? 0) + 1;
      return a;
    },
    {} as Record<string, number>,
  );

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
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
      <div className="mb-4 border-t border-border" />
      <h2 className="label mb-2 px-1">용어 관리 — 업로드 용어집</h2>
      {terms.length > 0 && (
        <p className="slug mb-3 px-1">
          적용 {summary.applied ?? 0} · 부분 {summary.partial ?? 0} · 누락 {summary.missed ?? 0} · 미등장 {summary.absent ?? 0}
        </p>
      )}

      {terms.length === 0 && (
        <p className="slug mb-3 px-1">업로드된 용어집이 없습니다. 아래에서 용어를 추가할 수 있습니다.</p>
      )}

      <ul className="space-y-2">
        {terms.map((t) => (
          <li key={t.source} className="rounded-lg border border-border p-2.5">
            <div className="flex items-center gap-2">
              <span
                title={t.status}
                className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: STATUS_COLOR[t.status] ?? "var(--muted)" }}
              />
              <span className="mono flex-1 text-[var(--fs-label)] opacity-70">{t.source}</span>
              <span className="slug">
                {t.applied_count}/{t.source_count}
              </span>
            </div>
            <div className="mt-2 flex items-center gap-1.5">
              <input
                value={drafts[t.source] ?? t.target}
                onChange={(e) => setDrafts((d) => ({ ...d, [t.source]: e.target.value }))}
                aria-label={`${t.source} 번역어`}
                className="field w-full"
              />
              <button
                className="btn-secondary"
                disabled={busy === t.source}
                onClick={() => saveEdit(t.source)}
              >
                {busy === t.source ? "…" : "수정"}
              </button>
            </div>
            {t.locations.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {t.locations.map((loc, i) => (
                  <button
                    key={i}
                    onClick={() => onJump(loc)}
                    className="chip-jump mono"
                    style={
                      loc.applied
                        ? undefined
                        : {
                            background: "var(--danger-bg)",
                            color: "var(--danger)",
                            borderColor: "var(--danger-ring)",
                          }
                    }
                    title={loc.applied ? "적용 위치" : "누락 위치"}
                  >
                    #{loc.segment_index}
                  </button>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>

      <div className="mt-4 rounded-lg border border-border p-2.5">
        <p className="label mb-2">+ 용어 추가</p>
        <div className="flex items-center gap-1.5">
          <input
            value={newSource}
            onChange={(e) => setNewSource(e.target.value)}
            placeholder="원어"
            aria-label="추가할 원어"
            className="field w-full"
          />
          <span className="text-text-3 opacity-70">→</span>
          <input
            value={newTarget}
            onChange={(e) => setNewTarget(e.target.value)}
            placeholder="번역어"
            aria-label="추가할 번역어"
            className="field w-full"
          />
        </div>
        <div className="mt-1.5 flex items-center gap-2">
          <button className="btn-secondary" disabled={busy === "__add__"} onClick={() => addTerm(false)}>
            등록
          </button>
          <button
            className="btn-primary"
            disabled={busy === "__add__"}
            onClick={() => addTerm(true)}
          >
            등록 + 지금 적용(재번역)
          </button>
        </div>
      </div>

      <div className="mt-4">
        <button className="font-semibold underline" style={{ fontSize: "var(--fs-label)", color: "var(--text-2)" }} onClick={() => setShowReplace((s) => !s)}>
          {showReplace ? "직접 교체 닫기" : "직접 교체 (임의 단어)"}
        </button>
        {showReplace && (
          <div className="mt-2 border-t border-border pt-2">
            <BulkReplacePanel docId={docId} lang={lang} onApplied={onChanged} />
          </div>
        )}
      </div>

      {note && <p className="slug mt-2">{note}</p>}
    </div>
  );
}
