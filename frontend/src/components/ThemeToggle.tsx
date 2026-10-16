"use client";

import { useSyncExternalStore } from "react";

// `.dark` on <html> is the source of truth (set pre-paint by the inline script in
// layout.tsx). Subscribe to it as an external store so reads stay hydration-safe:
// getServerSnapshot returns false (SSR has no theme knowledge) → the client's first
// render matches, then useSyncExternalStore re-reads the real class after hydration.
function subscribe(onChange: () => void) {
  // Re-read on cross-tab changes and on any class flip (e.g. toggle() below).
  window.addEventListener("storage", onChange);
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => {
    window.removeEventListener("storage", onChange);
    observer.disconnect();
  };
}

const getSnapshot = () => document.documentElement.classList.contains("dark");
const getServerSnapshot = () => false;

// Sun/moon toggle. Flips `.dark` on <html> and remembers the choice in localStorage;
// the MutationObserver subscription propagates the change back into render state.
export function ThemeToggle() {
  const dark = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function toggle() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      /* ignore storage failures (private mode) */
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "라이트 모드로 전환" : "다크 모드로 전환"}
      aria-pressed={dark}
      title={dark ? "라이트 모드" : "다크 모드"}
      className="btn-ghost"
      style={{ padding: "0.45rem" }}
    >
      {/* During hydration `dark` reads the server snapshot (false → sun); it self-corrects
          to the real <html> class right after mount, so SSR markup never mismatches. */}
      <span aria-hidden className="inline-flex h-[1.05rem] w-[1.05rem] items-center justify-center">
        {dark ? (
          // moon
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
          </svg>
        ) : (
          // sun
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
          </svg>
        )}
      </span>
    </button>
  );
}
