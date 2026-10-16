"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/AuthShell";
import { createClient } from "@/lib/supabase/client";

export default function UpdatePasswordPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("비밀번호가 일치하지 않아요.");
      return;
    }
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: err } = await supabase.auth.updateUser({ password });
    if (err) {
      setError("비밀번호를 변경하지 못했어요. 링크가 만료되었을 수 있어요.");
      setBusy(false);
      return;
    }
    router.push("/app");
    router.refresh();
  }

  return (
    <AuthShell>
      <h1 className="text-2xl font-bold leading-tight">새 비밀번호 설정</h1>
      <p className="mt-1.5 text-text-2" style={{ fontSize: "var(--fs-body)" }}>새로 사용할 비밀번호를 입력하세요.</p>
      <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="label">새 비밀번호</span>
          <input type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} className="field" placeholder="6자 이상" autoComplete="new-password" />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="label">새 비밀번호 확인</span>
          <input type="password" required minLength={6} value={confirm} onChange={(e) => setConfirm(e.target.value)} className="field" placeholder="다시 입력" autoComplete="new-password" />
        </label>
        {error && <p className="text-sm" style={{ color: "var(--danger)" }}>{error}</p>}
        <button type="submit" disabled={busy} className="btn-primary mt-1 w-full">{busy ? "변경 중…" : "비밀번호 변경"}</button>
      </form>
    </AuthShell>
  );
}
