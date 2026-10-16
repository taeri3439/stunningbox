"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { createClient } from "@/lib/supabase/client";
import { LANGS } from "@/lib/api";

type DocRow = {
  id: string;
  title: string | null;
  status: string;
  target_lang: string | null;
  created_at: string;
};

// Reads the signed-in user's documents straight from Supabase (RLS returns only own rows).
export function MyDocuments() {
  const [docs, setDocs] = useState<DocRow[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
    const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();
    if (!url || !key) {
      setDocs([]);
      return;
    }
    const supabase = createClient();
    supabase
      .from("documents")
      .select("id,title,status,target_lang,created_at")
      .order("created_at", { ascending: false })
      .then(({ data, error }) => {
        // Distinguish a real failure from a genuinely empty list (don't show a false empty state).
        if (error) setFailed(true);
        setDocs((data as DocRow[]) ?? []);
      });
  }, []);

  if (docs === null) return <p className="slug mt-8 px-1">불러오는 중…</p>;

  return (
    <section className="mt-8">
      <h2 className="label mb-4 px-1">내 문서</h2>
      {failed ? (
        <p className="text-text-2" style={{ fontSize: "var(--fs-body)", color: "var(--danger)" }}>
          문서 목록을 불러오지 못했어요. 잠시 후 새로고침해 주세요.
        </p>
      ) : docs.length === 0 ? (
        /* Soft dashed panel — visually tied to the card column above */
        <div
          className="rounded-lg border border-dashed px-6 py-8 text-center"
          style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
        >
          <p className="label" style={{ color: "var(--text-2)" }}>
            아직 문서가 없어요
          </p>
          <p className="slug mt-1" style={{ color: "var(--text-3)" }}>
            위에서 올려보세요
          </p>
        </div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {docs.map((d) => (
            <li key={d.id}>
              <Link
                href={`/c/${d.id}?lang=${encodeURIComponent(d.target_lang ?? "en")}`}
                className="ticket flex items-center justify-between gap-3 p-4 transition-transform hover:-translate-y-0.5"
              >
                <span className="min-w-0">
                  <span className="block truncate font-semibold">{d.title || d.id}</span>
                  <span className="slug">
                    {LANGS[d.target_lang ?? ""] ?? d.target_lang} ·{" "}
                    {new Date(d.created_at).toLocaleDateString("ko-KR")}
                  </span>
                </span>
                <span
                  className="slug shrink-0"
                  style={{
                    color:
                      d.status === "done"
                        ? "var(--ok)"
                        : d.status === "error"
                          ? "var(--danger)"
                          : "var(--text-3)",
                  }}
                >
                  {d.status === "done" ? "완료" : d.status === "error" ? "오류" : "변환 중"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
