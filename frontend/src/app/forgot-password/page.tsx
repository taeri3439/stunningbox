"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { createClient } from "@/lib/supabase/client";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const supabase = createClient();
    // Recovery email lands on /auth/confirm (verifyOtp type=recovery) → /update-password.
    try {
      await supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${location.origin}/auth/confirm?next=/update-password`,
      });
    } catch {
      // Swallow — stay neutral (don't reveal existence) and don't freeze the form.
    }
    setSent(true); // Always neutral — do not reveal whether the email exists.
  }

  return (
    <AuthShell>
      {sent ? (
        <div>
          <h1 className="text-2xl font-bold leading-tight">재설정 메일을 보냈어요</h1>
          <p className="mt-3 text-text-2" style={{ fontSize: "var(--fs-body)" }}>
            메일이 도착했다면 링크를 눌러 새 비밀번호를 설정하세요.
          </p>
          <Link href="/login" className="btn-secondary mt-6 inline-flex">로그인으로</Link>
        </div>
      ) : (
        <>
          <h1 className="text-2xl font-bold leading-tight">비밀번호 재설정</h1>
          <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>가입한 이메일로 재설정 링크를 보내드려요.</p>
          <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="label">이메일</span>
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="field" placeholder="you@example.com" autoComplete="email" />
            </label>
            <button type="submit" disabled={busy} className="btn-primary mt-1 w-full">{busy ? "보내는 중…" : "재설정 메일 보내기"}</button>
          </form>
          <p className="mt-6 text-center text-text-2" style={{ fontSize: "var(--fs-body)" }}>
            <Link href="/login" className="font-semibold" style={{ color: "var(--primary-hover)" }}>로그인으로 돌아가기</Link>
          </p>
        </>
      )}
    </AuthShell>
  );
}
