// Pure layout helpers for the Step-3 viewer's resizable spread. The split ratio, tool-dock
// width, and original-pane collapse persist to localStorage so a reviewer's preferred
// layout survives reloads. No React here — just math + storage + the grid column string.

export const MAIN_MIN = 0.25; // original pane never narrower/wider than these fractions
export const MAIN_MAX = 0.75;
export const DOCK_MIN = 280; // tool-dock width clamp (px)
export const DOCK_MAX = 560;
const STORAGE_KEY = "sb.viewer.layout.v1";

export interface ViewerLayout {
  /** original pane's fraction of the (original + main) area, before the dock. */
  originalRatio: number;
  /** tool-dock width in px (used only when a tool tab is open). */
  dockWidth: number;
  /** when true the original pane collapses to 0 (reopen from the top bar). */
  originalCollapsed: boolean;
}

export const DEFAULT_LAYOUT: ViewerLayout = {
  originalRatio: 0.5,
  dockWidth: 380,
  originalCollapsed: false,
};

export const clamp = (v: number, lo: number, hi: number): number =>
  Math.min(hi, Math.max(lo, v));

export function loadLayout(): ViewerLayout {
  if (typeof window === "undefined") return DEFAULT_LAYOUT; // SSR-safe: no localStorage on the server
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_LAYOUT;
    const p = JSON.parse(raw) as Partial<ViewerLayout>;
    return {
      originalRatio: clamp(Number(p.originalRatio) || DEFAULT_LAYOUT.originalRatio, MAIN_MIN, MAIN_MAX),
      dockWidth: clamp(Number(p.dockWidth) || DEFAULT_LAYOUT.dockWidth, DOCK_MIN, DOCK_MAX),
      originalCollapsed: Boolean(p.originalCollapsed),
    };
  } catch {
    return DEFAULT_LAYOUT; // private mode / corrupt value → defaults
  }
}

export function saveLayout(layout: ViewerLayout): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    /* private mode / quota — in-memory only */
  }
}

// CSS grid-template-columns for the spread (applied only at lg+). Child order:
// original, main-handle, main, [dock-handle, dock]. Collapsed → original shrinks to a thin
// rail (2.5rem, holding the reopen control) and the handle to 0; minmax(0,…) lets the
// editor/iframe shrink instead of forcing overflow.
export function gridColumns(layout: ViewerLayout, hasDock: boolean): string {
  const left = layout.originalCollapsed
    // minmax(2.5rem, 0fr) resolves to 2.5rem but keeps the same track *shape* as the
    // expanded minmax(0, …fr), so grid-template-columns can interpolate (animate) between them.
    ? `minmax(2.5rem, 0fr) 0px minmax(0, 1fr)`
    : `minmax(0, ${layout.originalRatio}fr) 6px minmax(0, ${1 - layout.originalRatio}fr)`;
  return hasDock ? `${left} 6px ${layout.dockWidth}px` : left;
}
