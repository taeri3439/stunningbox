"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { TBbox, THighlight } from "@/lib/api";

type Props = {
  docId: string;
  lang: string;
  reloadKey: number; // bump -> refetch the manifest (재변환 감지)
  segments?: { id: string; bbox: TBbox | null }[]; // 재번역 모드 hotspot
  activeSeg?: string | null;
  onSelectSeg?: (id: string) => void;
  highlights?: THighlight[]; // 용어 모드 오버레이
  scrollTo?: TBbox | null; // 지정 시 해당 위치로 스크롤
};

// Editor-parity translated pages: the page images are rendered by the same DS
// ConvertService the OnlyOffice editor uses, so they match it pixel-for-pixel — this is the
// same visual language as the original pane's page column in c/[id]/page.tsx (aspect-ratio
// wrapper + <img> + percentage-positioned hotspot buttons), mirrored here for the translation.
export default function TranslatedPages({
  docId,
  lang,
  reloadKey,
  segments,
  activeSeg,
  onSelectSeg,
  highlights,
  scrollTo,
}: Props) {
  const [manifest, setManifest] = useState<Awaited<ReturnType<typeof api.tpages>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Bumped by the retry button to re-run the fetch effect below without duplicating it.
  const [attempt, setAttempt] = useState(0);

  const activeHotRef = useRef<HTMLButtonElement | null>(null);
  const scrollToRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .tpages(docId, lang)
      .then((m) => {
        if (alive) setManifest(m); // keep the previous pages mounted until a fresh one lands
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [docId, lang, reloadKey, attempt]);

  // activeSeg changed (or its hotspot only just became renderable) → scroll it into view.
  useEffect(() => {
    activeHotRef.current?.scrollIntoView({ block: "center" });
  }, [activeSeg, segments, manifest]);

  // scrollTo changed (and the page it lives on is rendered) → scroll to that exact bbox.
  useEffect(() => {
    scrollToRef.current?.scrollIntoView({ block: "center" });
  }, [scrollTo, manifest]);

  if (error) {
    return (
      <div className="p-6 text-sm text-[var(--text-2)]">
        <p>번역본 페이지를 준비하지 못했습니다: {error}</p>
        <button type="button" className="btn-secondary mt-3" onClick={() => setAttempt((a) => a + 1)}>
          다시 시도
        </button>
      </div>
    );
  }

  return (
    <div className="relative h-full overflow-auto">
      {loading && (
        <div className="absolute inset-0 z-10 grid place-items-center bg-[var(--surface)]/60 text-sm text-[var(--text-2)]">
          편집기 레이아웃으로 페이지 준비 중…
        </div>
      )}
      {manifest?.pages.map((p) => (
        <figure
          key={p.n}
          id={`tp-${p.n}`}
          className="mx-auto mb-4"
          style={{ width: `min(100%, ${manifest.page_w}px)` }}
        >
          <div
            className="relative"
            style={{ aspectRatio: `${manifest.page_w} / ${manifest.page_h}` }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={p.url} alt={`번역본 ${p.n + 1}쪽`} className="sheet w-full bg-white" />
            {segments
              ?.filter((s) => s.bbox && s.bbox.page === p.n)
              .map((s) => {
                const b = s.bbox!;
                const isActive = activeSeg === s.id;
                return (
                  <button
                    key={s.id}
                    ref={isActive ? activeHotRef : undefined}
                    type="button"
                    aria-label="문장 선택"
                    onClick={() => onSelectSeg?.(s.id)}
                    className="absolute rounded-[3px] hover:bg-[var(--primary-soft)]"
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
            {highlights?.flatMap((h, hi) =>
              h.boxes
                .filter((b) => b.page === p.n)
                .map((b, bi) => (
                  <span
                    key={`${hi}-${bi}`}
                    aria-hidden
                    className={`pointer-events-none absolute rounded-[3px] ${
                      h.color === "indigo"
                        ? "bg-indigo-500/25 ring-1 ring-indigo-500"
                        : "bg-amber-400/30"
                    }`}
                    style={{
                      left: `${b.x * 100}%`,
                      top: `${b.y * 100}%`,
                      width: `${b.w * 100}%`,
                      height: `${b.h * 100}%`,
                    }}
                  />
                )),
            )}
            {scrollTo && scrollTo.page === p.n && (
              <span
                ref={scrollToRef}
                aria-hidden
                className="pointer-events-none absolute h-0 w-0"
                style={{ left: `${scrollTo.x * 100}%`, top: `${scrollTo.y * 100}%` }}
              />
            )}
          </div>
          <figcaption className="mt-1 text-center text-[var(--fs-caption)] opacity-50">
            {p.n + 1}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}
