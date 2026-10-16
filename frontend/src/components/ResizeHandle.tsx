"use client";

import { useCallback, useRef } from "react";

// A thin draggable divider for the viewer spread. A pure pointer sensor: it reports
// horizontal drag deltas (px) via onDelta and lets the parent map them to a ratio/width.
// Pointer capture keeps the drag alive even if the cursor outruns the 6px strip.
// Keyboard: ←/→ nudge by 16px (a11y). Hidden below lg (mobile stacks, no resizing).
export function ResizeHandle({
  onDelta,
  ariaLabel,
}: {
  onDelta: (dxPx: number) => void;
  ariaLabel: string;
}) {
  const dragging = useRef(false);

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  }, []);

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (dragging.current && e.movementX) onDelta(e.movementX);
    },
    [onDelta],
  );

  const stop = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
  }, []);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "ArrowLeft") onDelta(-16);
      else if (e.key === "ArrowRight") onDelta(16);
    },
    [onDelta],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      tabIndex={0}
      className="resize-handle hidden lg:block"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      onKeyDown={onKeyDown}
    />
  );
}
