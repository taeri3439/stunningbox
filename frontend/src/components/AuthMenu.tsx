"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

function supabaseConfigured(): boolean {
  if (process.env.NEXT_PUBLIC_DEV_NO_AUTH === "1") return false;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();
  return Boolean(url && key);
}

// Signed-in identity + sign-out, for the app/viewer chrome.
// Local dev (DEV_NO_AUTH / empty Supabase keys) renders nothing so the header
// does not construct a browser client that throws without a project URL.
export function AuthMenu() {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  const enabled = supabaseConfigured();

  useEffect(() => {
    if (!enabled) return;
    const supabase = createClient();
    supabase.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      setEmail(session?.user?.email ?? null);
    });
    return () => sub.subscription.unsubscribe();
  }, [enabled]);

  async function signOut() {
    if (!enabled) return;
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  if (!enabled) return null;

  return (
    <div className="flex items-center gap-2.5">
      {email && <span className="slug hidden max-w-[12rem] truncate sm:inline">{email}</span>}
      <button type="button" onClick={signOut} className="btn-secondary">로그아웃</button>
    </div>
  );
}
