"use client";

import { Suspense, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AuthShell } from "@/components/AuthShell";
import { createClient } from "@/lib/supabase/client";
import { safeRedirect } from "@/lib/safeRedirect";

function LoginInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const redirect = safeRedirect(sp.get("redirect"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // Surface failures redirected here by /auth/callback (?error=oauth) and /auth/confirm (?error=confirm).
  const [error, setError] = useState<string | null>(() => {
    const e = sp.get("error");
    if (e === "oauth") return "Google 로그인에 실패했어요. 다시 시도해 주세요.";
    if (e === "confirm") return "인증 링크가 만료되었거나 올바르지 않아요. 다시 시도해 주세요.";
    return null;
  });
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: err } = await supabase.auth.signInWithPassword({ email, password });
    if (err) {
      setError(
        err.message.includes("Email not confirmed")
          ? "이메일 인증이 완료되지 않았어요. 메일의 링크를 확인해 주세요."
          : "이메일 또는 비밀번호가 올바르지 않아요.",
      );
      setBusy(false);
      return;
    }
    router.push(redirect);
    router.refresh();
  }

  async function onGoogle() {
    setError(null);
    const supabase = createClient();
    const { error: err } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${location.origin}/auth/callback?redirect=${encodeURIComponent(redirect)}` },
    });
    if (err) setError("Google 로그인을 시작하지 못했어요.");
  }

  return (
    <>
      <h1 className="text-2xl font-bold leading-tight">다시 오신 걸 환영해요</h1>
      <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>스터닝박스에 로그인하세요.</p>

      <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="label">이메일</span>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="field" placeholder="you@example.com" autoComplete="email" />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="label flex items-center justify-between">
            비밀번호
            <Link href="/forgot-password" className="text-sm font-semibold" style={{ color: "var(--primary-hover)" }}>비밀번호를 잊으셨나요?</Link>
          </span>
          <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} className="field" placeholder="••••••••" autoComplete="current-password" />
        </label>
        {error && <p className="text-sm" style={{ color: "var(--danger)" }}>{error}</p>}
        <button type="submit" disabled={busy} className="btn-primary mt-1 w-full">{busy ? "로그인 중…" : "로그인"}</button>
      </form>

      <div className="my-5 flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="slug">또는</span>
        <span className="h-px flex-1 bg-border" />
      </div>

      <button type="button" onClick={onGoogle} className="btn-secondary w-full">Google로 계속</button>

      <p className="mt-6 text-center text-text-2" style={{ fontSize: "var(--fs-body)" }}>
        계정이 없으신가요? <Link href="/signup" className="font-semibold" style={{ color: "var(--primary-hover)" }}>가입</Link>
      </p>
    </>
  );
}

export default function LoginPage() {
  return (
    <AuthShell>
      {/* useSearchParams must sit inside a Suspense boundary for static generation. */}
      <Suspense>
        <LoginInner />
      </Suspense>
    </AuthShell>
  );
}
