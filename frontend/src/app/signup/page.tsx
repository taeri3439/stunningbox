"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { createClient } from "@/lib/supabase/client";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: err } = await supabase.auth.signUp({
      email,
      password,
      options: { emailRedirectTo: `${location.origin}/auth/confirm` },
    });
    if (err) {
      // Neutral message — do not reveal whether the email is already registered
      // (account enumeration). Supabase obfuscates existing emails when email
      // confirmation is on, so the success path stays neutral too.
      setError("가입에 실패했어요. 입력을 확인하고 다시 시도해 주세요.");
      setBusy(false);
      return;
    }
    setSent(true);
  }

  return (
    <AuthShell>
      {sent ? (
        <div>
          <h1 className="text-2xl font-bold leading-tight">확인 메일을 보냈어요</h1>
          <p className="mt-3 text-text-2" style={{ fontSize: "var(--fs-body)" }}>
            {email}로 보낸 메일의 링크를 눌러 가입을 완료하세요.
          </p>
          <Link href="/login" className="btn-secondary mt-6 inline-flex">로그인으로</Link>
        </div>
      ) : (
        <>
          <h1 className="text-2xl font-bold leading-tight">계정 만들기</h1>
          <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>스터닝박스를 무료로 시작하세요.</p>
          <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="label">이메일</span>
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="field" placeholder="you@example.com" autoComplete="email" />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="label">비밀번호</span>
              <input type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} className="field" placeholder="6자 이상" autoComplete="new-password" />
            </label>
            {error && <p className="text-sm" style={{ color: "var(--danger)" }}>{error}</p>}
            <button type="submit" disabled={busy} className="btn-primary mt-1 w-full">{busy ? "가입 중…" : "가입하기"}</button>
          </form>
          <p className="mt-6 text-center text-text-2" style={{ fontSize: "var(--fs-body)" }}>
            이미 계정이 있으신가요? <Link href="/login" className="font-semibold" style={{ color: "var(--primary-hover)" }}>로그인</Link>
          </p>
        </>
      )}
    </AuthShell>
  );
}
