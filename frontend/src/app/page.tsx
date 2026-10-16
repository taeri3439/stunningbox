// Server Component: pure static marketing markup. Interactivity lives in the client
// children it composes (<Logo/>, <ThemeToggle/>); the landing itself ships no JS.
import Link from "next/link";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { LANGS } from "@/lib/api";

/** Local POC without Supabase: skip login and open the upload dashboard. */
const START_HREF =
  process.env.NEXT_PUBLIC_DEV_NO_AUTH === "1" ? "/app" : "/login";

const STEPS = [
  { n: "1", title: "문서 업로드", body: "PDF · Word · HWP 파일을 끌어다 놓으면 끝." },
  { n: "2", title: "AI 구조 분석 + 번역", body: "제목·표·그림·번호를 식별하고 본문만 번역합니다." },
  { n: "3", title: "레이아웃 그대로 내보내기", body: "원본과 똑같은 구조의 DOCX로 다운로드." },
];

// Fix 2: distinct inline SVG icons per feature card (stroke=currentColor, ~18px)
const FEATURE_ICONS = [
  // 구조·레이아웃 보존 — layout grid
  <svg key="layout" width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <rect x="2" y="2" width="6" height="6" rx="1" />
    <rect x="10" y="2" width="6" height="6" rx="1" />
    <rect x="2" y="10" width="6" height="6" rx="1" />
    <rect x="10" y="10" width="6" height="6" rx="1" />
  </svg>,
  // 표·그림·번호 유지 — table
  <svg key="table" width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <rect x="2" y="2" width="14" height="14" rx="2" />
    <line x1="2" y1="7" x2="16" y2="7" />
    <line x1="2" y1="12" x2="16" y2="12" />
    <line x1="7" y1="7" x2="7" y2="16" />
  </svg>,
  // 용어집 관리 — book / glossary
  <svg key="book" width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M3 3h5a3 3 0 0 1 3 3v9a2 2 0 0 0-2-2H3V3z" />
    <path d="M15 3h-5a3 3 0 0 0-3 3v9a2 2 0 0 1 2-2h6V3z" />
  </svg>,
  // 실시간 편집 — pencil / edit
  <svg key="pencil" width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M12.5 2.5a2.12 2.12 0 0 1 3 3L5 16H2v-3L12.5 2.5z" />
    <line x1="10" y1="5" x2="13" y2="8" />
  </svg>,
];

const FEATURES = [
  { title: "구조·레이아웃 보존", body: "원본의 페이지 구성을 그대로 유지합니다." },
  { title: "표·그림·번호 유지", body: "표 구조와 그림, 번호 매김이 흐트러지지 않아요." },
  { title: "용어집 관리", body: "업로드한 용어집을 문서 전체에 일관되게 적용." },
  { title: "실시간 편집", body: "브라우저에서 바로 수정하고 내보낼 수 있어요." },
];

// Fix 4: nav link focus ring style (applied via inline style so it survives SSR)
const navLinkStyle: React.CSSProperties = {
  borderRadius: "var(--r-sm)",
  outline: "2px solid transparent",
  outlineOffset: "2px",
};

function Nav() {
  return (
    <nav className="sticky top-0 z-20 border-b border-border bg-surface/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3 sm:px-8">
        {/* Fix 1: logo links home via href prop */}
        <Logo size={28} href="/" />
        <div className="hidden items-center gap-6 md:flex">
          {/* Fix 4: px-1 py-2 for tap target + focus-visible ring via CSS class */}
          <a href="#how" className="label nav-link px-1 py-2 transition-colors hover:text-text" style={navLinkStyle}>작동 방식</a>
          <a href="#features" className="label nav-link px-1 py-2 transition-colors hover:text-text" style={navLinkStyle}>기능</a>
          <a href="#formats" className="label nav-link px-1 py-2 transition-colors hover:text-text" style={navLinkStyle}>지원 형식</a>
        </div>
        <div className="flex items-center gap-2.5">
          <Link href={START_HREF} className="btn-ghost">
            {START_HREF === "/app" ? "바로 시작" : "로그인"}
          </Link>
          <Link href={START_HREF} className="btn-primary">무료로 시작</Link>
          <ThemeToggle />
        </div>
      </div>
    </nav>
  );
}

function DocCard({ label, sample, accent }: { label: string; sample: string; accent?: boolean }) {
  return (
    <div className="ticket w-32 p-4 text-left sm:w-40">
      <span className="slug">{label}</span>
      <div className="mt-2 space-y-1.5">
        <div className="h-2 rounded-full" style={{ width: "85%", background: accent ? "var(--gradient-brand)" : "var(--surface-sunken)" }} />
        <div className="h-2 rounded-full bg-surface-sunken" style={{ width: "70%" }} />
        <div className="h-2 rounded-full bg-surface-sunken" style={{ width: "78%" }} />
      </div>
      <span className="slug mt-3 block">{sample}</span>
    </div>
  );
}

function Hero() {
  return (
    <header className="relative px-6 pt-20 pb-16 text-center sm:px-8">
      <span className="eyebrow reveal reveal-1">AI 문서 번역</span>
      <h1 className="reveal reveal-2 mx-auto mt-5 max-w-3xl font-bold" style={{ fontSize: "var(--fs-hero)", lineHeight: 1.12 }}>
        레이아웃은 그대로, 번역은 <span className="text-gradient">완벽하게</span>
      </h1>
      {/* Fix 3: keep-all prevents Korean particle break (e.g. "DOCX로") */}
      <p className="reveal reveal-2 mx-auto mt-4 max-w-xl text-text-2" style={{ fontSize: "var(--fs-base)", wordBreak: "keep-all", textWrap: "balance" } as React.CSSProperties}>
        PDF·Word·HWP를 올리면 구조·표·그림·번호는 그대로 두고, 본문만 번역해 DOCX로 내보냅니다.
      </p>
      <div className="reveal reveal-3 mt-8 flex flex-wrap items-center justify-center gap-3">
        <Link href={START_HREF} className="btn-primary">무료로 시작하기 →</Link>
        <a href="#how" className="btn-secondary">작동 방식 보기</a>
      </div>
      <div className="reveal reveal-3 mx-auto mt-14 flex max-w-3xl items-center justify-center gap-4 sm:gap-6">
        <DocCard label="원본" sample="가 나 다" />
        <Logo size={44} wordmark={false} />
        <DocCard label="번역본" sample="A B C" accent />
      </div>
    </header>
  );
}

function HowItWorks() {
  return (
    <section id="how" className="border-t border-border px-6 py-20 sm:px-8">
      <div className="mx-auto max-w-5xl">
        <h2 className="text-center font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.15 }}>이렇게 동작해요</h2>
        <p className="mt-3 text-center text-text-2" style={{ fontSize: "var(--fs-base)" }}>세 단계면 충분합니다.</p>
        <div className="mt-12 grid gap-6 sm:grid-cols-3">
          {/* Fix 6: restrained hover on step cards (translateY + shadow step) gated by prefers-reduced-motion */}
          {STEPS.map((s) => (
            <div key={s.n} className="ticket landing-card p-6">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-full font-bold" style={{ background: "var(--gradient-brand)", color: "var(--text-on-accent)" }}>{s.n}</span>
              <h3 className="mt-4 text-lg font-semibold">{s.title}</h3>
              <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>{s.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section id="features" className="border-t border-border px-6 py-20 sm:px-8">
      <div className="mx-auto max-w-5xl">
        <h2 className="text-center font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.15 }}>레이아웃을 지키는 번역</h2>
        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {/* Fix 2 + 6: distinct SVG icons on primary-soft tile + restrained hover */}
          {FEATURES.map((f, i) => (
            <div key={f.title} className="ticket landing-card p-6">
              <span
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg"
                style={{ background: "var(--primary-soft)", color: "var(--primary)" }}
              >
                {FEATURE_ICONS[i]}
              </span>
              <h3 className="mt-4 font-semibold">{f.title}</h3>
              <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>{f.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Formats() {
  const langs = Object.values(LANGS);
  return (
    <section id="formats" className="border-t border-border px-6 py-20 text-center sm:px-8">
      <div className="mx-auto max-w-3xl">
        <h2 className="font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.15 }}>지원 형식 & 언어</h2>
        <div className="mt-8 flex flex-wrap justify-center gap-2.5">
          {["PDF", "Word (DOCX)", "HWP"].map((f) => (
            <span key={f} className="rounded-full border border-border-strong px-4 py-2 font-semibold" style={{ fontSize: "var(--fs-body)" }}>{f}</span>
          ))}
        </div>
        <p className="slug mt-8">번역 언어 (양방향)</p>
        <div className="mt-3 flex flex-wrap justify-center gap-2">
          {langs.map((l) => (
            <span key={l} className="rounded-full bg-surface-2 px-3 py-1 text-text-2" style={{ fontSize: "var(--fs-body)" }}>{l}</span>
          ))}
        </div>
      </div>
    </section>
  );
}

function FinalCTA() {
  return (
    <section className="px-6 py-20 sm:px-8">
      <div className="mx-auto max-w-4xl rounded-[var(--r-xl)] p-12 text-center" style={{ background: "var(--gradient-brand)" }}>
        <h2 className="font-bold" style={{ fontSize: "var(--fs-h1)", lineHeight: 1.15, color: "var(--text-on-accent)" }}>지금 바로 번역을 시작하세요</h2>
        <p className="mx-auto mt-3 max-w-md" style={{ fontSize: "var(--fs-base)", color: "rgba(255,255,255,0.92)" }}>몇 초면 시작할 수 있어요.</p>
        {/* Fix 5: hover bg shift to --surface-2 + focus-visible ring; keeps white surface + --primary text */}
        <Link
          href={START_HREF}
          className="cta-btn mt-7 inline-flex rounded-full bg-white px-6 py-3 font-semibold shadow-sm"
          style={{ color: "var(--primary)" }}
        >
          무료로 시작
        </Link>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-border px-6 py-10 sm:px-8">
      <div className="mx-auto flex max-w-6xl flex-col items-center gap-3 text-center sm:flex-row sm:justify-between sm:text-left">
        <Logo size={24} />
        {/* Fix 7: tagline at --text-2 weight, copyright demoted to --text-3 + smaller */}
        <p className="slug" style={{ color: "var(--text-2)", fontSize: "var(--fs-caption)" }}>레이아웃을 지키는 AI 문서 번역, 스터닝박스</p>
        <p className="slug" style={{ color: "var(--text-3)", fontSize: "var(--fs-caption)", fontWeight: 400 }}>© 2026 StunningBox</p>
      </div>
    </footer>
  );
}

export default function LandingPage() {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <Nav />
      <main className="flex-1">
        <Hero />
        <HowItWorks />
        <Features />
        <Formats />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  );
}
