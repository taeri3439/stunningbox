# Auto-detect main language → smart default translation direction

## Context / why

On the upload page the user manually picks source + target language before uploading. For
this Korean↔English-centric tool the sensible default is almost always: a non-Korean
document → translate **to Korean**, a Korean document → translate **to English**. We want
to detect the document's main language on file-select and pre-fill the dropdowns with that
direction, while keeping them fully adjustable (before upload and on the viewer afterward).

## Flow

1. User selects a file on `frontend/src/app/page.tsx`.
2. Frontend `POST`s the file to a new lightweight endpoint `POST /api/detect-language`.
3. Backend extracts a **text sample** (first ~1–2 pages), detects the main language, and
   returns `{ source_lang, target_lang }`.
4. Frontend sets `sourceLang`/`targetLang` from the response. The user can change either
   before clicking "번역 시작" (and after, via the existing viewer lang switch).

## Backend

New module `backend/pipeline/lang_detect.py`:

- `sample_text(path: str) -> str` — cheap text sample, no vision/render:
  - `.pdf` → PyMuPDF: concatenate `page.get_text()` for the first ~2 pages (cap ~4000 chars).
  - `.docx`/`.doc` → python-docx paragraph text (first ~4000 chars). (`.doc` → reuse
    `convert.to_docx` only if needed; otherwise skip and default.)
  - `.hwp` → **shortcut**: return `""` and let `detect_lang` default to `ko` (HWP is a
    Korean word-processor format; skip extraction).
- `detect_lang(text: str, *, is_hwp: bool = False) -> str` — returns a supported source
  language code:
  - If `is_hwp` → `"ko"`.
  - **Hangul guard:** if Hangul chars / (Hangul+Latin+Kana+Han) ≥ ~0.15 → `"ko"`
    (Hangul is unambiguous and the key case; libraries misfire on short/mixed samples).
  - **Kana guard:** if Hiragana/Katakana present meaningfully → `"ja"`.
  - Else run `langdetect` (with `DetectorFactory.seed = 0` for determinism) and map the
    ISO code to the supported set {ko, en, ja, zh, es, fr, de, vi}; `zh-cn`/`zh-tw` → `zh`.
  - Unknown / empty / detection error → default `"en"`.
- `default_direction(source: str) -> tuple[str, str]` → `(source, "en" if source == "ko" else "ko")`.

New endpoint in `backend/main.py`: `POST /api/detect-language` (multipart `pdf` file,
same field name + extension validation as `/api/documents`). Writes to a temp file, calls
`sample_text` + `detect_lang` + `default_direction`, returns `{source_lang, target_lang}`.
Never raises to the client on detection failure — falls back to `{en, ko}`.

Dependency: add `langdetect` to `backend/requirements.txt`.

## Frontend

- `frontend/src/lib/api.ts`: `detectLanguage(file) -> Promise<{source_lang, target_lang}>`.
- `frontend/src/app/page.tsx`: on file-select, call `detectLanguage`; on success set
  `sourceLang` + `targetLang`. Show a small "언어 감지 중…" hint while pending. Silent
  fallback (keep current dropdown values) on error. Re-runs each time a new file is picked;
  manual dropdown changes persist until the next file-select.

## Edge cases

- Detection failure, empty/garbled/encrypted PDF, unsupported text → keep current defaults
  (never block the upload).
- HWP → always `ko → en` without extraction.
- A Korean document with heavy English technical terms → Hangul guard still yields `ko`.

## Testing

`backend/tests/test_lang_detect.py`:
- `detect_lang`: Korean sample → ko; English → en; Japanese (kana) → ja; a Hangul-heavy
  sample with English terms → ko (guard overrides); empty → en; `is_hwp=True` → ko.
- `default_direction`: ko → (ko, en); en → (en, ko); ja → (ja, ko).
- `sample_text`: extension routing (a tiny in-memory docx → returns its text; `.hwp` path
  → returns "" / handled as ko). PDF sample via a generated 1-page PDF (PyMuPDF).
- Endpoint smoke (offline): a small docx upload → returns a `{source_lang, target_lang}`
  shape with a valid direction.

## Out of scope

- Per-paragraph / mixed-language handling (we pick one main language).
- Auto-translating without user confirmation (the dropdowns remain the control).
