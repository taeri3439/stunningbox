"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, LANGS, type StyleDomain } from "@/lib/api";
import { AppHeader } from "@/components/AppHeader";
import { MyDocuments } from "@/components/MyDocuments";
import { FilePicker } from "@/components/FilePicker";

// Language <select> options are static — build them once at module load.
const LANG_OPTIONS = Object.entries(LANGS).map(([k, v]) => (
  <option key={k} value={k}>
    {v}
  </option>
));

function LangSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="label">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="field"
      >
        {LANG_OPTIONS}
      </select>
    </label>
  );
}

// Step 1 of 3 — upload. Pick a document and the language pair, then hand off to the
// processing screen (/p/[id]) which watches the background job to completion.
export default function UploadPage() {
  const router = useRouter();
  const [mode, setMode] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [glossaryFile, setGlossaryFile] = useState<File | null>(null);
  const [sourceLang, setSourceLang] = useState("ko");
  const [targetLang, setTargetLang] = useState("en");
  const [styleDomain, setStyleDomain] = useState("");
  const [domains, setDomains] = useState<StyleDomain[]>([]);
  const [busy, setBusy] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // On file select, auto-detect the main language and default the direction (non-Korean →
  // Korean, Korean → English). The user can still adjust either dropdown afterward.
  async function onFile(f: File | null) {
    setFile(f);
    if (!f) return;
    setDetecting(true);
    try {
      const { source_lang, target_lang } = await api.detectLanguage(f);
      setSourceLang(source_lang);
      setTargetLang(target_lang);
    } catch {
      /* keep current defaults on failure */
    } finally {
      setDetecting(false);
    }
  }

  useEffect(() => {
    api
      .health()
      .then((h) => setMode(h.mode))
      .catch(() => setMode("offline?"));
    // Load the user's style-reference domains for the picker (best-effort).
    api
      .styleDomains()
      .then((d) => setDomains(d.domains))
      .catch(() => setDomains([]));
  }, []);

  async function onStart() {
    if (!file) {
      setError("문서 파일을 먼저 선택하세요.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const { doc_id } = await api.upload(
        file, sourceLang, targetLang, glossaryFile, styleDomain || undefined,
      );
      router.push(`/p/${doc_id}?lang=${encodeURIComponent(targetLang)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col min-h-0">
      {/* header */}
      <AppHeader
        logoHref="/"
        lead={
          <p className="mt-2 text-base text-text-2">
            레이아웃은 그대로 두고 본문만 번역합니다
          </p>
        }
        actions={
          <>
            {/* Engine status: quiet dot + caption, not a decorative pill */}
            {mode && (() => {
              const live = mode === "openai" || mode === "anthropic" || mode === "claude";
              const label =
                mode === "openai" ? "OpenAI"
                : mode === "anthropic" || mode === "claude" ? "Claude"
                : mode === "offline-mock" ? "오프라인 목업"
                : `오프라인 · ${mode}`;
              return (
              <span
                className="inline-flex items-center gap-1.5"
                title={`번역 엔진: ${label}`}
              >
                <span
                  className="inline-block h-2 w-2 rounded-full flex-none"
                  style={{ background: live ? "var(--ok)" : "var(--muted)" }}
                  aria-hidden="true"
                />
                <span className="slug" style={{ color: live ? "var(--text-2)" : "var(--text-3)" }}>
                  {label}
                </span>
              </span>
              );
            })()}
            <Link
              href="/app/style"  
              className="rounded-full border border-border-strong px-3 py-1.5 text-sm font-semibold text-text-2 transition-colors hover:text-primary"
            >
              스타일 라이브러리
            </Link>
          </>
        }
      />

      {/* upload card — top-aligned so the card sits under the header without a large dead band */}
      <main className="flex flex-1 items-start justify-center overflow-auto px-6 pt-10 pb-12 sm:px-10 sm:pt-16">
        <section className="reveal reveal-2 w-full max-w-3xl">
          <div className="mb-7 text-center">
            <span className="eyebrow">1단계 / 3 · 문서 업로드</span>
            <h2
              className="mt-4 font-bold"
              style={{ fontSize: "var(--fs-hero)", lineHeight: 1.12 }}
            >
              번역할 문서를 <span className="text-gradient">올려주세요</span>
            </h2>
            <p className="mx-auto mt-3 max-w-md text-text-2" style={{ fontSize: "var(--fs-base)" }}>
              구조(제목·표·그림·번호)는 그대로 두고, 본문 텍스트만 번역합니다.
            </p>
          </div>

          <div className="ticket flex flex-col gap-6 p-7 sm:p-8">
            {/* 문서 파일 picker */}
            <div className="flex flex-col gap-2.5">
              <span className="label">문서 파일 (PDF · Word · HWP)</span>
              <FilePicker
                accept="application/pdf,.pdf,.docx,.doc,.hwp"
                onFile={onFile}
                file={file}
              />
              {file && detecting && (
                <span className="slug" style={{ color: "var(--text-3)" }}>
                  언어 감지 중…
                </span>
              )}
            </div>

            {/* 용어집 picker */}
            <div className="flex flex-col gap-2.5">
              <span className="label flex items-center justify-between">
                용어집 (CSV · 선택)
                <a
                  href="/api/glossary/template"
                  download
                  className="font-semibold underline"
                  style={{ fontSize: "var(--fs-label)", color: "var(--primary-hover)" }}
                >
                  양식 다운로드
                </a>
              </span>
              <FilePicker
                accept=".csv,text/csv"
                onFile={setGlossaryFile}
                file={glossaryFile}
              />
            </div>

            <div className="flex flex-wrap items-end gap-x-5 gap-y-4 pt-1">
              <LangSelect label="원문" value={sourceLang} onChange={setSourceLang} />
              <span className="pb-2 text-lg text-text-3 select-none">→</span>
              <LangSelect label="번역" value={targetLang} onChange={setTargetLang} />
              <label className="flex flex-col gap-1.5">
                <span className="label">레퍼런스 세트 (선택)</span>
                <select
                  value={styleDomain}
                  onChange={(e) => setStyleDomain(e.target.value)}
                  className="field"
                  title="선택한 레퍼런스 세트의 과거 번역 문체를 따라갑니다"
                >
                  <option value="">없음</option>
                  {domains.map((d) => (
                    <option key={d.domain} value={d.domain}>
                      {d.domain} ({d.segments})
                    </option>
                  ))}
                </select>
              </label>
              <button
                onClick={onStart}
                disabled={busy || !file}
                className="btn-primary ml-auto"
              >
                {busy ? "업로드 중…" : "번역 시작"}
              </button>
            </div>

            {/* Upload error — rendered inline in the card near the submit button */}
            {error && (
              <span className="stamp stamp--alert self-start">
                <span className="stamp__dot" aria-hidden="true" />
                문제가 발생했어요 — {error}
              </span>
            )}

            {domains.length === 0 && (
              <p className="slug -mt-2">
                문체를 적용하려면{" "}
                <Link href="/app/style" className="font-semibold underline" style={{ color: "var(--primary-hover)" }}>
                  스타일 라이브러리
                </Link>
                에 과거 번역을 레퍼런스 세트로 모아 올려두세요.
              </p>
            )}
          </div>

          <MyDocuments />
        </section>
      </main>
    </div>
  );
}
