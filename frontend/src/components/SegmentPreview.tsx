// frontend/src/components/SegmentPreview.tsx
"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * Clickable same-origin HTML preview for 재번역. The served HTML (render_html with
 * wrap_segments) tags each sentence with <span class="seg" data-seg="{seg-id}">. We attach a
 * delegated click listener inside the iframe and report the clicked seg-id string up;
 * `activeSeg` drives a .seg-active highlight + scroll. Same-origin (/api proxy) lets us
 * touch contentDocument directly — no postMessage.
 */
export function SegmentPreview({
  src,
  activeSeg,
  onSelectSeg,
  reloadKey,
  jumpBlock,
}: {
  src: string;
  activeSeg: string | null;
  onSelectSeg: (id: string) => void;
  reloadKey: number;
  jumpBlock?: string | null;
}) {
  const ref = useRef<HTMLIFrameElement | null>(null);

  // Always hold the latest onSelectSeg so the once-per-load click listener
  // doesn't capture a stale closure (parent may pass inline callbacks that
  // close over async-loaded data arriving after the iframe fires "load").
  const onSelectRef = useRef(onSelectSeg);
  useEffect(() => {
    onSelectRef.current = onSelectSeg;
  });

  const applyActive = useCallback(() => {
    const doc = ref.current?.contentDocument;
    if (!doc) return;
    doc.querySelectorAll(".seg-active").forEach((el) => el.classList.remove("seg-active"));
    if (activeSeg == null) return;
    const el = doc.querySelector(`[data-seg="${activeSeg}"]`);
    if (el) {
      el.classList.add("seg-active");
      el.scrollIntoView({ block: "nearest" });
    }
  }, [activeSeg]);

  // Wire click delegation + clickable affordance once the iframe document is ready.
  const onLoad = useCallback(() => {
    const doc = ref.current?.contentDocument;
    if (!doc) return;
    doc.body.classList.add("seg-clickable");
    doc.addEventListener("click", (e) => {
      const t = (e.target as HTMLElement).closest("[data-seg]") as HTMLElement | null;
      if (t?.dataset.seg != null) onSelectRef.current(t.dataset.seg);
    });
    // eslint-disable-next-line react-hooks/immutability
    if (jumpBlock != null) doc.location.hash = `blk${jumpBlock}`;
    applyActive();
    // onSelectRef.current is read at call-time so onSelectSeg is not a dep here.
    // applyActive is stable; jumpBlock is a primitive — both are fine to keep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpBlock]);

  // Re-highlight when the active segment changes on an already-loaded iframe.
  useEffect(() => {
    applyActive();
  }, [applyActive]);

  // Live jump on an already-loaded iframe (chip click, no reload).
  useEffect(() => {
    const win = ref.current?.contentWindow;
    // eslint-disable-next-line react-hooks/immutability
    if (win && jumpBlock != null) win.location.hash = `blk${jumpBlock}`;
  }, [jumpBlock]);

  return (
    <iframe
      ref={ref}
      key={reloadKey}
      src={`${src}${src.includes("?") ? "&" : "?"}v=${reloadKey}`}
      onLoad={onLoad}
      title="번역본 (문장 클릭 선택)"
      className="sheet min-h-0 w-full flex-1 bg-white"
    />
  );
}
