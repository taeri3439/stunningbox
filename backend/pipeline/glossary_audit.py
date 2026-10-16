"""Post-translation glossary application audit (deterministic, inference-based).

No translator instrumentation tells us which glossary entries were honored, so we infer
per segment: a glossary entry {원어: 번역어} is "applied" in a segment when the 원어 occurs
in that segment's SOURCE and the 번역어 occurs in its aligned TRANSLATION. Matching reuses
text_replace (word-boundary for space-separated langs, substring for CJK, case-insensitive)
so it agrees with the bulk-replace/preview features.
"""
from __future__ import annotations

from pipeline.text_replace import ReplaceRule, compile_rule, count_in_text


def audit_glossary(segments: list[dict], glossary: dict[str, str],
                   source_lang: str, target_lang: str) -> list[dict]:
    """segments: build_segments() output ({index, source, translated, source_bbox, block_id}).
    Returns one record per glossary entry with status + per-occurrence locations."""
    out: list[dict] = []
    for src_term, tgt_term in glossary.items():
        if not src_term or not tgt_term:
            continue  # degenerate entry (only via hand-corrupted glossary.json) — omit
        src_pat = compile_rule(ReplaceRule(find=src_term, replace=""), source_lang)
        tgt_pat = compile_rule(ReplaceRule(find=tgt_term, replace=""), target_lang)
        locations: list[dict] = []
        for seg in segments:
            if src_pat and count_in_text(seg["source"], src_pat):
                applied = bool(tgt_pat and count_in_text(seg["translated"], tgt_pat))
                locations.append({
                    "segment_index": seg["index"], "block_id": seg["block_id"],
                    "source_bbox": seg.get("source_bbox"), "applied": applied,
                })
        source_count = len(locations)
        applied_count = sum(1 for loc in locations if loc["applied"])
        if source_count == 0:
            status = "absent"
        elif applied_count == source_count:
            status = "applied"
        elif applied_count == 0:
            status = "missed"
        else:
            status = "partial"
        out.append({"source": src_term, "target": tgt_term,
                    "source_count": source_count, "applied_count": applied_count,
                    "status": status, "locations": locations})
    return out
