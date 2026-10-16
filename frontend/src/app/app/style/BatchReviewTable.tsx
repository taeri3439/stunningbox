"use client";

import { type CSSProperties, useState } from "react";
import { type StyleAnalysis, type StyleMode } from "@/lib/api";

export type RowStatus =
  | "analyzing"        // analyze in flight
  | "ready"            // analyzed, committable
  | "flagged"          // analyzed but auto-excluded (source_only or duplicate)
  | "analyze_failed"   // analyze threw
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
  duplicate: "이미 있음 (같은 세트)",
};

// Status → left-rail / status-dot colour. The rail is the at-a-glance signal.
const STATE_COLOR: Record<RowStatus, string> = {
  analyzing: "var(--muted)",
  ready: "var(--ok)",
  flagged: "var(--warn)",
  analyze_failed: "var(--danger)",
  committing: "var(--primary)",
  done: "var(--ok)",
  commit_failed: "var(--danger)",
};
// duplicate (re-selectable) reads as a soft warn; no_translation (hard-excluded) as danger.
function railColor(r: RefRow): string {
  if (r.status === "flagged") return r.flagReason === "no_translation" ? "var(--danger)" : "var(--warn)";
  return STATE_COLOR[r.status];
}
const PULSE: RowStatus[] = ["analyzing", "committing"];

export function BatchReviewTable({
  rows,
  modeLabel,
  overrideModes,
  onToggle,
  onRename,
  onOverride,
  onRetry,
  onViewPairs,
}: {
  rows: RefRow[];
  modeLabel: Record<StyleMode, string>;
  overrideModes: StyleMode[];
  onToggle: (id: string, selected: boolean) => void;
  onRename: (id: string, name: string) => void;
  onOverride: (id: string, mode: "" | StyleMode) => void;
  onRetry: (id: string) => void;
  onViewPairs: (id: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <ul className="ledger">
      {rows.map((r, i) => {
        const a = r.analysis;
        const expandable = a !== null && r.status !== "analyzing" && r.status !== "analyze_failed";
        const rail = railColor(r);
        return (
          <li key={r.id} className="reveal" style={{ animationDelay: `${Math.min(i, 12) * 0.025}s` }}>
            <div
              className="ledger-row"
              data-selected={r.selected}
              data-state={r.status}
              style={{ ["--rail"]: rail } as CSSProperties}
            >
              {/* duplicates are auto-unchecked but stay re-selectable by design;
                  only no_translation (source_only) is uncommittable → disabled */}
              <input
                type="checkbox"
                checked={r.selected}
                disabled={r.flagReason === "no_translation" || r.status === "analyzing"}
                onChange={(e) => onToggle(r.id, e.target.checked)}
                aria-label={`${r.name} 선택`}
              />
              <span
                className={`ledger-dot${PULSE.includes(r.status) ? " ledger-dot--pulse" : ""}`}
                style={{ color: rail }}
                aria-hidden
              />
              <span className="min-w-0 flex-1 truncate text-sm font-medium" title={r.file.name}>
                {r.name}
              </span>

              <div className="flex shrink-0 items-center gap-2">
                {r.status === "analyzing" && <span className="slug">분석 중…</span>}
                {r.status === "analyze_failed" && (
                  <span className="slug" style={{ color: "var(--danger)" }}>분석 실패</span>
                )}
                {r.flagReason && (
                  <span className="slug" style={{ color: rail }}>{FLAG_TEXT[r.flagReason]}</span>
                )}
                {!r.flagReason && a && r.status !== "analyzing" && (
                  <>
                    <span className="chip-soft">{modeLabel[(r.modeOverride || a.mode) as StyleMode]}</span>
                    <span className="slug text-text-3">
                      짝 {a.pair_count}{a.estimated ? " (추정)" : ""} · 문체 {a.style_count}
                    </span>
                  </>
                )}
                {r.status === "committing" && (
                  <span className="slug" style={{ color: "var(--primary)" }}>추가 중…</span>
                )}
                {r.status === "done" && (
                  <span className="slug" style={{ color: "var(--ok)" }}>추가됨</span>
                )}
                {r.status === "commit_failed" && (
                  <span className="slug" style={{ color: "var(--danger)" }} title={r.error}>실패</span>
                )}
                {(r.status === "analyze_failed" || r.status === "commit_failed") && (
                  <button type="button" onClick={() => onRetry(r.id)}
                    className="slug transition-colors hover:text-primary">재시도</button>
                )}
                {expandable && (
                  <button type="button" aria-label="자세히" aria-expanded={open === r.id}
                    onClick={() => setOpen(open === r.id ? null : r.id)}
                    className="text-text-3 transition-colors hover:text-primary">
                    {open === r.id ? "▾" : "▸"}
                  </button>
                )}
              </div>
              {r.status === "committing" && <span className="ledger-bar" aria-hidden />}
            </div>

            {open === r.id && a && (
              <div className="flex flex-col gap-2 px-10 pb-3 pt-1" style={{ background: "var(--surface-2)" }}>
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
                  <ul className="flex flex-col gap-2">
                    {a.sample_pairs.slice(0, 3).map((p, i) => (
                      <li key={`${r.id}-pair-${i}`} className="slug flex flex-col gap-0.5 break-words">
                        <span className="line-clamp-2 text-text-2">{p.source}</span>
                        <span className="line-clamp-2" style={{ color: "var(--primary-hover)" }}>{p.target}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {a.pair_count > 0 && (
                  <button type="button" onClick={() => onViewPairs(r.id)}
                    className="mt-1 inline-flex items-center gap-1.5 self-start rounded-full border border-border-strong px-3.5 py-1.5 text-sm font-semibold text-text-2 transition-colors hover:border-primary hover:text-primary">
                    전체 {a.pair_count}쌍 보기
                    <span aria-hidden>→</span>
                  </button>
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
