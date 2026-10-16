// Mirrors backend/models.py (the semantic-model contract).
// Translation/editing only ever mutates `text`/`caption`/cell `text`; markers,
// image refs, and structure stay fixed.

export type BlockType =
  | "heading"
  | "paragraph"
  | "list_item"
  | "table"
  | "figure"
  | "caption";

export interface HeadingBlock {
  type: "heading";
  level: number;
  marker?: string | null;
  text: string;
  source_span_ids: string[];
}

export interface ParagraphBlock {
  type: "paragraph";
  text: string;
  source_span_ids: string[];
}

export interface ListItemBlock {
  type: "list_item";
  marker: string;
  level: number;
  text: string;
  source_span_ids: string[];
}

export interface TableCell {
  text: string;
  rowspan: number;
  colspan: number;
  is_header: boolean;
  source_span_ids: string[];
}

export interface TableBlock {
  type: "table";
  rows: TableCell[][];
  caption?: string | null;
  source_span_ids: string[];
}

export interface FigureBlock {
  type: "figure";
  image_ref: string;
  width?: number | null;
  height?: number | null;
}

export interface CaptionBlock {
  type: "caption";
  text: string;
  source_span_ids: string[];
}

export type Block =
  | HeadingBlock
  | ParagraphBlock
  | ListItemBlock
  | TableBlock
  | FigureBlock
  | CaptionBlock;

export interface SemanticDocument {
  source_lang: string;
  target_lang?: string | null;
  title?: string | null;
  blocks: Block[];
}

export interface ReconcileReport {
  total_spans: number;
  covered_spans: number;
  coverage: number;
  missing_span_ids: string[];
  missing_texts: string[];
  warnings: string[];
}

// Office (.docx/.doc) and HWP ingest skip the vision reconciler, so their report is a
// lightweight summary instead of a coverage/gate report. The compare screen narrows on
// `missing_span_ids` to tell the two apart.
export interface IngestReport {
  ingest: string;
  blocks: number;
}

// POST /api/documents now starts a background job and returns immediately.
export interface StartResponse {
  doc_id: string;
  source_lang: string;
  target_lang: string;
  glossary_terms?: number;
}

// GET /api/documents/{id}/status — polled by the processing screen.
export type JobStage = "extract" | "analyze" | "translate" | "done";

export interface JobStatus {
  status: "running" | "done" | "error";
  stage: JobStage;
  done: number;
  total: number;
  error: string | null;
  source_lang?: string;
  target_lang?: string;
}

// GET /api/documents/{id}/result?lang= — consumed by the compare screen.
// The translation itself is edited in the OnlyOffice editor (a .docx), not here; this
// payload carries the original-page images + metadata for the left pane and header.
export interface ResultResponse {
  doc_id: string;
  title: string | null;
  source_lang: string;
  target_lang: string | null;
  report: ReconcileReport | IngestReport | null;
  assets_prefix: string;
  page_images: string[];
  page_size: { width_mm: number; height_mm: number } | null;
  // HWP uploads expose the original as a rendered HTML page (hwp5html) instead of
  // page images; the compare screen iframes it as the left "원본" pane.
  source_html?: string | null;
}

// GET /api/documents/{id}/segments?lang= — the 재번역 모드 sentence list.
export interface SourceBBox {
  page: number; // 0-based page index in page_images
  x: number;    // all fractions of the page (0..1)
  y: number;
  w: number;
  h: number;
}

export interface Segment {
  id: string;             // stable seg-id string (used by retranslate + click-select)
  block_id: string;       // stable block-id string → editor bookmark "blk{block_id}"
  index: number;          // ordinal (display only; do NOT use as identity)
  source: string;
  translated: string;
  source_bbox: SourceBBox | null; // null for HWP/office (no source.pdf) or unlocated
  added: boolean;         // true for paragraphs inserted in the editor (no source span)
}

export interface SegmentsResponse {
  lang: string;
  segments: Segment[];
}

// POST /api/documents/{id}/retranslate-segment
export interface RetranslateResponse {
  id: string;
  old_text: string;
  new_text: string;
}

// GET /api/documents/{id}/editor-config?lang= — config for new DocsAPI.DocEditor(...).
// `config` is passed verbatim to the OnlyOffice editor (it includes a signed `token`);
// `document_server_url` is where the browser loads the editor's api.js + iframe from.
export interface EditorConfigResponse {
  document_server_url: string;
  config: Record<string, unknown>;
}

export interface HealthResponse {
  ok: boolean;
  mode: string;
}

// POST /api/documents/{id}/replace[/preview]?lang= — 용어 일괄변경 (bulk term replace).
export type MatchMode = "smart" | "exact" | "loose";

export interface ReplaceRule {
  find: string;
  replace: string;
  match: MatchMode;
  save: boolean;
}

export interface ReplaceResult {
  find: string;
  replace: string;
  count: number;
}

export interface ReplaceResponse {
  results: ReplaceResult[];
  total: number; // sum of all per-rule counts (occurrences replaced/matched)
}

// GET /api/documents/{id}/glossary-report?lang= — 용어 관리 적용 추적.
// applied: 원어 있고 번역어 적용됨 · partial: 일부만 · missed: 원어 있는데 번역어 없음(미반영 추정)
// · absent: 원어가 원문에 등장하지 않음.
export type GlossaryTermStatus = "applied" | "partial" | "missed" | "absent";

export interface GlossaryLocation {
  segment_index: number;
  block_id: string | null; // stable block-id → preview anchor "blk{block_id}" / editor bookmark
  source_bbox: SourceBBox | null; // PDF only; null for HWP/office
  applied: boolean;
}

export interface GlossaryTermReport {
  source: string;
  target: string;
  source_count: number;
  applied_count: number;
  status: GlossaryTermStatus;
  locations: GlossaryLocation[];
}

export interface GlossaryReportResponse {
  lang: string;
  terms: GlossaryTermReport[];
}

// Proper-noun review: a proper noun translated but NOT in the glossary (고유명사 검수).
export interface ProperNounReport {
  source: string;
  translated: string; // how it was rendered in the translation
  category: string; // brand | product | organization | person | place | other (display-only)
  count: number;
  locations: GlossaryLocation[]; // reuses the glossary location shape; `applied` is always false
}

export interface ProperNounReportResponse {
  lang: string;
  terms: ProperNounReport[];
}

// POST /api/documents/{id}/glossary/term?lang= response.
export interface SaveGlossaryTermResponse {
  status: string; // "updated" | "added"
  replaced?: number; // update path: occurrences unified in the translation
  retranslated?: number; // add+apply path: segments re-translated
}
