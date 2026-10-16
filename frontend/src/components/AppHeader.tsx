import type { ReactNode } from "react";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";
import { AuthMenu } from "./AuthMenu";

/**
 * AppHeader — shared chrome for the authenticated app surfaces: the upload page
 * (/app), the Style Library (/app/style), and — in its `dense` variant — the
 * /c review toolbar.
 *
 * The marketing landing (`/`) keeps its own <Nav>: sticky, anonymous, with a
 * centered section nav and sign-in CTAs. That is intentionally a different shell
 * and is NOT built from this component.
 *
 * Two variants:
 *  • roomy (default) — large logo above a `lead` tagline. The right zone renders
 *    page-specific `actions` followed by the standard ThemeToggle + AuthMenu, so
 *    the system cluster is always present and consistently last.
 *  • dense (`dense`) — the working toolbar: a small inline logo + `lead`
 *    metadata. The right zone is whatever `actions` you pass, verbatim — the page
 *    owns the full layout (including where ThemeToggle/AuthMenu sit) because the
 *    review page needs its primary CTA (DOCX 내보내기) to stay rightmost.
 */
export function AppHeader({
  logoHref = "/",
  lead,
  actions,
  dense = false,
}: {
  /** Where the logo links to (home for /app and /c, the upload page for /app/style). */
  logoHref?: string;
  /** Left-zone content beside/under the logo: a tagline (roomy) or inline metadata (dense). */
  lead?: ReactNode;
  /** Right-zone content. Roomy: page actions before the auto ThemeToggle+AuthMenu. Dense: the complete cluster. */
  actions?: ReactNode;
  /** Dense working-toolbar variant (small logo, inline lead, tight padding, caller-owned right zone). */
  dense?: boolean;
}) {
  if (dense) {
    return (
      <header className="reveal reveal-1 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border px-4 py-2.5 sm:px-6">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
          <Logo href={logoHref} size={22} />
          {lead}
        </div>
        {actions}
      </header>
    );
  }

  return (
    <header className="reveal reveal-1 border-b border-border px-6 pt-6 pb-4 sm:px-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Logo href={logoHref} size={30} />
          {lead}
        </div>
        <div className="flex items-center gap-2.5">
          {actions}
          <ThemeToggle />
          <AuthMenu />
        </div>
      </div>
    </header>
  );
}
