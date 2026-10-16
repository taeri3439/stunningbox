"""Orchestrator: extract -> analyze -> reconcile, with on-disk caching.

Extraction + analysis run once per document (the expensive part). Translating to a new
target language reuses the cached semantic model, so only Stage 3 re-runs.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from typing import Callable, Optional

from models import SemanticDocument, iter_segments, iter_text_fields
from pipeline import convert, docx_extractor, extractor, figure_overlay, hwp_extractor
from pipeline._parallel import parallel_map
from pipeline.analyzer import get_analyzer
from pipeline.reconciler import reconcile
from pipeline.translator import translate_document, translate_segment


# A progress sink: (stage, done, total). Stages: "convert" | "extract" | "analyze" | "translate".
ProgressFn = Callable[[str, int, int], None]

_PAGE_RE = re.compile(r"^page(\d+)\.png$")

_LANG_RE = re.compile(r"^[A-Za-z]{2,8}$")


def _safe_lang(lang: str) -> str:
    """Guard against path traversal: `lang` flows into on-disk filenames
    (output.{lang}.docx, translated.{lang}.json, …). Reject anything that isn't a short
    alpha code so e.g. lang='../other_doc/output' can't escape the document's directory."""
    if not isinstance(lang, str) or not _LANG_RE.match(lang):
        raise ValueError(f"Invalid language code: {lang!r}")
    return lang


def ensure_block_ids(*docs) -> bool:
    """Assign a stable block_id to every block lacking one. The FIRST doc is the id source;
    later docs (translated/edited) are mirrored BY POSITION (valid pre-reorder). Idempotent;
    returns True if anything changed."""
    if not docs:
        return False
    primary = docs[0]
    changed = False
    for i, blk in enumerate(primary.blocks):
        if not getattr(blk, "block_id", None):
            blk.block_id = uuid.uuid4().hex
            changed = True
        for other in docs[1:]:
            if i < len(other.blocks) and not getattr(other.blocks[i], "block_id", None):
                other.blocks[i].block_id = blk.block_id
                changed = True
    return changed


def doc_dir(storage_dir: str, doc_id: str) -> str:
    return os.path.join(storage_dir, doc_id)


def asset_dir(storage_dir: str, doc_id: str) -> str:
    return os.path.join(doc_dir(storage_dir, doc_id), "assets")


def list_page_images(storage_dir: str, doc_id: str) -> list[str]:
    """Full-page renders (page0.png, page1.png, ...) in reading order."""
    ad = asset_dir(storage_dir, doc_id)
    if not os.path.isdir(ad):
        return []
    pages = [(int(m.group(1)), n) for n in os.listdir(ad) if (m := _PAGE_RE.match(n))]
    return [n for _, n in sorted(pages)]


def page_size_mm(storage_dir: str, doc_id: str) -> dict | None:
    """Physical size of the source page (from the first render, at RENDER_DPI), in mm.

    Lets the UI render the translation sheets at the *same* size as the original PDF
    page. Assumes a uniform page size (first page); returns None if no render exists.
    """
    pages = list_page_images(storage_dir, doc_id)
    if not pages:
        return None
    from PIL import Image

    with Image.open(os.path.join(asset_dir(storage_dir, doc_id), pages[0])) as im:
        w_px, h_px = im.size
    mm_per_px = 25.4 / extractor.RENDER_DPI
    return {"width_mm": round(w_px * mm_per_px, 1), "height_mm": round(h_px * mm_per_px, 1)}


def build_source_model(
    pdf_path: str,
    doc_id: str,
    storage_dir: str,
    analyzer_mode: str | None = None,
    source_lang: str = "ko",
    on_progress: Optional[ProgressFn] = None,
):
    """Stages 0-2. Returns (SemanticDocument, report dict, page_image names). Caches to disk."""
    ad = asset_dir(storage_dir, doc_id)
    raw = extractor.extract_document(pdf_path, ad)

    analyzer = get_analyzer(analyzer_mode)
    # Pages are independent — analyze them concurrently so a long document's latency is
    # the slowest page, not the sum of all pages (which would time out the request).
    total = len(raw.pages)
    if on_progress:
        on_progress("analyze", 0, total)
    counter = _Counter(on_progress, "analyze", total)
    page_blocks = parallel_map(
        lambda page: analyzer.analyze_page(page, ad), raw.pages, on_done=counter.bump
    )

    doc, report = reconcile(raw, page_blocks, pdf_path, ad, source_lang=source_lang)

    page_images = [p.render_path for p in raw.pages]

    base = doc_dir(storage_dir, doc_id)
    os.makedirs(base, exist_ok=True)
    # Cache the analyzer output (the only expensive, API-billed stage). Re-running
    # reconciliation after a pipeline fix can then reuse this instead of re-analyzing.
    ensure_block_ids(doc)
    _write_json(os.path.join(base, "page_blocks.json"), page_blocks)
    _write_json(os.path.join(base, "source.json"), doc.model_dump())
    _write_json(os.path.join(base, "report.json"), report.as_dict())
    return doc, report.as_dict(), page_images


def build_source_model_from_office(
    src_path: str,
    doc_id: str,
    storage_dir: str,
    source_lang: str = "ko",
    on_progress: Optional[ProgressFn] = None,
):
    """Office-ingest path (Path B): normalize to .docx, read its native structure into a
    SemanticDocument (no vision API). Mirrors build_source_model's on-disk outputs."""
    base = doc_dir(storage_dir, doc_id)
    ad = asset_dir(storage_dir, doc_id)
    os.makedirs(base, exist_ok=True)

    if on_progress:
        on_progress("convert", 0, 1)
    docx_path = convert.to_docx(src_path, base)
    if on_progress:
        on_progress("convert", 1, 1)
        on_progress("extract", 0, 1)
    doc = docx_extractor.extract_docx(docx_path, ad, source_lang=source_lang)
    if on_progress:
        on_progress("extract", 1, 1)

    report = {"ingest": "office", "blocks": len(doc.blocks)}
    ensure_block_ids(doc)
    _write_json(os.path.join(base, "source.json"), doc.model_dump())
    _write_json(os.path.join(base, "report.json"), report)
    return doc, report


def build_source_model_from_hwp(
    src_path: str,
    doc_id: str,
    storage_dir: str,
    source_lang: str = "ko",
    on_progress: Optional[ProgressFn] = None,
):
    """HWP-ingest path (Path C): parse hwp5html's XHTML+CSS natively into a
    SemanticDocument (no vision API, no LibreOffice). Mirrors the office path's outputs."""
    base = doc_dir(storage_dir, doc_id)
    ad = asset_dir(storage_dir, doc_id)
    os.makedirs(base, exist_ok=True)

    if on_progress:
        on_progress("extract", 0, 1)
    # work_dir=base persists hwp5html's XHTML under storage/<id>/hwphtml so the viewer can
    # render it as the left-pane "original" (served by /source-html).
    doc = hwp_extractor.extract_hwp(src_path, ad, source_lang=source_lang, work_dir=base)
    if on_progress:
        on_progress("extract", 1, 1)

    report = {"ingest": "hwp", "blocks": len(doc.blocks)}
    ensure_block_ids(doc)
    _write_json(os.path.join(base, "source.json"), doc.model_dump())
    _write_json(os.path.join(base, "report.json"), report)
    return doc, report


def rebuild_source_model(
    pdf_path: str, doc_id: str, storage_dir: str, source_lang: str = "ko"
):
    """Re-run extraction + reconciliation from CACHED analyzer output (no API calls).
    Used to apply reconciler/extractor fixes to an already-analyzed document. Returns
    (SemanticDocument, report dict). Raises FileNotFoundError if no cache exists."""
    base = doc_dir(storage_dir, doc_id)
    with open(os.path.join(base, "page_blocks.json"), encoding="utf-8") as fh:
        page_blocks = json.load(fh)
    ad = asset_dir(storage_dir, doc_id)
    raw = extractor.extract_document(pdf_path, ad)
    doc, report = reconcile(raw, page_blocks, pdf_path, ad, source_lang=source_lang)
    _write_json(os.path.join(base, "source.json"), doc.model_dump())
    _write_json(os.path.join(base, "report.json"), report.as_dict())
    return doc, report.as_dict()


class _Counter:
    """Thread-safe completion counter that forwards (stage, k, total) to a progress sink."""

    def __init__(self, on_progress: Optional[ProgressFn], stage: str, total: int):
        self._on_progress = on_progress
        self._stage = stage
        self._total = total
        self._n = 0
        self._lock = threading.Lock()

    def bump(self) -> None:
        if not self._on_progress:
            return
        with self._lock:
            self._n += 1
            n = self._n
        self._on_progress(self._stage, n, self._total)


def load_source_model(storage_dir: str, doc_id: str) -> SemanticDocument:
    source_path = os.path.join(doc_dir(storage_dir, doc_id), "source.json")
    with open(source_path, encoding="utf-8") as fh:
        model = SemanticDocument.model_validate(json.load(fh))
    if ensure_block_ids(model):
        _write_json(source_path, model.model_dump())
    return model


def _sync_from_docx_if_newer(
    storage_dir: str, doc_id: str, target_lang: str, model: SemanticDocument
) -> SemanticDocument:
    """If the OnlyOffice editor saved output.{lang}.docx AFTER the cached translation, reflect
    its bookmark-keyed, structure-preserving text edits into the model + persist — so the HTML
    views (재번역/용어/고유명사/미리보기) show the latest editor edit even when the save-callback
    sync raced the read (or didn't fire). Best-effort: any failure returns the unsynced model so
    a read never breaks. Idempotent: re-rendered/unedited docx round-trips to the same model
    (see test_docx_sync_apply identity), and the save bumps the cache's mtime so it won't re-run."""
    try:
        docx = output_docx_path(storage_dir, doc_id, target_lang)
        cache = translated_path(storage_dir, doc_id, target_lang)
        if os.path.exists(docx) and os.path.getmtime(docx) > os.path.getmtime(cache):
            from pipeline.docx_sync import apply_docx_edits_to_model
            synced = apply_docx_edits_to_model(model, docx)
            save_translated_model(storage_dir, doc_id, target_lang, synced)
            return synced
    except Exception:  # noqa: BLE001 — never break a read on a sync hiccup
        pass
    return model


def get_translated_model(
    storage_dir: str,
    doc_id: str,
    target_lang: str,
    on_progress: Optional[ProgressFn] = None,
) -> SemanticDocument:
    """Return the document in `target_lang`, translating + caching on first request."""
    cache = translated_path(storage_dir, doc_id, target_lang)
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            model = SemanticDocument.model_validate(json.load(fh))
        source_path = os.path.join(doc_dir(storage_dir, doc_id), "source.json")
        try:
            with open(source_path, encoding="utf-8") as fh:
                source_model = SemanticDocument.model_validate(json.load(fh))
            if ensure_block_ids(source_model, model):
                _write_json(source_path, source_model.model_dump())
                save_translated_model(storage_dir, doc_id, target_lang, model)
        except FileNotFoundError:
            pass
        return _sync_from_docx_if_newer(storage_dir, doc_id, target_lang, model)

    source = load_source_model(storage_dir, doc_id)
    if target_lang == source.source_lang:
        return source
    style_context = _build_style_context(storage_dir, doc_id, source, target_lang)
    tm_map = _build_tm_map(storage_dir, doc_id, source, target_lang)
    translated = translate_document(
        source, target_lang, on_progress=on_progress,
        extra_glossary=load_doc_glossary(storage_dir, doc_id),
        style_context=style_context, tm_map=tm_map,
    )
    figure_overlay.composite_document(
        translated, asset_dir(storage_dir, doc_id), target_lang)
    _write_json(cache, translated.model_dump())
    return translated


def edited_path(storage_dir: str, doc_id: str, target_lang: str) -> str:
    return os.path.join(doc_dir(storage_dir, doc_id), f"edited.{_safe_lang(target_lang)}.json")


def output_docx_path(storage_dir: str, doc_id: str, target_lang: str | None) -> str:
    """The exported/edited .docx on disk. This is the post-edit source of truth once
    the OnlyOffice editor saves back to it; before any edit it's (re)rendered from the
    model. `output.{lang}.docx` (or `output.{source_lang}.docx` when no target)."""
    suffix = _safe_lang(target_lang) if target_lang else "src"
    return os.path.join(doc_dir(storage_dir, doc_id), f"output.{suffix}.docx")


def save_edited_model(
    storage_dir: str, doc_id: str, target_lang: str, doc: SemanticDocument
) -> None:
    """Persist a user-edited model. Export prefers this over the auto-translation."""
    _write_json(edited_path(storage_dir, doc_id, target_lang), doc.model_dump())


def save_translated_model(
    storage_dir: str, doc_id: str, target_lang: str, doc: SemanticDocument
) -> None:
    """Overwrite the per-language translation cache (translated.{lang}.json)."""
    cache = translated_path(storage_dir, doc_id, target_lang)
    _write_json(cache, doc.model_dump())


def glossary_path(storage_dir: str, doc_id: str) -> str:
    return os.path.join(doc_dir(storage_dir, doc_id), "glossary.json")


def save_doc_glossary(storage_dir: str, doc_id: str, mapping: dict) -> None:
    """Persist the per-document user glossary ({source: target})."""
    _write_json(glossary_path(storage_dir, doc_id), mapping)


def load_doc_glossary(storage_dir: str, doc_id: str) -> dict:
    """The per-document user glossary, or {} if none/unreadable. A corrupt glossary
    must not break translation — degrade to the built-in glossary instead of erroring."""
    path = glossary_path(storage_dir, doc_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def style_path(storage_dir: str, doc_id: str) -> str:
    return os.path.join(doc_dir(storage_dir, doc_id), "style.json")


def save_doc_style(storage_dir: str, doc_id: str, domain: str, owner: str) -> None:
    """Persist the style-reference domain + owner chosen for this doc at upload (so the
    translation step can retrieve owner/domain-scoped style exemplars). No-op for no domain."""
    if domain:
        _write_json(style_path(storage_dir, doc_id), {"domain": domain, "owner": owner})


def load_doc_style(storage_dir: str, doc_id: str) -> tuple[str, str]:
    """(domain, owner) for this doc's style reference, or ('', '') if none/unreadable."""
    path = style_path(storage_dir, doc_id)
    if not os.path.exists(path):
        return "", ""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d.get("domain", ""), d.get("owner", "")
    except (json.JSONDecodeError, OSError):
        return "", ""


def _build_style_context(storage_dir, doc_id, source, target_lang, query_segments=None) -> str:
    """The style-reference prompt block for this doc's domain, or '' (no domain configured).
    Fails safe: RAG retrieval (external embeddings/DB in prod) must NEVER break translation."""
    domain, owner = load_doc_style(storage_dir, doc_id)
    if not domain:
        return ""
    try:
        from pipeline import style_memory
        segs = query_segments if query_segments is not None else [
            t for get, _ in iter_text_fields(source) if (t := get().strip())
        ]
        return style_memory.retrieve_style_context(
            owner, domain, segs, source.source_lang, target_lang
        )
    except Exception:  # noqa: BLE001 — style transfer is best-effort, never fatal
        return ""


def _build_tm_map(storage_dir, doc_id, source, target_lang) -> dict | None:
    """{source_segment: approved_translation} for high-confidence Translation-Memory matches
    in this doc's domain (bilingual references only). None/empty disables TM reuse. Fails
    safe — never breaks translation."""
    domain, owner = load_doc_style(storage_dir, doc_id)
    if not domain or target_lang == source.source_lang:
        return None
    try:
        from pipeline import style_memory
        segs = [get() for get, _ in iter_text_fields(source)]
        return style_memory.tm_prefill(owner, domain, segs, source.source_lang)
    except Exception:  # noqa: BLE001
        return None


def translated_path(storage_dir: str, doc_id: str, target_lang: str) -> str:
    return os.path.join(doc_dir(storage_dir, doc_id), f"translated.{_safe_lang(target_lang)}.json")


def replacements_path(storage_dir: str, doc_id: str, target_lang: str) -> str:
    return os.path.join(doc_dir(storage_dir, doc_id), f"replacements.{_safe_lang(target_lang)}.json")


def load_replacements(storage_dir: str, doc_id: str, target_lang: str) -> list[dict]:
    """Saved post-translation term replacements for this doc/lang, or [] if none/unreadable.
    A corrupt file must not block re-translation — degrade to []."""
    path = replacements_path(storage_dir, doc_id, target_lang)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return [r for r in data
                if isinstance(r, dict) and "find" in r and "replace" in r] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_replacements(storage_dir: str, doc_id: str, target_lang: str, rules) -> None:
    """Merge `rules` (ReplaceRule list) into the saved set, deduped by `find` (later wins)."""
    by_find = {r["find"]: r for r in load_replacements(storage_dir, doc_id, target_lang)}
    for r in rules:
        by_find[r.find] = {"find": r.find, "replace": r.replace, "match": r.match}
    _write_json(replacements_path(storage_dir, doc_id, target_lang), list(by_find.values()))


def preview_replace(storage_dir: str, doc_id: str, target_lang: str, rules) -> dict:
    """Per-rule occurrence counts over the current .docx (reflects manual editor edits).
    No disk writes. Caller must have ensured the .docx exists."""
    from docx import Document
    from pipeline import docx_edit

    valid = [r for r in rules if r.find and r.find != r.replace]
    document = Document(output_docx_path(storage_dir, doc_id, target_lang))
    counts = docx_edit.count_replacements(document, valid, target_lang)
    results = [{"find": r.find, "replace": r.replace, "count": counts.get(r.find, 0)}
               for r in valid]
    return {"results": results, "total": sum(c["count"] for c in results)}


def bulk_replace(storage_dir: str, doc_id: str, target_lang: str, rules) -> dict:
    """Apply term replacements across the live .docx (preserving manual edits), mirror the
    same change into the translated/edited model(s), and persist rules whose save=True so
    they re-apply on later re-translations. Caller must have ensured the .docx exists.
    Returns {results:[{find,replace,count}], total}."""
    from docx import Document
    from pipeline import docx_edit, text_replace

    valid = [r for r in rules if r.find and r.find != r.replace]

    path = output_docx_path(storage_dir, doc_id, target_lang)
    document = Document(path)
    counts = docx_edit.apply_replacements(document, valid, target_lang)
    if counts:  # nothing matched → don't rewrite the file (avoids a needless DS reload)
        document.save(path)

    # Mirror into the model so the 재번역 list + any later re-render stay consistent with
    # the .docx. (source.json is the original language — left untouched.)
    for model_path in (translated_path(storage_dir, doc_id, target_lang),
                       edited_path(storage_dir, doc_id, target_lang)):
        if not os.path.exists(model_path):
            continue
        with open(model_path, encoding="utf-8") as fh:
            model = SemanticDocument.model_validate(json.load(fh))
        for get, set_ in iter_text_fields(model):
            new, _ = text_replace.apply_to_text(get(), valid, target_lang)
            if new != get():
                set_(new)
        _write_json(model_path, model.model_dump())

    saved = [r for r in valid if r.save]
    if saved:
        save_replacements(storage_dir, doc_id, target_lang, saved)

    results = [{"find": r.find, "replace": r.replace, "count": counts.get(r.find, 0)}
               for r in valid]
    return {"results": results, "total": sum(counts.values())}


def build_segments(storage_dir: str, doc_id: str, target_lang: str, translator=None):
    """Sentence list for 재번역 모드: [{index, id, block_id, source, translated, source_bbox}].

    Enumerates the TRANSLATED model (editor/current order) and pairs each segment to its
    SOURCE counterpart by seg-id. Attaches a page-fraction bbox from the persisted
    source.pdf when present (PDF path); HWP/office uploads have no source.pdf, so
    source_bbox is null. Non-translatable (empty/atomic) segments are skipped but the
    within-fetch ordinal (index) still advances over them so click targets stay stable."""
    from pipeline.translator import _is_atomic  # private helper; not exported at module top

    source = load_source_model(storage_dir, doc_id)
    if translator is not None:
        translated = translate_document(source.model_copy(deep=True), target_lang,
                                        translator=translator)
        save_translated_model(storage_dir, doc_id, target_lang, translated)
    else:
        translated = get_translated_model(storage_dir, doc_id, target_lang)

    span_map: dict = {}
    page_sizes: list = []
    pdf_path = os.path.join(doc_dir(storage_dir, doc_id), "source.pdf")
    if os.path.exists(pdf_path):
        span_map, page_sizes = extractor.extract_span_bboxes(pdf_path)

    # Source segments indexed by seg-id for O(1) pairing regardless of translated order.
    src_by_id = {seg_id: (get, span_ids)
                 for get, _set, span_ids, _bid, seg_id in iter_segments(source)}

    out = []
    i = 0
    for get, _set, _span, block_id, seg_id in iter_segments(translated):
        t = get()                                       # translated text (editor order)
        if seg_id not in src_by_id:                    # no source counterpart ⇒ user addition
            if t.strip():                              # skip a blank new paragraph
                out.append({"index": i, "id": seg_id, "block_id": block_id,
                            "source": "", "translated": t, "source_bbox": None, "added": True})
            i += 1
            continue
        src_get, span_ids = src_by_id[seg_id]
        s = src_get()
        if not s.strip() or _is_atomic(s):
            i += 1
            continue
        bbox = (extractor.segment_source_bbox(span_ids, span_map, page_sizes)
                if span_map else None)
        out.append({"index": i, "id": seg_id, "block_id": block_id,
                    "source": s, "translated": t, "source_bbox": bbox, "added": False})
        i += 1
    return out


def retranslate_one_segment(
    storage_dir: str, doc_id: str, target_lang: str, seg_id: str, guidance: str,
    translator=None,
):
    """Re-translate one segment (by seg-id) from the source with guidance, write it
    back into translated.{lang}.json, and return {id, old_text, new_text}. Raises
    IndexError if the seg-id is not found in source or translated."""
    source = load_source_model(storage_dir, doc_id)
    src_by_id = {sid: get for get, _set, _sp, _bid, sid in iter_segments(source)}
    if seg_id not in src_by_id:
        raise IndexError(f"segment id {seg_id!r} not found in source")
    src_text = src_by_id[seg_id]()
    style_context = _build_style_context(
        storage_dir, doc_id, source, target_lang, query_segments=[src_text]
    )
    new_text = translate_segment(
        src_text, source.source_lang, target_lang, guidance, translator=translator,
        extra_glossary=load_doc_glossary(storage_dir, doc_id),
        style_context=style_context,
    )
    # Keep any saved bulk-replace terms applied: a corrected term shouldn't reappear just
    # because this segment was re-translated fresh from the source.
    saved = load_replacements(storage_dir, doc_id, target_lang)
    if saved:
        from pipeline import text_replace
        rules = [text_replace.ReplaceRule(**r) for r in saved]
        new_text, _ = text_replace.apply_to_text(new_text, rules, target_lang)

    translated = get_translated_model(storage_dir, doc_id, target_lang)
    tr_by_id = {sid: (get, set_) for get, set_, _sp, _bid, sid in iter_segments(translated)}
    if seg_id not in tr_by_id:
        raise IndexError(f"segment id {seg_id!r} absent from the {target_lang} translation")
    old_text = tr_by_id[seg_id][0]()
    tr_by_id[seg_id][1](new_text)
    save_translated_model(storage_dir, doc_id, target_lang, translated)
    return {"id": seg_id, "old_text": old_text, "new_text": new_text}


def glossary_report(storage_dir: str, doc_id: str, target_lang: str) -> list[dict]:
    """Per-term applied/missed audit of the uploaded glossary against the current
    translation. Lazy (no disk writes); [] when no glossary was uploaded."""
    from pipeline import glossary_audit

    glossary = load_doc_glossary(storage_dir, doc_id)
    if not glossary:
        return []
    segments = build_segments(storage_dir, doc_id, target_lang)
    source = load_source_model(storage_dir, doc_id)
    return glossary_audit.audit_glossary(segments, glossary, source.source_lang, target_lang)


def proper_noun_report(storage_dir: str, doc_id: str, target_lang: str, auditor=None) -> list[dict]:
    """Proper nouns translated but NOT in the glossary (고유명사 검수). Lazy (no disk writes),
    mirroring glossary_report; runs even with an empty glossary — that's when it's most useful."""
    from pipeline import proper_noun_audit

    glossary = load_doc_glossary(storage_dir, doc_id)
    segments = build_segments(storage_dir, doc_id, target_lang)
    source = load_source_model(storage_dir, doc_id)
    return proper_noun_audit.audit(segments, glossary, source.source_lang, target_lang, auditor)


def update_glossary_term(storage_dir: str, doc_id: str, source: str,
                         new_target: str, target_lang: str) -> dict:
    """Change an existing glossary entry's 번역어 and unify it in the current translation
    (replace old_target -> new_target via bulk_replace). Updates glossary.json."""
    from pipeline.text_replace import ReplaceRule

    glossary = load_doc_glossary(storage_dir, doc_id)
    old_target = glossary.get(source)
    replaced = 0
    if old_target and old_target != new_target:
        res = bulk_replace(storage_dir, doc_id, target_lang,
                           [ReplaceRule(find=old_target, replace=new_target,
                                        match="smart", save=False)])
        replaced = res["total"]
    glossary[source] = new_target
    save_doc_glossary(storage_dir, doc_id, glossary)
    return {"replaced": replaced}


def add_glossary_term(storage_dir: str, doc_id: str, source: str, target: str) -> None:
    """Register a new glossary entry (applies on next (re)translation, or via
    apply_glossary_term)."""
    glossary = load_doc_glossary(storage_dir, doc_id)
    glossary[source] = target
    save_doc_glossary(storage_dir, doc_id, glossary)


def apply_glossary_term(storage_dir: str, doc_id: str, source_term: str,
                        target_lang: str, translator=None) -> dict:
    """Re-translate the segments whose SOURCE contains source_term, so the (now updated)
    glossary is applied. Returns {retranslated: n}.

    NOTE: this updates translated.{lang}.json only, NOT output.{lang}.docx — the API
    caller must re-render the docx afterward (the /glossary/term endpoint does), or the
    OnlyOffice editor will serve a stale document.
    """
    from pipeline.text_replace import ReplaceRule, compile_rule, count_in_text

    source = load_source_model(storage_dir, doc_id)
    pat = compile_rule(ReplaceRule(find=source_term, replace=""), source.source_lang)
    n = 0
    if pat:
        for get, _set, _sp, _bid, seg_id in iter_segments(source):
            if count_in_text(get(), pat):
                retranslate_one_segment(storage_dir, doc_id, target_lang, seg_id, "",
                                        translator=translator)
                n += 1
    return {"retranslated": n}


def get_export_model(
    storage_dir: str, doc_id: str, target_lang: str | None
) -> SemanticDocument:
    """Model used for rendering/export: user edits win, else translation, else source."""
    if not target_lang:
        return load_source_model(storage_dir, doc_id)
    ep = edited_path(storage_dir, doc_id, target_lang)
    if os.path.exists(ep):
        with open(ep, encoding="utf-8") as fh:
            model = SemanticDocument.model_validate(json.load(fh))
        source_path = os.path.join(doc_dir(storage_dir, doc_id), "source.json")
        try:
            with open(source_path, encoding="utf-8") as fh:
                source_model = SemanticDocument.model_validate(json.load(fh))
            if ensure_block_ids(source_model, model):
                _write_json(source_path, source_model.model_dump())
                _write_json(ep, model.model_dump())
        except FileNotFoundError:
            pass
        return model
    return get_translated_model(storage_dir, doc_id, target_lang)


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
