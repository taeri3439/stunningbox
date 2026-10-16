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
