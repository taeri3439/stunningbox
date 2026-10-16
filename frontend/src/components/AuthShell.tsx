"use client";

import type { ReactNode } from "react";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";

// Shared auth-page chrome: gradient brand panel (lg+) + centered form column. The page
// supplies its form via children. Mirrors the original /login split layout.
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-h-0 flex-1">
      <div className="absolute right-4 top-4 z-10">
        <ThemeToggle />
      </div>

      <div
        className="relative hidden flex-col justify-between p-12 lg:flex lg:w-1/2"
        style={{ background: "var(--gradient-brand)" }}
      >
        <Logo size={32} tone="onAccent" href="/" />
        <div>
          <p className="font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.2, color: "var(--text-on-accent)" }}>
            레이아웃은 그대로,<br />번역은 완벽하게
          </p>
          <p className="mt-3 max-w-sm" style={{ fontSize: "var(--fs-base)", color: "rgba(255,255,255,0.9)" }}>
            PDF·Word·HWP의 구조를 지키며 본문만 번역합니다.
          </p>
        </div>
        <p className="slug" style={{ color: "rgba(255,255,255,0.8)" }}>© 2026 StunningBox</p>
      </div>

      <div className="flex flex-1 items-center justify-center overflow-y-auto p-6 lg:w-1/2">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex justify-center lg:hidden">
            <Logo size={30} href="/" />
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}
