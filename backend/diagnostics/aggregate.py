"""Roll per-page findings into a corpus-wide ranked report.

Ranking rule: dimensions sorted by (mean_score ascending, high_severity_count
descending) so the biggest data-backed gaps surface first — serving the diagnose-first
goal. Emits a machine-readable report.json and a human report.md.
"""
from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict

from diagnostics import schema

# Dimensions whose page-by-page score degrades when the rendered docx paginates
# differently from the source (positional misalignment). Marked in the report.
ALIGNMENT_SENSITIVE = {
    "table_structure", "table_shading", "figure_placement", "figure_wrap",
    "overall_layout", "indent", "linebreak_fidelity", "heading_hierarchy",
}


def _read_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def aggregate(run_dir: str) -> dict:
    """Read every <doc_id>/page*.json + meta.json under run_dir and build the report."""
    by_dim_scores: dict[str, list[int]] = defaultdict(list)
    by_dim_high: dict[str, int] = defaultdict(int)
    by_dim_pages: dict[str, int] = defaultdict(int)
    by_dim_examples: dict[str, list[dict]] = defaultdict(list)
    doc_summaries: list[dict] = []
    mismatches: list[dict] = []
    structural_losses: list[dict] = []
    total_pages = 0

    for doc_id in sorted(_doc_dirs(run_dir)):
        ddir = os.path.join(run_dir, doc_id)
        meta = _read_json(os.path.join(ddir, "meta.json")) if os.path.exists(
            os.path.join(ddir, "meta.json")) else {}
        if meta.get("page_count_mismatch"):
            mismatches.append({"doc_id": doc_id, **meta["page_count_mismatch"]})
        chk = meta.get("structural") or {}
        if chk.get("notes"):
            structural_losses.append({"doc_id": doc_id, "notes": chk["notes"],
                                      "render_error": bool(meta.get("render_error"))})

        doc_scores: list[float] = []
        for name in sorted(os.listdir(ddir)):
            if not (name.startswith("page") and name.endswith(".json")):
                continue
            pf = schema.PageFinding.model_validate(_read_json(os.path.join(ddir, name)))
            total_pages += 1
            if pf.page_score is not None:
                doc_scores.append(pf.page_score)
            for d in pf.dimensions:
                if d.score is None:
                    continue
                by_dim_scores[d.dimension].append(d.score)
                if d.severity == "high":
                    by_dim_high[d.dimension] += 1
                if d.score <= 2:
                    by_dim_pages[d.dimension] += 1
                    by_dim_examples[d.dimension].append({
                        "doc_id": doc_id, "page_index": pf.page_index, "score": d.score,
                        "pair_png": pf.pair_png, "observation": d.observation,
                    })
        doc_summaries.append({
            "doc_id": doc_id,
            "mean_score": round(statistics.mean(doc_scores), 2) if doc_scores else None,
            "pages_scored": len(doc_scores),
            "structural": meta.get("structural"),
            "structural_notes": (meta.get("structural") or {}).get("notes", []),
        })

    dimensions = []
    for dim in schema.DIMENSIONS:
        scores = by_dim_scores.get(dim, [])
        dimensions.append({
            "dimension": dim,
            "rubric": schema.RUBRIC[dim],
            "mean_score": round(statistics.mean(scores), 2) if scores else None,
            "scored_pages": len(scores),
            "high_severity_count": by_dim_high.get(dim, 0),
            "pages_affected": by_dim_pages.get(dim, 0),
            "affected_fraction": round(by_dim_pages.get(dim, 0) / total_pages, 2) if total_pages else 0.0,
            "worst_examples": sorted(
                by_dim_examples.get(dim, []), key=lambda e: e["score"]
            )[:5],
        })
    # Rank: lowest mean first (None means never applicable -> sort last), then most highs.
    dimensions.sort(key=lambda d: (
        d["mean_score"] if d["mean_score"] is not None else 99,
        -d["high_severity_count"],
    ))

    return {
        "total_pages": total_pages,
        "docs": len(doc_summaries),
        "dimensions": dimensions,
        "doc_summaries": doc_summaries,
        "page_count_mismatches": mismatches,
        "structural_losses": structural_losses,
    }


def _doc_dirs(run_dir: str) -> list[str]:
    if not os.path.isdir(run_dir):
        return []
    return [n for n in os.listdir(run_dir)
            if os.path.isdir(os.path.join(run_dir, n))]


def write_reports(report: dict, run_dir: str) -> tuple[str, str]:
    json_path = os.path.join(run_dir, "report.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    md_path = os.path.join(run_dir, "report.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(_render_md(report))
    return md_path, json_path


def _render_md(report: dict) -> str:
    out: list[str] = []
    out.append("# Fidelity diagnosis report\n")
    out.append(f"- Docs: **{report['docs']}**  ·  Scored pages: **{report['total_pages']}**\n")

    # Page-by-page positional comparison only holds while page counts match. When the
    # translated docx reflows to a different page count, rendered-page-N stops matching
    # source-page-N, so alignment-SENSITIVE dimensions (table_structure, table_shading,
    # figure_placement, overall_layout, indent, linebreak_fidelity) get contaminated by
    # misalignment. The structural cross-check below is alignment-independent — trust it
    # for "is content actually lost". Alignment-INDEPENDENT style dims (font_family,
    # alignment, emphasis, line_spacing, text_color) stay reliable on every page.
    if any(m.get("severity") == "high" for m in report.get("page_count_mismatches", [])):
        out.append(
            "\n> ⚠️ **Alignment caveat:** some docs reflowed to a very different page "
            "count (see Page-count mismatches). For those, alignment-SENSITIVE dimensions "
            "(table_structure, table_shading, figure_placement, overall_layout, indent, "
            "linebreak_fidelity) are partly misalignment artifacts — cross-check against "
            "the Structural section. Font/alignment/emphasis/spacing/colour remain reliable.\n"
        )

    losses = report.get("structural_losses", [])
    if losses:
        out.append("\n## Structural losses (no-API cross-check: source.json vs rendered docx)\n")
        for s in losses:
            out.append(f"- `{s['doc_id']}`: {'; '.join(s['notes'])}")

    out.append("\n## Ranked fidelity gaps (worst first)\n")
    out.append("† = alignment-sensitive (trust only where page counts match / cross-check structural).\n")
    out.append("| Rank | Dimension | Mean (0-4) | % pages affected | High-sev | Scored |")
    out.append("|---|---|---|---|---|---|")
    for i, d in enumerate(report["dimensions"], 1):
        mean = "n/a" if d["mean_score"] is None else f"{d['mean_score']:.2f}"
        mark = " †" if d["dimension"] in ALIGNMENT_SENSITIVE else ""
        out.append(
            f"| {i} | {d['dimension']}{mark} | {mean} | {int(d['affected_fraction']*100)}% "
            f"| {d['high_severity_count']} | {d['scored_pages']} |"
        )

    out.append("\n## Top gaps — evidence\n")
    for d in report["dimensions"]:
        if not d["worst_examples"]:
            continue
        out.append(f"\n### {d['dimension']} — {d['rubric']}")
        for e in d["worst_examples"]:
            out.append(
                f"- `{e['doc_id']}` p{e['page_index']} (score {e['score']}): "
                f"{e['observation']}  \n  ![pair]({e['pair_png']})"
            )

    if report["page_count_mismatches"]:
        out.append("\n## Page-count mismatches (reflow signal)\n")
        for m in report["page_count_mismatches"]:
            out.append(
                f"- `{m['doc_id']}`: source {m['source_pages']} vs rendered "
                f"{m['rendered_pages']} pages (gap {m['gap']}, {m['severity']})"
            )

    out.append("\n## Per-document\n")
    for s in report["doc_summaries"]:
        mean = "n/a" if s["mean_score"] is None else f"{s['mean_score']:.2f}"
        notes = f" — {'; '.join(s['structural_notes'])}" if s.get("structural_notes") else ""
        out.append(f"- `{s['doc_id']}`: mean {mean} ({s['pages_scored']} pages){notes}")

    return "\n".join(out) + "\n"
