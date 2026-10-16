"""Stage 3 — translation of leaf text only.

We never touch the tree: `iter_text_fields` yields every translatable string in the
SemanticDocument, we translate them as a batch, and write them straight back. Markers,
numbers, and image refs are structural and stay put. A per-language-pair glossary
pins domain terminology (스태빌라이저 -> Stabilizer, 정격하중 -> Rated load, ...).
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import threading
from typing import Callable, Optional

from models import SemanticDocument, iter_text_fields
from pipeline._parallel import parallel_map
from pipeline.llm_provider import openai_model, openai_tool_call, resolve_llm_provider

GLOSSARY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "glossary")
TRANSLATOR_MODEL = os.environ.get("TRANSLATOR_MODEL", "claude-opus-4-8")

LANG_NAMES = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese (Simplified)",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "vi": "Vietnamese",
}


def load_glossary(source_lang: str, target_lang: str) -> dict[str, str]:
    path = os.path.join(GLOSSARY_DIR, f"{source_lang}-{target_lang}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


# A progress sink: (stage, done, total). Matches process.ProgressFn.
ProgressFn = Callable[[str, int, int], None]

# Segments per translation request (also the unit of progress reporting).
CHUNK_SIZE = 80

# Cells that look like a header row (skipped when first row of an uploaded glossary CSV).
_GLOSSARY_HEADER = {
    "원어", "번역어", "원문", "번역", "source", "target", "term", "translation",
    "source term", "target term",
}


def parse_glossary_csv(text: str) -> dict[str, str]:
    """Parse a user glossary CSV ('source,target' per row) into {source: target}. Skips
    blank rows, rows with < 2 columns, and an optional header row. Later rows win on a
    duplicate source term. Values are trimmed."""
    out: dict[str, str] = {}
    text = text.lstrip("﻿")  # tolerate a UTF-8 BOM (Excel "CSV UTF-8" export)
    for ri, row in enumerate(csv.reader(io.StringIO(text))):
        if len(row) < 2:
            continue
        src, tgt = row[0].strip(), row[1].strip()
        if not src or not tgt:
            continue
        if ri == 0 and (src.lower() in _GLOSSARY_HEADER and tgt.lower() in _GLOSSARY_HEADER):
            continue  # header row
        out[src] = tgt
    return out

# Re-translation direction presets (재번역 모드). Appended to the translator system prompt
# so a single sentence can be re-translated "more concise", "more formal", etc.
PRESET_GUIDANCE = {
    "concise": "Make the translation more concise — keep only the essential meaning.",
    "detailed": "Make the translation more detailed and explanatory; make implicit "
                "meaning explicit.",
    "formal": "Use a formal, written register suitable for an official document.",
    "natural": "Translate idiomatically and naturally rather than literally, while "
               "preserving the meaning.",
}


def build_guidance(preset: str | None, custom: str | None) -> str:
    """Combine an optional direction preset with an optional free-text instruction."""
    parts = []
    if preset and preset in PRESET_GUIDANCE:
        parts.append(PRESET_GUIDANCE[preset])
    if custom and custom.strip():
        parts.append(custom.strip())
    return " ".join(parts)


def translate_segment(
    source_text: str,
    source_lang: str,
    target_lang: str,
    guidance: str = "",
    translator=None,
    extra_glossary: dict | None = None,
    style_context: str = "",
) -> str:
    """Re-translate a single segment with optional direction guidance + style context.
    Empty / atomic (pure number/symbol) segments are returned unchanged."""
    if not source_text.strip() or _is_atomic(source_text):
        return source_text
    translator = translator or get_translator()
    glossary = {**load_glossary(source_lang, target_lang), **(extra_glossary or {})}
    out = translator.translate(
        [source_text], source_lang, target_lang, glossary,
        guidance=guidance, style_context=style_context,
    )
    return out[0] if out else source_text


def translate_document(
    doc: SemanticDocument,
    target_lang: str,
    source_lang: str | None = None,
    translator=None,
    on_progress: Optional[ProgressFn] = None,
    extra_glossary: dict | None = None,
    style_context: str = "",
    tm_map: dict | None = None,
) -> SemanticDocument:
    source_lang = source_lang or doc.source_lang
    accessors = list(iter_text_fields(doc))
    texts = [get() for get, _ in accessors]
    # only translate non-empty, non-numeric segments
    idxs = [i for i, t in enumerate(texts) if t.strip() and not _is_atomic(t)]
    payload = [texts[i] for i in idxs]

    if payload:
        translator = translator or get_translator()
        glossary = {**load_glossary(source_lang, target_lang), **(extra_glossary or {})}
        tm_map = tm_map or {}
        # Identical source segments must translate identically: a table repeating
        # 통과/정지 across 14 rows must not drift between GO/NO-GO and PASS/STOP when
        # occurrences land in different chunks. Translate each UNIQUE text once
        # (first-seen order) and fan the result out to every occurrence — consistency
        # by construction, and repeated boilerplate stops costing LLM tokens.
        uniq_index: dict[str, int] = {}
        uniq_texts: list[str] = []
        for seg in payload:
            if seg not in uniq_index:
                uniq_index[seg] = len(uniq_texts)
                uniq_texts.append(seg)
        # Translation-Memory reuse: a segment with a high-confidence approved match skips
        # the LLM entirely (verbatim consistency for recurring boilerplate).
        llm_pos = [k for k, seg in enumerate(uniq_texts) if seg not in tm_map]
        llm_payload = [uniq_texts[k] for k in llm_pos]
        total_chunks = max(1, math.ceil(len(llm_payload) / CHUNK_SIZE)) if llm_payload else 1
        if on_progress:
            on_progress("translate", 0, total_chunks)
        on_done = _chunk_reporter(on_progress, total_chunks) if on_progress else None
        llm_out = translator.translate(
            llm_payload, source_lang, target_lang, glossary,
            on_done=on_done, style_context=style_context,
        ) if llm_payload else []
        uniq_results = list(uniq_texts)
        for k, seg in enumerate(uniq_texts):
            if seg in tm_map:
                uniq_results[k] = tm_map[seg]
        for pos, k in enumerate(llm_pos):
            uniq_results[k] = llm_out[pos]
        for i, seg in zip(idxs, payload):
            _, setter = accessors[i]
            setter(uniq_results[uniq_index[seg]])
        if on_progress and not llm_payload:    # all segments TM-reused → report completion
            on_progress("translate", 1, 1)

    doc.target_lang = target_lang
    return doc


def _chunk_reporter(on_progress: ProgressFn, total: int) -> Callable[[], None]:
    """Thread-safe per-chunk counter forwarding ("translate", k, total) to the sink."""
    state = {"n": 0}
    lock = threading.Lock()

    def bump() -> None:
        with lock:
            state["n"] += 1
            n = state["n"]
        on_progress("translate", n, total)

    return bump


# A TOC/index dot-leader run ("Title ............ 12"). These long runs waste tokens and
# read like a non-translatable reference, so the model tends to echo the segment back in
# the source language. Collapse them before sending; the renderer recreates the leader.
_LEADER = re.compile(r"[.·…]{4,}")


def _collapse_leaders(s: str) -> str:
    return _LEADER.sub("...", s)


def _is_atomic(t: str) -> bool:
    """Pure number / code / symbol segment — keep verbatim, don't spend a translation.

    str.isalpha() is Unicode-aware, so Korean/Latin/etc. all count as translatable
    content; segments like "1 960 N" or "± 4 %" have no alphabetic char and are skipped.
    """
    return not any(ch.isalpha() for ch in t)


# --------------------------------------------------------------------------- #


class MockTranslator:
    """Offline translator: tags text so the pipeline/renderers can be tested
    without an API key. Applies glossary so that path is exercised too."""

    def translate(self, texts, source_lang, target_lang, glossary, on_done=None,
                  guidance="", style_context=""):
        out = []
        for t in texts:
            for src, dst in glossary.items():
                t = t.replace(src, dst)
            tag = (f"[{target_lang}]" + (f"[g:{guidance}]" if guidance else "")
                   + ("[s]" if style_context else ""))
            out.append(f"{tag} {t}")
        if on_done:
            on_done()
        return out


class ClaudeTranslator:
    def __init__(self, client=None, model: str = TRANSLATOR_MODEL):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def translate(self, texts, source_lang, target_lang, glossary, on_done=None,
                  guidance="", style_context=""):
        src = LANG_NAMES.get(source_lang, source_lang)
        tgt = LANG_NAMES.get(target_lang, target_lang)
        gloss_lines = "\n".join(f"  {k} => {v}" for k, v in glossary.items())
        # Order: base → glossary (hard term constraints) → style reference (soft style cue)
        # → per-request guidance. style_context already begins with its own blank line.
        system = (
            f"You are a professional technical translator ({src} -> {tgt}). "
            "Translate each input segment faithfully. Keep all numbers, units, codes, "
            "and symbols EXACTLY as-is. Do not add or drop segments. Preserve meaning "
            "and register for an engineering standard."
            + (f"\nUse this glossary strictly:\n{gloss_lines}" if glossary else "")
            + (style_context or "")
            + (f"\nAdditional instruction for this request: {guidance}" if guidance else "")
        )
        tool = {
            "name": "emit_translations",
            "description": "Return translations aligned 1:1 with the input segments.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "translations": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["translations"],
            },
        }
        # Batch to keep each request bounded, then run the batches concurrently so a
        # large document doesn't translate one chunk at a time.
        chunks = [texts[i : i + CHUNK_SIZE] for i in range(0, len(texts), CHUNK_SIZE)]
        parts = parallel_map(
            lambda c: self._translate_chunk(c, system, tool), chunks, on_done=on_done
        )
        return [t for part in parts for t in part]

    def _translate_chunk(self, chunk, system, tool) -> list[str]:
        if not chunk:
            return []
        numbered = json.dumps([_collapse_leaders(c) for c in chunk], ensure_ascii=False)
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_translations"},
            messages=[
                {
                    "role": "user",
                    "content": "Translate these segments (JSON array), returning a "
                    "JSON array of the same length and order:\n" + numbered,
                }
            ],
        )
        got = []
        for part in msg.content:
            if part.type == "tool_use":
                got = part.input.get("translations", [])
        if len(got) == len(chunk):
            return got
        # Count mismatch (model dropped/merged segments, or hit the token limit). Rather
        # than fall the WHOLE chunk back to the source language, recover granularly: split
        # and retry. Only a single segment that still fails falls back to identity.
        if len(chunk) > 1:
            mid = len(chunk) // 2
            return (self._translate_chunk(chunk[:mid], system, tool)
                    + self._translate_chunk(chunk[mid:], system, tool))
        return chunk


class OpenAITranslator:
    """GPT translation via forced function call (same segment contract as ClaudeTranslator)."""

    def __init__(self, client=None, model: str | None = None):
        if client is not None:
            self._client = client
        self.model = model or openai_model(
            dedicated_env="OPENAI_TRANSLATOR_MODEL",
            shared_env="TRANSLATOR_MODEL",
            default="gpt-4o",
        )

    def translate(self, texts, source_lang, target_lang, glossary, on_done=None,
                  guidance="", style_context=""):
        src = LANG_NAMES.get(source_lang, source_lang)
        tgt = LANG_NAMES.get(target_lang, target_lang)
        gloss_lines = "\n".join(f"  {k} => {v}" for k, v in glossary.items())
        system = (
            f"You are a professional technical translator ({src} -> {tgt}). "
            "Translate each input segment faithfully. Keep all numbers, units, codes, "
            "and symbols EXACTLY as-is. Do not add or drop segments. Preserve meaning "
            "and register for an engineering standard."
            + (f"\nUse this glossary strictly:\n{gloss_lines}" if glossary else "")
            + (style_context or "")
            + (f"\nAdditional instruction for this request: {guidance}" if guidance else "")
        )
        tool = {
            "name": "emit_translations",
            "description": "Return translations aligned 1:1 with the input segments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "translations": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["translations"],
            },
        }
        chunks = [texts[i : i + CHUNK_SIZE] for i in range(0, len(texts), CHUNK_SIZE)]
        parts = parallel_map(
            lambda c: self._translate_chunk(c, system, tool), chunks, on_done=on_done
        )
        return [t for part in parts for t in part]

    def _translate_chunk(self, chunk, system, tool) -> list[str]:
        if not chunk:
            return []
        numbered = json.dumps([_collapse_leaders(c) for c in chunk], ensure_ascii=False)
        parsed, _finish = openai_tool_call(
            model=self.model,
            system=system,
            user_content=(
                "Translate these segments (JSON array), returning a JSON array of the same "
                "length and order via emit_translations:\n" + numbered
            ),
            tool_name=tool["name"],
            tool_description=tool["description"],
            parameters=tool["parameters"],
            max_tokens=8000,
        )
        got = (parsed or {}).get("translations", [])
        if len(got) == len(chunk):
            return got
        if len(chunk) > 1:
            mid = len(chunk) // 2
            return (self._translate_chunk(chunk[:mid], system, tool)
                    + self._translate_chunk(chunk[mid:], system, tool))
        return chunk


def get_translator():
    provider = resolve_llm_provider()
    if provider == "openai":
        return OpenAITranslator()
    if provider == "anthropic":
        return ClaudeTranslator()
    return MockTranslator()
