"""FastAPI app: upload a PDF, translate it, edit blocks, view/download.

The frontend (Next.js) consumes the semantic model as JSON so blocks are editable
in the browser. Edited models are saved back and used for docx export. HTML is still
offered for quick read-only viewing.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import threading
import uuid

from dotenv import load_dotenv

# Load backend/.env (ANTHROPIC_API_KEY, optional ANALYZER_MODEL/TRANSLATOR_MODEL) BEFORE
# importing the pipeline, which reads those env vars at module load. Explicit path so it
# works regardless of the process working directory.
# override=True: backend/.env wins over stale Windows/Railway shell ANTHROPIC_* vars.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

import auth
import documents_store
import jobs
import onlyoffice
from pipeline import ds_convert, pdf_map, process
from pipeline.translator import build_guidance, parse_glossary_csv
from pipeline.text_replace import ReplaceRule
from renderers.docx_renderer import render_docx
from renderers.html_renderer import render_html

STORAGE = os.environ.get("STORAGE_DIR", os.path.join(os.path.dirname(__file__), "storage"))
os.makedirs(STORAGE, exist_ok=True)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class RetranslateBody(BaseModel):
    lang: str
    id: str
    preset: str | None = None
    guidance: str | None = None


class ReplaceRequest(BaseModel):
    rules: list[ReplaceRule] = Field(..., max_length=1000)


class GlossaryTermBody(BaseModel):
    source: str
    target: str
    apply: bool = False


def _assets_prefix(doc_id: str) -> str:
    return f"/api/documents/{doc_id}/assets"


def _load_report(doc_id: str) -> dict | None:
    path = os.path.join(process.doc_dir(STORAGE, doc_id), "report.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _ensure_docx(doc_id: str, lang: str | None) -> str:
    """Path to the .docx the OnlyOffice editor opens/saves. Once the editor has saved,
    this file IS the source of truth; before any edit we render it from the model."""
    path = process.output_docx_path(STORAGE, doc_id, lang)
    if not os.path.exists(path):
        doc = process.get_export_model(STORAGE, doc_id, lang)
        render_docx(doc, path, process.asset_dir(STORAGE, doc_id))
    return path


def _sync_model_from_saved_docx(doc_id: str, lang: str | None) -> None:
    """After the DS saves output.{lang}.docx, reflect its text edits into the model so EVERY
    view + operation sees them. We write the synced model to translated.{lang}.json — the
    single live model that build_segments (재번역 list), the 용어/고유명사 audits, retranslate,
    and render (get_export_model) all read — so the edit is reflected coherently everywhere
    (writing to edited.{lang}.json instead would only reach render, leaving the segment list +
    audits stale, since build_segments/retranslate read translated.json). retranslate + glossary
    already persist their edits to translated.json, so this is consistent. Phase 1:
    structure-preserving text sync. Best-effort — never breaks the save."""
    from pipeline.docx_sync import apply_docx_edits_to_model
    try:
        # Resolve eff_lang BEFORE loading the model so we read the same file we write.
        # translated.json is the single live model; get_export_model may read edited.json
        # which would be read-but-not-written (asymmetry).
        if lang:
            eff_lang = lang
        else:
            eff_lang = process.load_source_model(STORAGE, doc_id).source_lang
        out = process.output_docx_path(STORAGE, doc_id, eff_lang)
        if not os.path.exists(out):
            return
        model = process.get_translated_model(STORAGE, doc_id, eff_lang)
        synced = apply_docx_edits_to_model(model, out)
        process.save_translated_model(STORAGE, doc_id, eff_lang, synced)
    except Exception:  # noqa: BLE001 — never fail the editor save on a sync hiccup
        import logging
        logging.getLogger("uvicorn.error").warning(
            "model sync from saved docx failed for %s/%s", doc_id, lang, exc_info=True)


def _issued_key_path(doc_id: str, lang: str) -> str:
    return os.path.join(process.doc_dir(STORAGE, doc_id), f"onlyoffice.{lang}.key")


def _write_issued_key(doc_id: str, lang: str, key: str) -> None:
    """Remember the document key the editor opened with, so a later force-save targets the
    same live DS session. document_key() recomputed after the file changed would mismatch
    (→ CommandService error 1). Best-effort — never break editor open."""
    try:
        with open(_issued_key_path(doc_id, lang), "w", encoding="utf-8") as fh:
            fh.write(key)
    except OSError:
        pass


def _read_issued_key(doc_id: str, lang: str) -> str | None:
    try:
        with open(_issued_key_path(doc_id, lang), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


app = FastAPI(title="PDF Skeleton-Preserving Translator")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# The backend serves only /api/*; visiting any other path (e.g. the bare domain)
# bounces to the frontend app so users don't land on a JSON 404. Override the
# target with the FRONTEND_URL env var if the app domain changes.
FRONTEND_URL = os.environ.get(
    "FRONTEND_URL", "https://frontend-production-ea454.up.railway.app"
)


@app.get("/")
def root():
    return RedirectResponse(FRONTEND_URL)


@app.get("/api/health")
def health():
    from pipeline.llm_provider import llm_configured, resolve_llm_provider

    provider = resolve_llm_provider()
    return {
        "ok": True,
        "mode": provider if llm_configured() else "offline-mock",
    }


_GLOSSARY_TEMPLATE = (
    "원어,번역어\r\n"
    "소방사다리차,Aerial Ladder Vehicle\r\n"
    "정격하중,Rated Load\r\n"
)


@app.get("/api/glossary/template")
def glossary_template():
    """A fill-in CSV template for user glossaries (utf-8-sig so Excel reads Korean)."""
    data = ("﻿" + _GLOSSARY_TEMPLATE).encode("utf-8")
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="glossary-template.csv"'},
    )


# Word is read natively (Path B); HWP is read natively too (Path C: pyhwp's hwp5html
# XHTML+CSS parsed directly — LibreOffice can't load .hwp). .hwpx is deferred until a
# converter is validated.
_OFFICE_EXTS = (".docx", ".doc", ".hwp")
_ALLOWED_EXTS = (".pdf",) + _OFFICE_EXTS


def _ext_ok(filename: str) -> bool:
    return os.path.splitext((filename or "").lower())[1] in _ALLOWED_EXTS


# Style references only need text (not layout), so plain-text formats are accepted too.
# (.hwpx excluded — hwp5html reads binary .hwp only; matches _ALLOWED_EXTS.)
_STYLE_EXTS = _ALLOWED_EXTS + (".txt", ".csv", ".md")


def _style_ext_ok(filename: str) -> bool:
    return os.path.splitext((filename or "").lower())[1] in _STYLE_EXTS


def _run_job(doc_id: str, src_path: str, source_lang: str, target_lang: str) -> None:
    """Analyze + translate on a background thread, reporting progress to `jobs`.

    Both the source model and the translation are persisted to disk, so the result is
    retrievable via /result even after this process forgets the in-memory job entry.
    Routing: PDF → vision pipeline; HWP → native (Path C); Word → native (Path B).
    """
    def report(stage, done, total):
        jobs.progress(doc_id, stage, done, total)

    try:
        ext = os.path.splitext(src_path)[1].lower()
        if ext == ".pdf":
            process.build_source_model(
                src_path, doc_id, STORAGE, source_lang=source_lang, on_progress=report
            )
        elif ext == ".hwp":
            process.build_source_model_from_hwp(
                src_path, doc_id, STORAGE, source_lang=source_lang, on_progress=report
            )
        else:
            process.build_source_model_from_office(
                src_path, doc_id, STORAGE, source_lang=source_lang, on_progress=report
            )
        if target_lang != source_lang:
            process.get_translated_model(
                STORAGE, doc_id, target_lang, on_progress=report
            )
        jobs.finish(doc_id)
        try:
            documents_store.set_status(doc_id, "done")
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        jobs.fail(doc_id, f"Processing failed: {e}")
        try:
            documents_store.set_status(doc_id, "error")
        except Exception:  # noqa: BLE001
            pass


@app.post("/api/documents")
async def upload(
    pdf: UploadFile = File(...),
    source_lang: str = Form("ko"),
    target_lang: str = Form("en"),
    glossary: UploadFile | None = File(None),
    style_domain: str = Form(""),
    uid: str = Depends(auth.current_user_bearer),
):
    filename = pdf.filename or ""
    if not _ext_ok(filename):
        raise HTTPException(
            400, "Supported formats: PDF, Word (.docx/.doc), HWP (.hwp/.hwpx)"
        )
    ext = os.path.splitext(filename.lower())[1]
    doc_id = secrets.token_urlsafe(18)  # ~144-bit capability (the /docx + DS-callback routes rely on its unguessability)
    base = process.doc_dir(STORAGE, doc_id)
    os.makedirs(base, exist_ok=True)
    # Keep the original extension; _run_job routes PDF vs office by it.
    src_path = os.path.join(base, f"source{ext}")
    with open(src_path, "wb") as fh:
        fh.write(await pdf.read())

    glossary_terms = 0
    if glossary is not None:
        raw = await glossary.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        mapping = parse_glossary_csv(text)
        if mapping:
            process.save_doc_glossary(STORAGE, doc_id, mapping)
        glossary_terms = len(mapping)

    # Kick the pipeline off in the background and return immediately; the client polls
    # /status for live progress and fetches /result when done.
    title = os.path.splitext(os.path.basename(filename))[0] or doc_id
    documents_store.create(doc_id, uid, title, source_lang, target_lang)
    # Persist the chosen style-reference domain (+ owner) so the translation step can retrieve
    # owner/domain-scoped style exemplars. No-op when no domain was selected.
    process.save_doc_style(STORAGE, doc_id, style_domain.strip(), uid)
    jobs.start(doc_id, source_lang, target_lang)
    threading.Thread(
        target=_run_job, args=(doc_id, src_path, source_lang, target_lang), daemon=True
    ).start()
    return {
        "doc_id": doc_id,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "glossary_terms": glossary_terms,
    }


@app.post("/api/detect-language")
async def detect_language(pdf: UploadFile = File(...), _: str = Depends(auth.current_user_bearer)):
    """Quick main-language detection from a text sample, used to default the upload
    direction (non-Korean → Korean, Korean → English). Best-effort: any failure returns
    the {en → ko} default rather than erroring, so it never blocks the upload UI."""
    import tempfile

    from pipeline import lang_detect

    filename = pdf.filename or ""
    ext = os.path.splitext(filename.lower())[1]
    if not _ext_ok(filename):
        return {"source_lang": "en", "target_lang": "ko"}
    try:
        text = ""
        if ext != ".hwp":  # HWP → ko shortcut, no extraction needed
            data = await pdf.read()
            fd, tmp = tempfile.mkstemp(suffix=ext)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                text = lang_detect.sample_text(tmp)
            finally:
                os.unlink(tmp)
        source = lang_detect.detect_lang(text, is_hwp=(ext == ".hwp"))
        s, t = lang_detect.default_direction(source)
        return {"source_lang": s, "target_lang": t}
    except Exception:  # noqa: BLE001 — never block the UI on detection
        return {"source_lang": "en", "target_lang": "ko"}


@app.get("/api/documents/{doc_id}/status")
def status(doc_id: str, lang: str | None = None, _: str = Depends(auth.owner_bearer)):
    job = jobs.get(doc_id)
    if job is not None:
        return job
    # In-memory entry gone (restart) but the work may already be on disk → treat as done.
    base = process.doc_dir(STORAGE, doc_id)
    if not os.path.exists(base):
        raise HTTPException(404, "Unknown document")
    translated = lang and os.path.exists(process.translated_path(STORAGE, doc_id, lang))
    source_done = os.path.exists(os.path.join(base, "source.json"))
    if translated or source_done:
        return {"status": "done", "stage": "done", "done": 1, "total": 1, "error": None}
    raise HTTPException(404, "No job state for document")


@app.get("/api/documents/{doc_id}/result")
def result(doc_id: str, lang: str | None = None, _: str = Depends(auth.owner_bearer)):
    base = process.doc_dir(STORAGE, doc_id)
    if not os.path.exists(base):
        raise HTTPException(404, "Unknown document")
    try:
        doc = process.get_export_model(STORAGE, doc_id, lang)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Result unavailable: {e}")
    # HWP ingest persists its hwp5html rendering; expose it as the left-pane original
    # (PDF/office uploads have page_images / nothing instead).
    has_src_html = os.path.exists(os.path.join(base, "hwphtml", "index.xhtml"))
    return {
        "doc_id": doc_id,
        "title": doc.title,
        "source_lang": doc.source_lang,
        "target_lang": lang,
        "report": _load_report(doc_id),
        "assets_prefix": _assets_prefix(doc_id),
        "page_images": process.list_page_images(STORAGE, doc_id),
        "page_size": process.page_size_mm(STORAGE, doc_id),
        "source_html": (
            f"/api/documents/{doc_id}/source-html/index.xhtml" if has_src_html else None
        ),
    }


@app.post("/api/documents/{doc_id}/translate")
def translate(doc_id: str, target_lang: str = Form(...), _: str = Depends(auth.owner_bearer)):
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    try:
        # Prefer a previously edited model so the user doesn't lose edits on revisit.
        doc = process.get_export_model(STORAGE, doc_id, target_lang)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Translation failed: {e}")
    return {"doc_id": doc_id, "target_lang": target_lang, "model": doc.model_dump()}


@app.get("/api/documents/{doc_id}/segments")
def segments(doc_id: str, lang: str, _: str = Depends(auth.owner_bearer)):
    """Sentence list for 재번역 모드: source↔translation pairs with original-page bbox."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    try:
        items = process.build_segments(STORAGE, doc_id, lang)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Segments unavailable: {e}")
    return {"lang": lang, "segments": items}


@app.post("/api/documents/{doc_id}/retranslate-segment")
def retranslate_segment(doc_id: str, body: RetranslateBody, _: str = Depends(auth.owner_bearer)):
    """Re-translate one segment with direction guidance; persist to the translation cache."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    guidance = build_guidance(body.preset, body.guidance)
    try:
        return process.retranslate_one_segment(
            STORAGE, doc_id, body.lang, body.id, guidance
        )
    except IndexError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Re-translation failed: {e}")


@app.post("/api/documents/{doc_id}/render")
def render(doc_id: str, lang: str, _: str = Depends(auth.owner_bearer)):
    """Force a re-render of output.{lang}.docx from the current model (which includes any
    re-translations written to translated.{lang}.json). Used so OnlyOffice Community — which
    has no connector for in-place edits — can reflect a re-translation by reloading the docx.
    Changes the file's mtime/size, so the editor's document key changes and the DS reloads."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    try:
        doc = process.get_export_model(STORAGE, doc_id, lang)
        out = process.output_docx_path(STORAGE, doc_id, lang)
        render_docx(doc, out, process.asset_dir(STORAGE, doc_id))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Render failed: {e}")
    return {"ok": True}


@app.post("/api/documents/{doc_id}/forcesave")
def forcesave(doc_id: str, lang: str | None = None, _: str = Depends(auth.owner_bearer)):
    """Tell the DS to save the live editing session NOW (→ status-6 callback → model sync),
    so leaving 문서 편집 for a tool view reflects the edit immediately instead of after the
    multi-second disconnect-save. Best-effort: returns {ok,error}; ok = DS accepted (0) or had
    no unsaved changes (4). The client falls back to a plain switch + poll on any other result.
    Must NOT re-render (that would change the file key and miss the live session)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    doc = process.get_export_model(STORAGE, doc_id, lang)
    eff_lang = lang or doc.source_lang
    out = process.output_docx_path(STORAGE, doc_id, eff_lang)
    key = _read_issued_key(doc_id, eff_lang)  # the key the editor opened with; None → recompute
    try:
        res = onlyoffice.forcesave(out, key)
    except Exception:  # noqa: BLE001 — DS unreachable/timeout → let the client fall back
        return {"ok": False, "error": -1}
    err = res.get("error", -1)
    return {"ok": err in (0, 4), "error": err}


@app.post("/api/documents/{doc_id}/replace/preview")
def replace_preview(doc_id: str, body: ReplaceRequest, lang: str, _: str = Depends(auth.owner_bearer)):
    """Per-rule occurrence counts for a set of term replacements (no writes)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    _ensure_docx(doc_id, lang)
    try:
        return process.preview_replace(STORAGE, doc_id, lang, body.rules)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Preview failed: {e}")


@app.post("/api/documents/{doc_id}/replace")
def replace_apply(doc_id: str, body: ReplaceRequest, lang: str, _: str = Depends(auth.owner_bearer)):
    """Apply bulk term replacements across the document's .docx (preserving manual edits),
    mirror into the model, and persist rules marked save. Returns per-rule replaced counts.
    The caller reloads the editor (Community Edition has no in-place connector)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    _ensure_docx(doc_id, lang)
    try:
        return process.bulk_replace(STORAGE, doc_id, lang, body.rules)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Replace failed: {e}")


@app.get("/api/documents/{doc_id}/glossary-report")
def glossary_report(doc_id: str, lang: str, _: str = Depends(auth.owner_bearer)):
    """Per-term applied/missed audit of the uploaded glossary vs the current translation."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    try:
        return {"lang": lang, "terms": process.glossary_report(STORAGE, doc_id, lang)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Glossary report failed: {e}")


@app.get("/api/documents/{doc_id}/proper-nouns")
def proper_nouns(doc_id: str, lang: str, _: str = Depends(auth.owner_bearer)):
    """Proper nouns translated but not in the glossary (고유명사 검수) — review pass."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    try:
        return {"lang": lang, "terms": process.proper_noun_report(STORAGE, doc_id, lang)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Proper-noun report failed: {e}")


@app.get("/api/documents/{doc_id}/preview", response_class=HTMLResponse)
def preview(doc_id: str, lang: str | None = None, segments: bool = False,
            _: str = Depends(auth.owner_any)):
    """Read-only translated HTML with uploaded-glossary 번역어 highlighted + block anchors.
    segments=1 also wraps each translatable field in a data-seg span (재번역 click-select)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    from pipeline.text_replace import compile_terms

    doc = process.get_export_model(STORAGE, doc_id, lang)
    eff_lang = lang or doc.source_lang
    glossary = process.load_doc_glossary(STORAGE, doc_id)
    pattern = compile_terms(list(glossary.values()), eff_lang) if glossary else None
    return render_html(doc, asset_url_prefix=_assets_prefix(doc_id),
                       highlight_pattern=pattern, anchor_blocks=True, wrap_segments=segments)


@app.get("/api/documents/{doc_id}/source-preview", response_class=HTMLResponse)
def source_preview(doc_id: str, _: str = Depends(auth.owner_any)):
    """Read-only SOURCE-language HTML with data-seg spans + block anchors, for the
    재번역 left pane on HWP/office uploads (which have no page-image bboxes to click)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    doc = process.load_source_model(STORAGE, doc_id)
    return render_html(doc, asset_url_prefix=_assets_prefix(doc_id),
                       anchor_blocks=True, wrap_segments=True)


@app.post("/api/documents/{doc_id}/glossary/term")
def glossary_term(doc_id: str, body: GlossaryTermBody, lang: str, _: str = Depends(auth.owner_bearer)):
    """Edit an existing glossary term (unifies the current translation) or add a new one
    (optionally re-translating the affected segments to apply it now)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    source, target = body.source.strip(), body.target.strip()
    if not source or not target:
        raise HTTPException(422, "source and target are required")
    _ensure_docx(doc_id, lang)  # update path edits the .docx via bulk_replace — ensure it exists
    try:
        if source in process.load_doc_glossary(STORAGE, doc_id):
            res = process.update_glossary_term(STORAGE, doc_id, source, target, lang)
            return {"status": "updated", **res}  # bulk_replace already edited the .docx
        process.add_glossary_term(STORAGE, doc_id, source, target)
        if body.apply:
            res = process.apply_glossary_term(STORAGE, doc_id, source, lang)
            # apply re-translates into translated.{lang}.json ONLY → re-render the docx so the
            # OnlyOffice editor reflects it (the 용어 관리 preview is model-based, already fresh).
            out = process.output_docx_path(STORAGE, doc_id, lang)
            render_docx(process.get_export_model(STORAGE, doc_id, lang), out,
                        process.asset_dir(STORAGE, doc_id))
            return {"status": "added", **res}
        return {"status": "added"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Glossary term update failed: {e}")


@app.get("/api/style/domains")
def style_domains(uid: str = Depends(auth.current_user_bearer)):
    """The user's style-reference domain folders with reference/segment counts."""
    from pipeline import style_memory
    return {"domains": style_memory.list_domains(uid)}


@app.get("/api/style/references")
def style_references(domain: str, uid: str = Depends(auth.current_user_bearer)):
    """References within one of the user's domains."""
    from pipeline import style_memory
    return {"domain": domain, "references": style_memory.list_references(uid, domain)}


async def _stage_style_files(tmp, target, source):
    """Write the uploaded reference file(s) into `tmp`; returns (target_path, source_path|None)."""
    tpath = os.path.join(tmp, f"target{os.path.splitext((target.filename or '').lower())[1]}")
    with open(tpath, "wb") as fh:
        fh.write(await target.read())
    spath = None
    if source is not None and source.filename:
        spath = os.path.join(tmp, f"source{os.path.splitext(source.filename.lower())[1]}")
        with open(spath, "wb") as fh:
            fh.write(await source.read())
    return tpath, spath


@app.post("/api/style/references/analyze")
async def style_reference_analyze(
    target: UploadFile = File(...),
    source: UploadFile | None = File(None),
    _: str = Depends(auth.current_user_bearer),
):
    """Detect a reference's bilingual format + preview extracted style/TM counts and samples,
    WITHOUT storing or embedding. Drives the upload UI's auto-detect + override."""
    if not _style_ext_ok(target.filename) or (source and not _style_ext_ok(source.filename)):
        raise HTTPException(400, "Supported reference formats: PDF, Word, HWP, TXT/CSV")
    from pipeline import reference_intake
    tmp = tempfile.mkdtemp(prefix="styleanalyze_")
    try:
        tpath, spath = await _stage_style_files(tmp, target, source)
        try:
            return reference_intake.analyze(tpath, spath)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Analyze failed: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/api/style/references/pairs")
async def style_reference_pairs(
    target: UploadFile = File(...),
    source: UploadFile | None = File(None),
    mode: str = Form(""),
    _: str = Depends(auth.current_user_bearer),
):
    """Full list of every 원문↔번역 pair for one reference file (no storage, no embedding).
    sequential/paired are positional estimates (estimated=true)."""
    if not _style_ext_ok(target.filename) or (source and not _style_ext_ok(source.filename)):
        raise HTTPException(400, "Supported reference formats: PDF, Word, HWP, TXT/CSV")
    from pipeline import reference_intake
    tmp = tempfile.mkdtemp(prefix="stylepairs_")
    try:
        tpath, spath = await _stage_style_files(tmp, target, source)
        try:
            res = reference_intake.pairs_preview(tpath, spath, mode.strip() or None)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Pairs preview failed: {e}")
        res["count"] = len(res["pairs"])
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/api/style/references")
async def style_reference_upload(
    domain: str = Form(...),
    target: UploadFile = File(...),
    source: UploadFile | None = File(None),
    target_lang: str = Form("en"),
    source_lang: str = Form("ko"),
    name: str = Form(""),
    mode: str = Form(""),
    uid: str = Depends(auth.current_user_bearer),
):
    """Ingest a past translation into a domain folder. `target` (target-language) is required
    and indexed for style; an optional `source` original is aligned for Translation Memory."""
    domain = domain.strip()
    if not domain:
        raise HTTPException(422, "domain is required")
    if not _style_ext_ok(target.filename) or (source and not _style_ext_ok(source.filename)):
        raise HTTPException(400, "Supported reference formats: PDF, Word, HWP, TXT/CSV")
    tmp = tempfile.mkdtemp(prefix="styleref_")
    try:
        tpath, spath = await _stage_style_files(tmp, target, source)
        from pipeline import style_memory
        try:
            result = style_memory.ingest_reference(
                uid, domain, name.strip() or (target.filename or "reference"),
                tpath, target_lang, source_path=spath, lang_src=source_lang,
                mode=mode.strip() or None,
            )
        except ValueError as e:           # source-only / nothing to learn
            raise HTTPException(422, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Reference ingest failed: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ref_id = result["id"]
    cur = next((r for r in style_memory.list_references(uid, domain) if r["id"] == ref_id), {})
    # `tm` reports best-effort Translation-Memory indexing: style is always saved, but if pair
    # embedding failed (e.g. provider rate limit) tm.indexed is false with the captured error.
    return {"id": ref_id, "domain": domain, "segment_count": cur.get("segment_count", 0),
            "tm": result["tm"]}


@app.delete("/api/style/references/{ref_id}")
def style_reference_delete(ref_id: str, uid: str = Depends(auth.current_user_bearer)):
    from pipeline import style_memory
    if not style_memory.delete_reference(uid, ref_id):
        raise HTTPException(404, "Unknown reference")
    return {"ok": True}


@app.get("/api/documents/{doc_id}/editor-config")
def editor_config(doc_id: str, lang: str | None = None, bookmark: str | None = None, _: str = Depends(auth.owner_bearer)):
    """Signed config for `new DocsAPI.DocEditor(...)` plus the browser-facing DS URL.
    Renders the docx lazily on first open so the DS has something to fetch. `bookmark`
    (e.g. 'blk12') opens the editor scrolled to that block."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    doc = process.get_export_model(STORAGE, doc_id, lang)
    eff_lang = lang or doc.source_lang
    docx_path = _ensure_docx(doc_id, eff_lang)
    config = onlyoffice.build_editor_config(
        doc_id, eff_lang, doc.title, docx_path, bookmark=bookmark
    )
    _write_issued_key(doc_id, eff_lang, config["document"]["key"])
    return {"document_server_url": onlyoffice.PUBLIC_URL, "config": config}


@app.get("/api/documents/{doc_id}/docx")
def serve_docx(doc_id: str, lang: str | None = None):
    """The .docx the Document Server fetches to open. Served inline (no attachment)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    doc = process.get_export_model(STORAGE, doc_id, lang)
    eff_lang = lang or doc.source_lang
    return FileResponse(_ensure_docx(doc_id, eff_lang), media_type=DOCX_MIME)


# --- translated-page (editor-parity) views ---------------------------------
# The DS ConvertService renders output.{lang}.docx with the SAME engine as the
# embedded editor, so these page images match the editor pixel-for-pixel; the
# interaction layer (segment hotspots / term highlights) mirrors the source
# pane's fraction-bbox overlays. Capability-authed like /docx (no bearer dep) —
# the unguessable doc_id in the URL is the security boundary, since these are
# fetched as plain <img>/fetch requests without an Authorization header.

def _tpage_segments(doc_id: str, lang: str) -> list[dict]:
    """[{id, text}] in document order, using the SAME ids as GET /segments — reuses
    process.build_segments (the exact function /segments calls) so the frontend can
    join tsegments bboxes onto the /segments list by id."""
    items = process.build_segments(STORAGE, doc_id, lang)
    return [{"id": it["id"], "text": it["translated"]} for it in items]


@app.get("/api/documents/{doc_id}/tpages")
def tpages(doc_id: str, lang: str = "en"):
    try:
        lang = process._safe_lang(lang)
    except ValueError:
        raise HTTPException(400, "invalid lang")
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    _ensure_docx(doc_id, lang)
    try:
        pdf = ds_convert.ensure_pdf(STORAGE, doc_id, lang)
    except FileNotFoundError:
        raise HTTPException(404, "document not found")
    except (ds_convert.DsConvertError, httpx.HTTPError) as e:
        raise HTTPException(503, f"문서 변환 실패: {e}")
    out_dir = os.path.join(STORAGE, doc_id, f"tpages.{lang}")
    m = pdf_map.render_pages(pdf, out_dir)
    return {
        "version": m["version"],
        "page_w": m["page_w"],
        "page_h": m["page_h"],
        "pages": [
            {"n": p["n"],
             "url": f"/api/documents/{doc_id}/tpages/{p['n']}.png?lang={lang}&v={m['version']}"}
            for p in m["pages"]
        ],
    }


@app.get("/api/documents/{doc_id}/tpages/{n}.png")
def tpage_png(doc_id: str, n: int, lang: str = "en", v: str = ""):
    try:
        lang = process._safe_lang(lang)
    except ValueError:
        raise HTTPException(400, "invalid lang")
    path = os.path.join(STORAGE, doc_id, f"tpages.{lang}", f"tpage{n}.png")
    if not os.path.exists(path):
        raise HTTPException(404, "page not rendered")
    return FileResponse(path, media_type="image/png")


@app.get("/api/documents/{doc_id}/tversion")
def tversion(doc_id: str, lang: str = "en"):
    """Cheap freshness signal for the translated pane: the SAME document_key() that gates
    tpages/tsegments' cache, without ever converting anything. output.{lang}.docx changes
    exactly when a DOCX edit is saved (forcesave/callback/render) — never when the PDF pages
    are (re)rendered from it — so polling this after a forcesave is a correct, cheap way to
    detect "the edit landed" without waiting on segment TEXT (which never changes for a
    formatting-only edit, and which the DS's disconnect-save can land after a short poll's
    timeout). Pure os.stat: no _ensure_docx, no ds_convert."""
    try:
        lang = process._safe_lang(lang)
    except ValueError:
        raise HTTPException(400, "invalid lang")
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    docx_path = process.output_docx_path(STORAGE, doc_id, lang)
    if not os.path.exists(docx_path):
        return {"version": None}
    return {"version": onlyoffice.document_key(docx_path)}


@app.get("/api/documents/{doc_id}/tsegments")
def tsegments(doc_id: str, lang: str = "en"):
    try:
        lang = process._safe_lang(lang)
    except ValueError:
        raise HTTPException(400, "invalid lang")
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    _ensure_docx(doc_id, lang)
    try:
        pdf = ds_convert.ensure_pdf(STORAGE, doc_id, lang)
    except FileNotFoundError:
        raise HTTPException(404, "document not found")
    except (ds_convert.DsConvertError, httpx.HTTPError) as e:
        raise HTTPException(503, f"문서 변환 실패: {e}")
    version = onlyoffice.document_key(pdf)
    cache = os.path.join(STORAGE, doc_id, f"tpages.{lang}", "tsegments.json")
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("version") == version:
                return data
        except (OSError, ValueError):
            pass
    segs = _tpage_segments(doc_id, lang)
    # Running-header/footer band segments (id "band:{which}:{attr}") repeat verbatim
    # on EVERY page of the converted PDF. align_words is a single forward-cursor,
    # order-based matcher, so feeding a band segment into it anchors to some
    # coincidental occurrence and strands the cursor there — real-document body
    # match rate collapsed to 13/120 with bands included vs. 120/120 excluded (see
    # tests/test_tpages_integration.py). Bands aren't click-to-highlight targets
    # anyway (a single hotspot on a string that repeats every page would be
    # meaningless), so they're excluded from alignment entirely; each still gets
    # exactly one response entry, with bbox always null.
    body_segs = [s for s in segs if not s["id"].startswith("band:")]
    boxes = pdf_map.map_segments(pdf, body_segs)
    data = {"version": version,
            "segments": [{"id": s["id"], "bbox": boxes.get(s["id"])} for s in segs]}
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    # Unique-per-attempt tmp name (pid + random hex), not a fixed path: two concurrent
    # requests racing a stale cache would otherwise race os.replace on the same tmp file
    # (PermissionError on Windows) — same hardening as ds_convert.ensure_pdf.
    tmp_cache = f"{cache}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp_cache, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp_cache, cache)
    return data


class TfindBody(BaseModel):
    terms: list[str]


@app.post("/api/documents/{doc_id}/tfind")
def tfind(doc_id: str, body: TfindBody, lang: str = "en"):
    try:
        lang = process._safe_lang(lang)
    except ValueError:
        raise HTTPException(400, "invalid lang")
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    _ensure_docx(doc_id, lang)
    try:
        pdf = ds_convert.ensure_pdf(STORAGE, doc_id, lang)
    except FileNotFoundError:
        raise HTTPException(404, "document not found")
    except (ds_convert.DsConvertError, httpx.HTTPError) as e:
        raise HTTPException(503, f"문서 변환 실패: {e}")
    terms = [t for t in body.terms if t.strip()][:50]
    return {"hits": pdf_map.find_terms(pdf, terms)}


@app.post("/api/documents/{doc_id}/onlyoffice-callback")
async def onlyoffice_callback(doc_id: str, request: Request, lang: str | None = None):
    """Save hook. The DS POSTs here; status 2/6 means an edited document is ready to
    download from the `url` it provides. We verify the JWT, fetch it, and overwrite the
    stored docx (the post-edit source of truth)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    body = await request.json()
    try:
        data = onlyoffice.verify_callback(request.headers, body)
    except Exception:  # noqa: BLE001 — bad/missing token
        raise HTTPException(403, "Invalid OnlyOffice callback token")

    # 2 = MustSave (all editors closed), 6 = ForceSave (explicit save while editing).
    if data.get("status") in (2, 6) and data.get("url"):
        doc = process.get_export_model(STORAGE, doc_id, lang)
        eff_lang = lang or doc.source_lang
        src = onlyoffice.ds_fetch_url(data["url"])
        out = process.output_docx_path(STORAGE, doc_id, eff_lang)
        with httpx.stream("GET", src, timeout=60) as r:
            r.raise_for_status()
            with open(out, "wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        _sync_model_from_saved_docx(doc_id, eff_lang)   # ← reflect edits into the model
    return {"error": 0}


@app.get("/api/documents/{doc_id}/assets/{name}")
def asset(doc_id: str, name: str, _: str = Depends(auth.owner_any)):
    path = os.path.join(process.asset_dir(STORAGE, doc_id), os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(404, "Asset not found")
    return FileResponse(path)


@app.get("/api/documents/{doc_id}/source-html/{path:path}")
def source_html(doc_id: str, path: str, _: str = Depends(auth.owner_any)):
    """Serve the persisted hwp5html rendering (index.xhtml + styles.css + bindata/) for an
    HWP upload, so the viewer can show the real original as the left pane. Path-confined to
    the doc's hwphtml dir."""
    import mimetypes

    root = os.path.realpath(os.path.join(process.doc_dir(STORAGE, doc_id), "hwphtml"))
    target = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath([root, target]) != root or not os.path.isfile(target):
        raise HTTPException(404, "Not found")
    media = "text/html; charset=utf-8" if target.endswith((".xhtml", ".html")) else (
        mimetypes.guess_type(target)[0] or "application/octet-stream")
    return FileResponse(target, media_type=media)


@app.get("/api/documents/{doc_id}/view", response_class=HTMLResponse)
def view(doc_id: str, lang: str | None = None, _: str = Depends(auth.owner_any)):
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    doc = process.get_export_model(STORAGE, doc_id, lang)
    return render_html(doc, asset_url_prefix=_assets_prefix(doc_id))


@app.get("/api/documents/{doc_id}/download")
def download(doc_id: str, lang: str | None = None, _: str = Depends(auth.owner_any)):
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    doc = process.get_export_model(STORAGE, doc_id, lang)
    eff_lang = lang or doc.source_lang
    # The stored docx is the edited version once OnlyOffice has saved; otherwise
    # _ensure_docx renders it from the model. Either way it's the source of truth.
    out_path = _ensure_docx(doc_id, eff_lang)
    fname = f"{(doc.title or 'document')[:40]}.{eff_lang}.docx"
    return FileResponse(out_path, media_type=DOCX_MIME, filename=fname)
