import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

// Modern humanist sans for Latin/UI. Korean glyphs fall through to Pretendard
// (loaded via the CDN <link> below) so KO/EN read as one calm, premium sans.
const sans = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});

// Mono kept for the rare code-ish bit (doc ids, counts).
const mono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "StunningBox · 레이아웃 보존 문서 번역",
  description: "PDF·Word·HWP의 구조를 그대로 유지한 채 본문만 번역해 DOCX로 내보내는 AI 문서 번역, 스터닝박스.",
};

// Set the theme class before first paint so there's no flash. Reads the saved
// preference, falling back to the OS color scheme on first visit.
const themeScript = `(function(){try{var t=localStorage.getItem('theme');var m=window.matchMedia('(prefers-color-scheme: dark)').matches;if(t==='dark'||(!t&&m)){document.documentElement.classList.add('dark');}}catch(e){}})();`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      suppressHydrationWarning
      className={`${sans.variable} ${mono.variable} h-full antialiased`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <link rel="preconnect" href="https://cdn.jsdelivr.net" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css"
        />
      </head>
      {/* Fill the viewport and contain scrolling: the app's panes scroll internally
          (sticky toolbar/masthead) instead of the whole page becoming one giant sheet. */}
      <body className="h-dvh overflow-hidden flex flex-col">{children}</body>
    </html>
  );
}
