"""Rendered-translated-PDF projections for the viewer.

The DS-converted PDF (see ds_convert.py) IS the editor's layout. This module
projects it for the frontend: page PNGs (the visual layer) and text-position
maps (segment/term bboxes for the interaction overlays), mirroring how the
source pane works (page images + fraction-based hotspots).
"""
from __future__ import annotations

import json
import os
import re
import uuid

import fitz  # PyMuPDF

import onlyoffice
from pipeline import ds_convert


def render_pages(pdf_path: str, out_dir: str, dpi: int = 150) -> dict:
    """Render each page to out_dir/tpage{N}.png + manifest.json. Fresh manifest
    (same pdf version) returns without touching the PNGs."""
    version = onlyoffice.document_key(pdf_path)  # path+mtime+size hash works for any file
    manifest_path = os.path.join(out_dir, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                m = json.load(fh)
            if m.get("version") == version:
                return m
        except (OSError, ValueError):
            pass
    # Stale (or missing) manifest: regenerate under a per-out_dir lock. Two concurrent
    # callers for the same tpages dir (e.g. tpages + tsegments fetched together, both
    # racing a stale cache) would otherwise interleave the delete+render loop below or
    # race os.replace on the manifest (PermissionError on Windows) — the same hazard
    # ensure_pdf hardened against in ds_convert.py. Reuse its private per-key lock
    # helper rather than duplicating it, keyed on out_dir (ds_convert's own callers key
    # it on (storage_dir, doc_id, lang); any hashable tuple works as long as it's
    # unique per resource, so out_dir alone in the first slot is enough here).
    with ds_convert._lock_for(out_dir, "pdf_map.render_pages", ""):
        # Re-check freshness INSIDE the lock: a concurrent caller may have just finished
        # regenerating this exact version while we were waiting on the lock.
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, encoding="utf-8") as fh:
                    m = json.load(fh)
                if m.get("version") == version:
                    return m
            except (OSError, ValueError):
                pass
        os.makedirs(out_dir, exist_ok=True)
        for old in os.listdir(out_dir):
            if old.startswith("tpage") and old.endswith(".png"):
                os.remove(os.path.join(out_dir, old))
        doc = fitz.open(pdf_path)
        zoom = dpi / 72.0
        pages = []
        for i in range(doc.page_count):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            name = f"tpage{i}.png"
            pix.save(os.path.join(out_dir, name))
            pages.append({"n": i, "name": name})
        rect = doc[0].rect if doc.page_count else fitz.Rect(0, 0, 0, 0)
        manifest = {
            "version": version,
            "page_w": round(rect.width, 2),
            "page_h": round(rect.height, 2),
            "pages": pages,
        }
        doc.close()
        # Unique-per-attempt tmp name (pid + random hex): defense in depth even if two
        # processes ever raced past the lock, mirroring ensure_pdf's own hardening.
        tmp = f"{manifest_path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        os.replace(tmp, manifest_path)
        return manifest


# A TOC dot-leader run or decoration; drop before tokenizing (the model side
# collapses leaders too — see translator._collapse_leaders).
_LEADER_RE = re.compile(r"[.·…]{2,}")

# How many tokens must anchor a segment before greedy extension takes over.
_MATCH_HEAD = 8
# How many words ahead of the cursor we search for a segment's anchor (skips
# running-band text / page numbers sitting between body segments).
_SKIP_WINDOW = 400


def _norm_tokens(text: str) -> list[str]:
    return [t for t in _LEADER_RE.sub(" ", text).casefold().split() if t]


def align_words(words, segments):
    """Greedy in-order alignment of document-order segments onto the PDF's word
    stream. words: [(page, fitz.Rect, normalized_token)]. Returns
    {seg_id: (page, union_rect_on_first_page) | None}. A miss does NOT advance
    the cursor, so one unmatched segment can't derail the rest.

    Consecutive misses escalate the search window (400, 800, 1600, ... capped
    at 12800) so a long unmatchable run (e.g. an image-fallback table page)
    can't strand the cursor forever — later segments still get a widening
    look-ahead. A hit resets the miss counter, keeping near-first preference:
    the first attempt after any hit always uses the small window."""
    out = {}
    cur = 0
    n = len(words)
    misses = 0
    for seg in segments:
        toks = _norm_tokens(seg["text"])
        if not toks:
            out[seg["id"]] = None
            continue
        head = toks[:_MATCH_HEAD]
        window = _SKIP_WINDOW << min(misses, 5)
        found = -1
        for j in range(cur, min(cur + window, n - len(head) + 1)):
            if all(words[j + k][2] == head[k] for k in range(len(head))):
                found = j
                break
        if found < 0:
            out[seg["id"]] = None
            misses += 1
            continue
        misses = 0
        k = found + len(head)
        for t in toks[_MATCH_HEAD:]:
            if k < n and words[k][2] == t:
                k += 1
            else:
                break
        first_page = words[found][0]
        rect = fitz.Rect(words[found][1])
        for w in words[found + 1:k]:
            if w[0] == first_page:
                rect |= w[1]
        out[seg["id"]] = (first_page, rect)
        cur = k
    return out


def _pdf_words(doc) -> list:
    # A PyMuPDF "word" can glue a TOC leader run and its page number onto the
    # preceding text (e.g. "Application.........8" -> ["application", "8"]).
    # Emit one stream entry PER normalized token (all sharing that word's
    # rect) so nothing is silently dropped and short TOC segments can still
    # match; pure leader-dot "words" normalize to [] and contribute nothing.
    words = []
    for pi in range(doc.page_count):
        for x0, y0, x1, y1, word, *_ in doc[pi].get_text("words"):
            rect = fitz.Rect(x0, y0, x1, y1)
            for tok in _norm_tokens(word):
                words.append((pi, rect, tok))
    return words


def _fraction(page_rect, page: int, rect) -> dict:
    x = rect.x0 / page_rect.width
    y = rect.y0 / page_rect.height
    w = max(rect.width, 1.0) / page_rect.width
    h = max(rect.height, 1.0) / page_rect.height
    # The 1pt floor above can push a degenerate rect near the page's
    # right/bottom edge past it; shrink w/h (never x/y) so x+w and y+h stay
    # within the page.
    if x + w > 1.0:
        w = max(1.0 - x, 0.0)
    if y + h > 1.0:
        h = max(1.0 - y, 0.0)
    x, y, w, h = round(x, 4), round(y, 4), round(w, 4), round(h, 4)
    if x + w > 1.0:  # guard against independent rounding pushing it back over
        w = round(1.0 - x, 4)
    if y + h > 1.0:
        h = round(1.0 - y, 4)
    return {"page": page, "x": x, "y": y, "w": w, "h": h}


def map_segments(pdf_path: str, segments: list) -> dict:
    doc = fitz.open(pdf_path)
    try:
        aligned = align_words(_pdf_words(doc), segments)
        out = {}
        for seg_id, hit in aligned.items():
            if hit is None:
                out[seg_id] = None
            else:
                page, rect = hit
                out[seg_id] = _fraction(doc[page].rect, page, rect)
        return out
    finally:
        doc.close()


def find_terms(pdf_path: str, terms: list) -> dict:
    """search_for-based term hit boxes (glossary/proper-noun highlights)."""
    doc = fitz.open(pdf_path)
    try:
        out = {t: [] for t in terms}
        for pi in range(doc.page_count):
            page = doc[pi]
            for t in terms:
                if not t.strip() or len(out[t]) >= 200:
                    continue
                for rect in page.search_for(t)[: 200 - len(out[t])]:
                    out[t].append(_fraction(page.rect, pi, rect))
        return out
    finally:
        doc.close()
