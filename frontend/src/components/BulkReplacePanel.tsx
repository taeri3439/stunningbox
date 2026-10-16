"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { MatchMode, ReplaceResult, ReplaceRule } from "@/lib/types";

const MATCHES: { key: MatchMode; label: string }[] = [
  { key: "smart", label: "스마트" },
  { key: "exact", label: "정확히" },
  { key: "loose", label: "느슨하게" },
];

// A rule row carries a stable id so React keys survive row removal (matters for IME
// composition in the find/replace inputs). The id is stripped before hitting the API.
interface Row extends ReplaceRule {
  id: number;
}

let _rowSeq = 0;

function emptyRule(): Row {
  return { id: _rowSeq++, find: "", replace: "", match: "smart", save: true };
}

export function BulkReplacePanel({
  docId,
  lang,
  onApplied,
}: {
  docId: string;
  lang: string;
  // Called after a successful apply so the parent reloads the OnlyOffice editor
  // (Community Edition has no connector for in-place edits).
  onApplied: () => void;
}) {
  const [rules, setRules] = useState<Row[]>([emptyRule()]);
  const [results, setResults] = useState<ReplaceResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  function update(i: number, patch: Partial<Row>) {
    setRules((prev) => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    setResults(null);
  }
  function addRow() {
    setRules((prev) => [...prev, emptyRule()]);
  }
  function removeRow(i: number) {
    setRules((prev) => (prev.length === 1 ? prev : prev.filter((_, j) => j !== i)));
    setResults(null);
  }

  // Drop empty / no-op rows and strip the local id before sending (backend skips them too).
  const filled = (): ReplaceRule[] =>
    rules
      .filter((r) => r.find.trim() && r.find !== r.replace)
      .map((r) => ({ find: r.find, replace: r.replace, match: r.match, save: r.save }));
  const countOf = (rule: Row) =>
    results?.find((r) => r.find === rule.find && r.replace === rule.replace)?.count;

  async function doPreview() {
    setBusy(true);
    setNote(null);
    try {
      const res = await api.replacePreview(docId, lang, filled());
      setResults(res.results);
      if (res.total === 0) setNote("일치하는 단어가 없습니다.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    setBusy(true);
    setNote(null);
    try {
      const res = await api.replaceApply(docId, lang, filled());
      setResults(res.results);
      if (res.total > 0) {
        onApplied();
        setNote(`${res.total}곳을 변경하고 에디터에 반영했습니다.`);
      } else {
        setNote("일치하는 단어가 없어 변경하지 않았습니다.");
      }
    } catch (e) {
      setNote(`변경 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
      <h2 className="label mb-3 px-1">용어 일괄변경 — 번역 결과에서 단어를 바꿉니다</h2>
      <ul className="space-y-3">
        {rules.map((rule, i) => (
          <li key={rule.id} className="rounded-lg border border-border p-2.5">
            <div className="flex items-center gap-1.5">
              <input
                value={rule.find}
                onChange={(e) => update(i, { find: e.target.value })}
                aria-label="찾을 단어"
                placeholder="찾을 단어"
                className="field w-full"
              />
              <span className="text-text-3 opacity-70">→</span>
              <input
                value={rule.replace}
                onChange={(e) => update(i, { replace: e.target.value })}
                aria-label="바꿀 단어"
                placeholder="바꿀 단어"
                className="field w-full"
              />
              {rules.length > 1 && (
                <button
                  aria-label="행 삭제"
                  className="px-1 text-lg opacity-60"
                  onClick={() => removeRow(i)}
                >
                  ×
                </button>
              )}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              {MATCHES.map((m) => (
                <button
                  key={m.key}
                  onClick={() => update(i, { match: m.key })}
                  className="rounded-full border px-3 py-1.5 text-[0.8rem] font-semibold transition-colors"
                  style={
                    rule.match === m.key
                      ? { borderColor: "var(--primary)", color: "var(--primary-hover)", background: "var(--primary-soft)" }
                      : { borderColor: "var(--border)", color: "var(--text-2)" }
                  }
                >
                  {m.label}
                </button>
              ))}
              <label className="ml-auto flex items-center gap-1.5 text-[var(--fs-label)]">
                <input
                  type="checkbox"
                  checked={rule.save}
                  onChange={(e) => update(i, { save: e.target.checked })}
                />
                용어집에 저장
              </label>
            </div>
            {countOf(rule) != null && (
              <p className="slug mt-1">{countOf(rule)}곳</p>
            )}
          </li>
        ))}
      </ul>
      <button className="mt-2 font-semibold underline" style={{ fontSize: "var(--fs-label)", color: "var(--text-2)" }} onClick={addRow}>
        + 행 추가
      </button>
      <div className="mt-3 flex items-center gap-2">
        <button className="btn-ink" disabled={busy} onClick={doPreview}>
          {busy ? "확인 중…" : "미리보기"}
        </button>
        <button className="btn-vermillion" disabled={busy} onClick={apply}>
          적용
        </button>
      </div>
      {note && <p className="slug mt-2">{note}</p>}
    </div>
  );
}
