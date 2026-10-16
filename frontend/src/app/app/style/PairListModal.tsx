"use client";

import { useEffect, useState } from "react";
import { api, type StyleMode } from "@/lib/api";

export function PairListModal({
  file,
  source,
  mode,
  onClose,
}: {
  file: File;
  source?: File | null;
  mode?: "" | StyleMode;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pairs, setPairs] = useState<{ source: string; target: string }[]>([]);
  const [estimated, setEstimated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .styleReferencePairs({ target: file, source, mode: mode || undefined })
      .then((r) => {
        if (cancelled) return;
        setPairs(r.pairs);
        setEstimated(r.estimated);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [file, source, mode]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(15,23,42,0.45)" }}
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-[var(--r-lg)] border border-border shadow-xl"
        style={{ background: "var(--surface)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-bold">전체 {pairs.length}쌍</span>
            <span className="slug break-all">{file.name}</span>
            {estimated && (
              <span className="rounded-full px-2 py-0.5 text-xs font-semibold"
                title="정확한 짝은 추가(커밋) 시 정렬로 확정됩니다"
                style={{ color: "var(--primary)", background: "var(--primary-soft)" }}>추정</span>
            )}
          </div>
          <button type="button" aria-label="닫기" onClick={onClose}
            className="shrink-0 text-text-3 transition-colors hover:text-primary">✕</button>
        </div>
        <div className="flex-1 overflow-auto px-5 py-4">
          {loading && <p className="slug">불러오는 중…</p>}
          {error && <p className="slug" style={{ color: "var(--danger)" }}>불러오지 못했어요 — {error}</p>}
          {!loading && !error && pairs.length === 0 && (
            <p className="slug">표시할 문장 쌍이 없어요.</p>
          )}
          {!loading && !error && pairs.length > 0 && (
            <ol className="flex flex-col gap-2.5">
              {pairs.map((p, i) => (
                <li key={i} className="flex flex-col gap-0.5">
                  <span className="text-sm text-text-2 break-words">{p.source}</span>
                  <span className="text-sm break-words" style={{ color: "var(--primary-hover)" }}>{p.target}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}
