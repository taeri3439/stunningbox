"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Segment } from "@/lib/types";

const PRESETS: { key: string; label: string }[] = [
  { key: "concise", label: "간략하게" },
  { key: "detailed", label: "상세하게" },
  { key: "formal", label: "격식·문어체" },
  { key: "natural", label: "자연스럽게(의역)" },
];

export function RetranslatePanel({
  docId,
  lang,
  segments,
  activeSeg,
  onSelect,
  onApplied,
  onSegmentText,
}: {
  docId: string;
  lang: string;
  segments: Segment[];
  activeSeg: Segment | null;
  onSelect: (seg: Segment) => void;
  // Bump the editor reload after a re-render so 문서 편집 reflects the change.
  onApplied: (bookmark: string | null) => void;
  // Reflect applied text up so the lifted segments list + preview show it.
  onSegmentText: (index: number, text: string) => void;
}) {
  const [preset, setPreset] = useState<string>("concise");
  const [guidance, setGuidance] = useState<string>("");
  const [preview, setPreview] = useState<{ id: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  // Reset transient state whenever the active segment id changes — covers
  // selections that arrive from outside (viewer click, PDF hotspot, etc.) and
  // not just from the list-row click handler below.
  const prevIndexRef = useRef<string | undefined>(undefined);
  const activeIndex = activeSeg?.id;
  if (activeIndex !== prevIndexRef.current) {
    prevIndexRef.current = activeIndex;
    if (preview !== null) setPreview(null);
    if (note !== null) setNote(null);
    if (guidance !== "") setGuidance("");
    if (preset !== "concise") setPreset("concise");
  }

  // Follow selections that arrive from the panes (translated-page hotspot or
  // source hotspot): bring the active row into view. "nearest" is a no-op when
  // the row is already visible, so list-row clicks never yank the scroll.
  const activeRowRef = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    activeRowRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeIndex]);

  function select(seg: Segment) {
    onSelect(seg);
  }

  async function doPreview(seg: Segment) {
    setBusy(true);
    setNote(null);
    try {
      const res = await api.retranslateSegment(docId, {
        lang,
        id: seg.id,
        preset,
        guidance,
      });
      setPreview({ id: seg.id, text: res.new_text });
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function apply(seg: Segment) {
    if (!preview) return;
    const newText = preview.text;
    onSegmentText(seg.index, newText); // reflect in the lifted list + refresh preview
    setPreview(null);
    setBusy(true);
    setNote("반영 중…");
    try {
      await api.render(docId, lang); // keep the docx in sync for 문서 편집
      onApplied(seg.block_id != null ? `blk${seg.block_id}` : null);
      setNote("반영했습니다.");
    } catch (e) {
      setNote(`반영 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
      <h2 className="label mb-3 px-1">재번역 — 문장을 선택하세요</h2>
      <ul className="space-y-2">
        {segments.map((seg) => (
          <li
            key={seg.id}
            ref={activeSeg?.id === seg.id ? activeRowRef : undefined}
            className={`rounded-lg border p-2.5 transition-colors ${
              activeSeg?.id === seg.id
                ? "border-border bg-surface-2"
                : "border-transparent hover:bg-surface-2"
            }`}
          >
            <button
              className="block w-full rounded-md text-left focus-visible:outline-none focus-visible:[box-shadow:var(--ring)]"
              onClick={() => select(seg)}
            >
              {seg.added ? (
                <span className="slug inline-block rounded-full bg-[var(--muted-bg)] px-2 py-0.5 text-[var(--muted)]">
                  원문 없음 · 추가됨
                </span>
              ) : (
                <span className="mono block text-[var(--fs-label)] opacity-60">{seg.source}</span>
              )}
              <span className="mt-0.5 block text-[var(--fs-body)] leading-relaxed">{seg.translated}</span>
            </button>

            {activeSeg?.id === seg.id && (
              <div className="mt-2 border-t border-border pt-2">
                <div className="flex flex-wrap gap-1.5">
                  {PRESETS.map((p) => (
                    <button
                      key={p.key}
                      onClick={() => setPreset(p.key)}
                      className="rounded-full border px-3 py-1.5 text-[0.8rem] font-semibold transition-colors focus-visible:outline-none focus-visible:[box-shadow:var(--ring)]"
                      style={
                        preset === p.key
                          ? { borderColor: "var(--primary)", color: "var(--primary-hover)", background: "var(--primary-soft)" }
                          : { borderColor: "var(--border)", color: "var(--text-2)" }
                      }
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
                <textarea
                  value={guidance}
                  onChange={(e) => setGuidance(e.target.value)}
                  placeholder="추가 지시 (선택) — 예: 법률 용어는 원문 유지"
                  className="field mt-2 w-full"
                  rows={2}
                />
                <div className="mt-2 flex items-center gap-2">
                  <button
                    className="btn-secondary"
                    disabled={busy || seg.added}
                    title={seg.added ? "추가된 문단은 원문이 없어 재번역할 수 없습니다" : undefined}
                    onClick={() => doPreview(seg)}
                  >
                    {busy ? "번역 중…" : "재번역"}
                  </button>
                  {preview?.id === seg.id && (
                    <button className="btn-primary" onClick={() => apply(seg)}>
                      적용
                    </button>
                  )}
                </div>
                {preview?.id === seg.id && (
                  <p className="mt-2 rounded-md bg-surface-2 p-2.5 text-[0.95rem] leading-relaxed">{preview.text}</p>
                )}
                {note && <p className="slug mt-2">{note}</p>}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
