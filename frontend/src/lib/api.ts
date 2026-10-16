import type {
  EditorConfigResponse,
  GlossaryReportResponse,
  HealthResponse,
  JobStatus,
  ProperNounReportResponse,
  ReplaceResponse,
  ReplaceRule,
  ResultResponse,
  RetranslateResponse,
  SaveGlossaryTermResponse,
  SegmentsResponse,
  StartResponse,
} from "./types";
import { createClient } from "@/lib/supabase/client";

// Attach the Supabase access token so the FastAPI backend can authorize the request.
async function authedFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();
  if (supabaseUrl && supabaseKey) {
    const { data } = await createClient().auth.getSession();
    const token = data.session?.access_token;
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(input, { ...init, headers });
}

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetch("/api/health").then((r) => asJson<HealthResponse>(r)),

  // Starts the background job; returns at once with a doc_id to poll. `styleDomain` (optional)
  // applies that domain's style references to the translation.
  upload: (
    file: File,
    sourceLang: string,
    targetLang: string,
    glossary?: File | null,
    styleDomain?: string,
  ) => {
    const fd = new FormData();
    fd.append("pdf", file);
    fd.append("source_lang", sourceLang);
    fd.append("target_lang", targetLang);
    if (glossary) fd.append("glossary", glossary);
    if (styleDomain) fd.append("style_domain", styleDomain);
    return authedFetch("/api/documents", { method: "POST", body: fd }).then((r) =>
      asJson<StartResponse>(r),
    );
  },

  // Style references (번역 메모리 / 스타일 라이브러리), scoped per user + domain.
  styleDomains: () =>
    authedFetch("/api/style/domains").then((r) => asJson<{ domains: StyleDomain[] }>(r)),

  styleReferences: (domain: string) =>
    authedFetch(`/api/style/references?domain=${encodeURIComponent(domain)}`).then((r) =>
      asJson<{ domain: string; references: StyleReference[] }>(r),
    ),

  // Detect a reference's bilingual format + preview style/TM counts (no storage/embedding).
  analyzeStyleReference: (args: { target: File; source?: File | null }) => {
    const fd = new FormData();
    fd.append("target", args.target);
    if (args.source) fd.append("source", args.source);
    return authedFetch("/api/style/references/analyze", { method: "POST", body: fd }).then((r) =>
      asJson<StyleAnalysis>(r),
    );
  },

  // All 원문↔번역 pairs for one reference file (no storage/embedding).
  styleReferencePairs: (args: { target: File; source?: File | null; mode?: string }) => {
    const fd = new FormData();
    fd.append("target", args.target);
    if (args.source) fd.append("source", args.source);
    if (args.mode) fd.append("mode", args.mode);
    return authedFetch("/api/style/references/pairs", { method: "POST", body: fd }).then((r) =>
      asJson<StylePairs>(r),
    );
  },

  // Upload a past translation into a domain. `target` (target-language) is required; an
  // optional `source` original (or interleaved bilingual `target`) yields Translation-Memory
  // pairs. `mode` overrides auto-detection (interleaved/columns/sequential/paired_files/target_only).
  uploadStyleReference: (args: {
    domain: string;
    target: File;
    source?: File | null;
    targetLang?: string;
    sourceLang?: string;
    name?: string;
    mode?: string;
  }) => {
    const fd = new FormData();
    fd.append("domain", args.domain);
    fd.append("target", args.target);
    if (args.source) fd.append("source", args.source);
    fd.append("target_lang", args.targetLang ?? "en");
    fd.append("source_lang", args.sourceLang ?? "ko");
    if (args.name) fd.append("name", args.name);
    if (args.mode) fd.append("mode", args.mode);
    return authedFetch("/api/style/references", { method: "POST", body: fd }).then((r) =>
      asJson<{
        id: string;
        domain: string;
        segment_count: number;
        // Best-effort Translation-Memory indexing: style is always saved; if pair embedding
        // failed (e.g. provider rate limit) `indexed` is false with the captured `error`.
        tm: { indexed: boolean; pairs: number; error: string | null };
      }>(r),
    );
  },

  deleteStyleReference: (refId: string) =>
    authedFetch(`/api/style/references/${encodeURIComponent(refId)}`, {
      method: "DELETE",
    }).then((r) => asJson<{ ok: boolean }>(r)),

  // Detect the document's main language to default the translation direction. Best-effort:
  // the backend returns {en, ko} on any failure, so the caller can use it unconditionally.
  detectLanguage: (file: File) => {
    const fd = new FormData();
    fd.append("pdf", file);
    return authedFetch("/api/detect-language", { method: "POST", body: fd }).then((r) =>
      asJson<{ source_lang: string; target_lang: string }>(r),
    );
  },

  status: (docId: string, lang: string) =>
    authedFetch(`/api/documents/${docId}/status?lang=${encodeURIComponent(lang)}`).then(
      (r) => asJson<JobStatus>(r),
    ),

  result: (docId: string, lang: string) =>
    authedFetch(`/api/documents/${docId}/result?lang=${encodeURIComponent(lang)}`).then(
      (r) => asJson<ResultResponse>(r),
    ),

  // OnlyOffice editor config (the .docx is the source of truth; saving is handled by
  // the Document Server's callback, not a client-side PUT).
  editorConfig: (docId: string, lang: string, bookmark?: string | null) =>
    authedFetch(
      `/api/documents/${docId}/editor-config?lang=${encodeURIComponent(lang)}` +
        (bookmark ? `&bookmark=${encodeURIComponent(bookmark)}` : ""),
    ).then((r) => asJson<EditorConfigResponse>(r)),

  segments: (docId: string, lang: string) =>
    authedFetch(
      `/api/documents/${docId}/segments?lang=${encodeURIComponent(lang)}`,
    ).then((r) => asJson<SegmentsResponse>(r)),

  retranslateSegment: (
    docId: string,
    body: { lang: string; id: string; preset?: string | null; guidance?: string | null },
  ) =>
    authedFetch(`/api/documents/${docId}/retranslate-segment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => asJson<RetranslateResponse>(r)),

  // Force a re-render of the .docx from the current model so the OnlyOffice editor can
  // reflect an applied re-translation on reload (Community Edition has no connector).
  render: (docId: string, lang: string) =>
    authedFetch(`/api/documents/${docId}/render?lang=${encodeURIComponent(lang)}`, {
      method: "POST",
    }).then((r) => asJson<{ ok: boolean }>(r)),

  // Force the DS to save the live editor session NOW so leaving 문서 편집 reflects the edit
  // immediately. Returns {ok} (DS accepted or nothing to save) — the caller falls back to a
  // plain switch + poll when !ok. error: DS CommandService code (0 ok, 4 no-changes, 1 no
  // session, 6 bad token, -1 DS unreachable).
  forcesave: (docId: string, lang: string) =>
    authedFetch(`/api/documents/${docId}/forcesave?lang=${encodeURIComponent(lang)}`, {
      method: "POST",
    }).then((r) => asJson<{ ok: boolean; error: number }>(r)),

  // Per-rule occurrence counts for a set of replacements (preview before applying).
  replacePreview: (docId: string, lang: string, rules: ReplaceRule[]) =>
    authedFetch(`/api/documents/${docId}/replace/preview?lang=${encodeURIComponent(lang)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules }),
    }).then((r) => asJson<ReplaceResponse>(r)),

  // Apply the replacements to the .docx (preserving manual edits) + mirror the model.
  replaceApply: (docId: string, lang: string, rules: ReplaceRule[]) =>
    authedFetch(`/api/documents/${docId}/replace?lang=${encodeURIComponent(lang)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules }),
    }).then((r) => asJson<ReplaceResponse>(r)),

  // 용어 관리: per-term applied/missed audit of the uploaded glossary.
  glossaryReport: (docId: string, lang: string) =>
    authedFetch(`/api/documents/${docId}/glossary-report?lang=${encodeURIComponent(lang)}`).then(
      (r) => asJson<GlossaryReportResponse>(r),
    ),

  // Proper nouns translated but not in the glossary (고유명사 검수) — review pass.
  properNounReport: (docId: string, lang: string) =>
    authedFetch(`/api/documents/${docId}/proper-nouns?lang=${encodeURIComponent(lang)}`).then(
      (r) => asJson<ProperNounReportResponse>(r),
    ),

  sourcePreviewUrl: (docId: string) => `/api/documents/${docId}/source-preview`,

  // Edit an existing term (unifies translation) or add a new one (apply=re-translate now).
  saveGlossaryTerm: (
    docId: string,
    lang: string,
    body: { source: string; target: string; apply?: boolean },
  ) =>
    authedFetch(`/api/documents/${docId}/glossary/term?lang=${encodeURIComponent(lang)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => asJson<SaveGlossaryTermResponse>(r)),

  downloadUrl: (docId: string, lang: string) =>
    `/api/documents/${docId}/download?lang=${encodeURIComponent(lang)}`,

  // Editor-parity translated-page viewer: the DS ConvertService renders these page images
  // with the SAME engine the OnlyOffice editor uses, so they match it pixel-for-pixel.
  // Capability-authed like /docx (no bearer dep on the backend) — the unguessable doc_id in
  // the URL is the security boundary, since the page images are also loaded as plain
  // <img src> (no Authorization header) — so these use plain fetch, not authedFetch.
  tpages: (docId: string, lang: string) =>
    fetch(`/api/documents/${docId}/tpages?lang=${encodeURIComponent(lang)}`).then((r) =>
      asJson<TPagesResponse>(r),
    ),

  // bbox is a fraction (0..1) of its page, joined by segment id onto GET /segments.
  tsegments: (docId: string, lang: string) =>
    fetch(`/api/documents/${docId}/tsegments?lang=${encodeURIComponent(lang)}`).then((r) =>
      asJson<TSegmentsResponse>(r),
    ),

  // Cheap freshness signal for the translated pane: the docx's version key (same hash that
  // gates tpages/tsegments' cache) WITHOUT triggering a conversion — a pure backend stat. Used
  // to detect "the edit landed" by polling for a version change instead of segment text (which
  // never changes for a formatting-only edit). version is null before the docx exists.
  tversion: (docId: string, lang: string) =>
    fetch(`/api/documents/${docId}/tversion?lang=${encodeURIComponent(lang)}`).then((r) =>
      asJson<{ version: string | null }>(r),
    ),

  // Per-term fraction bboxes in the translated pages (용어 하이라이트 위치 조회). The backend
  // truncates a single request's `terms` to 50, so chunk here: callers may pass the full
  // glossary + proper-noun term list combined and still get every term's hits back.
  tfind: async (docId: string, lang: string, terms: string[]): Promise<TFindResponse> => {
    const TFIND_BATCH_SIZE = 50;
    const batches: string[][] = [];
    for (let i = 0; i < terms.length; i += TFIND_BATCH_SIZE) {
      batches.push(terms.slice(i, i + TFIND_BATCH_SIZE));
    }
    if (batches.length === 0) return { hits: {} };
    const responses = await Promise.all(
      batches.map((batch) =>
        fetch(`/api/documents/${docId}/tfind?lang=${encodeURIComponent(lang)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ terms: batch }),
        }).then((r) => asJson<TFindResponse>(r)),
      ),
    );
    const hits: Record<string, TBbox[]> = {};
    for (const r of responses) Object.assign(hits, r.hits);
    return { hits };
  },
};

export type StyleDomain = { domain: string; references: number; segments: number };

export type StyleMode =
  | "interleaved"
  | "columns"
  | "sequential"
  | "paired_files"
  | "target_only"
  | "source_only";

export type StyleAnalysis = {
  mode: StyleMode;
  style_count: number;
  pair_count: number;
  sample_pairs: { source: string; target: string }[];
  sample_style: string[];
  has_pairs: boolean;
  estimated?: boolean;
  message?: string;
};

export type StylePairs = {
  mode: StyleMode;
  estimated: boolean;
  count: number;
  pairs: { source: string; target: string }[];
};

export type StyleReference = {
  id: string;
  name: string;
  lang_src: string;
  lang_tgt: string;
  has_pairs: boolean;
  segment_count: number;
  created_at?: string;
};

export const LANGS: Record<string, string> = {
  ko: "한국어",
  en: "English",
  ja: "日本語",
  zh: "中文",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
  vi: "Tiếng Việt",
};

// --- Editor-parity translated-page viewer (see api.tpages/tsegments/tfind above) ----------
// A bbox is a fraction (0..1) of its page — same convention as SourceBBox in ./types.
export type TBbox = { page: number; x: number; y: number; w: number; h: number };
// Interaction-layer overlay: amber = 용어(glossary) match, indigo = active 고유명사(proper noun).
export type THighlight = { color: "amber" | "indigo"; boxes: TBbox[] };

export type TPagesResponse = {
  version: string;
  page_w: number;
  page_h: number;
  pages: { n: number; url: string }[];
};

export type TSegmentsResponse = {
  version: string;
  segments: { id: string; bbox: TBbox | null }[];
};

export type TFindResponse = {
  hits: Record<string, TBbox[]>;
};
