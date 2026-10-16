"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import type { JobStage, JobStatus } from "@/lib/types";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Logo } from "@/components/Logo";

const STAGES: { key: JobStage; label: string; note: string; unit?: string }[] = [
  { key: "extract", label: "원본 추출", note: "PDF에서 텍스트·이미지 추출" },
  { key: "analyze", label: "구조 분석", note: "쪽마다 제목·표·그림 식별", unit: "쪽" },
  { key: "translate", label: "번역", note: "본문 텍스트 번역", unit: "묶음" },
];
const ORDER: Record<JobStage, number> = {
  extract: 0,
  analyze: 1,
  translate: 2,
  done: 3,
};

// Step 2 of 3 — watch the background job. Polls /status ~1s and advances a staged
// checklist with live per-stage counts; routes to /c/[id] when the job finishes.
export default function ProcessingPage() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const lang = useSearchParams().get("lang") ?? "en";

  const [job, setJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigated = useRef(false);

  useEffect(() => {
    let alive = true;

    async function poll() {
      try {
        const st = await api.status(id, lang);
        if (!alive) return;
        setJob(st);
        if (st.status === "error") {
          setError(st.error ?? "처리 중 오류가 발생했습니다.");
        } else if (st.status === "done" && !navigated.current) {
          navigated.current = true;
          router.replace(`/c/${id}?lang=${encodeURIComponent(lang)}`);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    }

    poll();
    const timer = setInterval(poll, 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [id, lang, router]);

  const current = job ? ORDER[job.stage] : -1;

  return (
    <div className="flex flex-1 flex-col min-h-0">
      <header className="reveal reveal-1 border-b border-border px-6 pt-6 pb-4 sm:px-10">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-4">
            <Logo href="/" size={30} />
            <span className="eyebrow">2단계 / 3 · 변환 중</span>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex flex-1 items-center justify-center overflow-auto px-6 py-12 sm:px-10">
        <section className="reveal reveal-2 w-full max-w-xl">
          {error ? (
            <div className="ticket p-7 text-center sm:p-8">
              <span className="stamp stamp--alert">
                <span className="stamp__dot" />
                처리 실패
              </span>
              <p className="mt-4 text-base" style={{ color: "var(--danger)" }}>
                {error}
              </p>
              <Link href="/app" className="btn-secondary mt-6 inline-flex">
                처음으로
              </Link>
            </div>
          ) : (
            <div className="ticket p-7 sm:p-8">
              <div className="mb-6 text-center">
                <h2 className="text-2xl font-bold leading-tight">문서를 변환하고 있어요…</h2>
                <p className="mt-2 text-text-2" style={{ fontSize: "var(--fs-base)" }}>
                  AI가 페이지 구조를 분석하고 번역합니다 · 약 1~2분 소요
                </p>
              </div>

              <ol className="flex flex-col gap-1">
                {STAGES.map((s, i) => {
                  const done = current > i || job?.status === "done";
                  const active = current === i && job?.status !== "done";
                  const showCount = active && (job?.total ?? 0) > 0;
                  const pct = showCount
                    ? Math.round(((job!.done) / job!.total) * 100)
                    : 0;
                  return (
                    <li
                      key={s.key}
                      className="border-b border-border py-3 last:border-b-0"
                      style={{ opacity: done || active ? 1 : 0.4 }}
                    >
                      <div className="flex items-center gap-3">
                        <span
                          className="stamp__dot"
                          style={{
                            background:
                              done || active ? "var(--primary)" : "var(--border-strong)",
                            boxShadow: active ? "0 0 0 3px var(--primary-soft)" : undefined,
                          }}
                        />
                        <span className="flex-1 text-base font-semibold">{s.label}</span>
                        <span className="label" style={{ color: "var(--text-3)" }}>
                          {done
                            ? "완료"
                            : active
                              ? showCount
                                ? `${job!.done} / ${job!.total}${s.unit ?? ""}`
                                : "진행 중"
                              : "대기"}
                        </span>
                      </div>
                      <p className="slug mt-1 ml-5">
                        {s.note}
                      </p>
                      {active && (
                        <div
                          className="mt-2 ml-5 h-[3px] overflow-hidden rounded-full"
                          style={{ background: "var(--surface-sunken)" }}
                        >
                          <div
                            className="h-full rounded-full transition-all duration-500"
                            style={{
                              width: showCount ? `${pct}%` : "35%",
                              background: "var(--gradient-brand)",
                            }}
                          />
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
