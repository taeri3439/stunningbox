"use client";

import Link from "next/link";
import { useId } from "react";

// The StunningBox brand lockup: a layered-box mark (two offset document sheets with a
// transform arrow — 원본 → 번역본, brand-gradient filled) + the "StunningBox" wordmark.
// tone="onAccent" renders white-on-gradient (for the gradient brand panels); default
// "brand" renders the gradient mark + gradient "Box" on a neutral surface.
export function Logo({
  size = 28,
  wordmark = true,
  href,
  tone = "brand",
  className = "",
}: {
  size?: number;
  wordmark?: boolean;
  href?: string;
  tone?: "brand" | "onAccent";
  className?: string;
}) {
  const gid = useId();
  const onAccent = tone === "onAccent";

  const mark = (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden
      style={{ flex: "none" }}
    >
      {!onAccent && (
        <defs>
          <linearGradient id={gid} x1="2" y1="2" x2="30" y2="30" gradientUnits="userSpaceOnUse">
            <stop stopColor="#07b9ce" />
            <stop offset="0.5" stopColor="#3969e7" />
            <stop offset="1" stopColor="#7d2ae7" />
          </linearGradient>
        </defs>
      )}
      {/* back sheet (원본) */}
      <rect x="6" y="4.5" width="15" height="20" rx="3" fill={onAccent ? "#fff" : `url(#${gid})`} opacity={onAccent ? 0.5 : 0.32} />
      {/* front sheet (번역본) */}
      <rect x="11" y="7.5" width="15" height="20" rx="3" fill={onAccent ? "#fff" : `url(#${gid})`} />
      {/* transform cue (→) on the front sheet */}
      <path
        d="M15 17.5h6M19 14.5l3 3-3 3"
        stroke={onAccent ? "#3969e7" : "#fff"}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.95}
      />
    </svg>
  );

  const content = (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      {mark}
      {wordmark &&
        (onAccent ? (
          <span className="font-bold tracking-tight text-white" style={{ fontSize: size * 0.62 }}>
            StunningBox
          </span>
        ) : (
          <span className="font-bold tracking-tight" style={{ fontSize: size * 0.62 }}>
            Stunning<span className="text-gradient">Box</span>
          </span>
        ))}
    </span>
  );

  return href ? (
    <Link href={href} aria-label="StunningBox 홈" className="inline-flex">
      {content}
    </Link>
  ) : (
    content
  );
}
