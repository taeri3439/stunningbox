"""CLI entrypoint for the fidelity-diagnosis harness.

    python -m diagnostics.run --doc-ids all --lang en [--no-score] [--force]
                              [--limit-pages N] [--run-id latest] [--rerender-source]

Per doc: render output.<lang>.docx -> PNGs, pair with source page PNGs, build side-by-side
composites, (optionally) score each pair with the vision model (cached), structural
cross-check, then aggregate across docs into runs/<run_id>/report.{md,json}.

Read-only over the corpus: every write lands under diagnostics/runs/<run_id>/.
"""
from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from diagnostics import aggregate, pair, render, score, structural
from pipeline import process
from renderers.docx_renderer import render_docx

HERE = os.path.dirname(__file__)
RUNS_ROOT = os.path.join(HERE, "runs")
# Pick up ANTHROPIC_API_KEY etc. from backend/.env, exactly like main.py does.
load_dotenv(os.path.join(os.path.dirname(HERE), ".env"))


def _fresh_docx(storage: str, doc_id: str, lang: str, run_doc_dir: str) -> str | None:
    """Render a CURRENT docx from the model (current renderer), so the diagnosis reflects
    the live pipeline rather than a possibly-stale cached output.<lang>.docx. Returns the
    path, or None if the model can't be loaded (legacy/invalid source.json)."""
    try:
        model = process.get_export_model(storage, doc_id, lang)
    except Exception:  # noqa: BLE001 — legacy/invalid source.json -> caller falls back
        return None
    out = os.path.join(run_doc_dir, f"fresh.{lang}.docx")
    render_docx(model, out, process.asset_dir(storage, doc_id))
    return out


def _source_pngs(storage: str, doc_id: str, run_doc_dir: str, rerender: bool) -> list[str]:
    """Full paths to source page PNGs. Prefer cached assets/page{N}.png; fall back to
    rendering source.pdf into the run dir."""
    if not rerender:
        names = process.list_page_images(storage, doc_id)
        if names:
            ad = process.asset_dir(storage, doc_id)
            return [os.path.join(ad, n) for n in names]
    src_pdf = os.path.join(process.doc_dir(storage, doc_id), "source.pdf")
    if os.path.exists(src_pdf):
        return render.render_pdf_to_pngs(src_pdf, os.path.join(run_doc_dir, "source"))
    return []


def _resolve_doc_ids(storage: str, requested: str, lang: str) -> list[str]:
    if requested and requested != "all":
        return [d.strip() for d in requested.split(",") if d.strip()]
    ids = []
    for name in sorted(os.listdir(storage)):
        d = os.path.join(storage, name)
        if os.path.isdir(d) and os.path.exists(process.output_docx_path(storage, name, lang)):
            ids.append(name)
    return ids


def diagnose_doc(storage: str, doc_id: str, lang: str, run_dir: str, *,
                 do_score: bool, force: bool, limit_pages: int | None,
                 rerender_source: bool, fresh: bool, client=None) -> None:
    run_doc_dir = os.path.join(run_dir, doc_id)
    os.makedirs(run_doc_dir, exist_ok=True)

    docx_path = None
    if fresh:
        docx_path = _fresh_docx(storage, doc_id, lang, run_doc_dir)
    if docx_path is None:  # not fresh, or fresh render unavailable -> cached output
        docx_path = process.output_docx_path(storage, doc_id, lang)
    if not os.path.exists(docx_path):
        print(f"  [skip] {doc_id}: no docx to diagnose (fresh failed and no output.{lang}.docx)")
        return

    # Structural cross-check first — it needs only source.json + the docx (no LibreOffice,
    # no API), so it's the backstop that always produces signal even if rendering fails.
    source_json = os.path.join(process.doc_dir(storage, doc_id), "source.json")
    chk = structural.cross_check(source_json, docx_path)

    source_pngs = _source_pngs(storage, doc_id, run_doc_dir, rerender_source)

    # Visual rendering (DOCX->PDF->PNG) needs LibreOffice; degrade gracefully if absent.
    rendered_pngs: list[str] = []
    render_error = None
    if source_pngs:
        try:
            rendered_pngs = render.render_docx_to_pngs(
                docx_path, os.path.join(run_doc_dir, "rendered"))
        except Exception as e:  # noqa: BLE001 — LibreOffice missing / conversion failed
            render_error = str(e)

    pairs = pair.build_pairs(source_pngs, rendered_pngs)
    mismatch = (pair.page_count_mismatch(source_pngs, rendered_pngs)
                if source_pngs and rendered_pngs else None)

    meta = {"doc_id": doc_id, "lang": lang, "page_count_mismatch": mismatch,
            "structural": chk.model_dump(), "render_error": render_error}
    with open(os.path.join(run_doc_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    if render_error:
        print(f"  [partial] {doc_id}: structural only (render failed: {render_error[:60]})"
              + (f"; {'; '.join(chk.notes)}" if chk.notes else ""))
        return
    if not source_pngs:
        print(f"  [partial] {doc_id}: no source page images / source.pdf; structural only"
              + (f"; {'; '.join(chk.notes)}" if chk.notes else ""))
        return

    pairs_dir = os.path.join(run_doc_dir, "pairs")
    for pp in pairs:
        if limit_pages is not None and pp.index >= limit_pages:
            break
        composite_abs = os.path.join(pairs_dir, f"pair{pp.index}.png")
        pair.side_by_side(pp.source_png, pp.rendered_png, composite_abs)
        composite_rel = os.path.relpath(composite_abs, run_dir).replace(os.sep, "/")

        page_json = os.path.join(run_doc_dir, f"page{pp.index}.json")
        if not do_score:
            continue
        if os.path.exists(page_json) and not force:
            continue
        finding = score.score_pair(pp, structural_notes=chk.notes, client=client)
        finding.doc_id = doc_id
        finding.pair_png = composite_rel
        with open(page_json, "w", encoding="utf-8") as fh:
            json.dump(finding.model_dump(), fh, ensure_ascii=False, indent=2)
    print(f"  [done] {doc_id}: {len(pairs)} page pairs"
          + (f", structural: {'; '.join(chk.notes)}" if chk.notes else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="Document fidelity diagnosis harness")
    ap.add_argument("--storage", default=os.environ.get("STORAGE_DIR", "storage"))
    ap.add_argument("--doc-ids", default="all", help="'all' or comma-separated ids")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--run-id", default="latest")
    ap.add_argument("--no-score", action="store_true", help="render + structural only (no API)")
    ap.add_argument("--force", action="store_true", help="re-score cached pages")
    ap.add_argument("--limit-pages", type=int, default=None)
    ap.add_argument("--rerender-source", action="store_true")
    ap.add_argument("--fresh", action="store_true",
                    help="render the docx from the CURRENT model (live renderer) instead "
                         "of the possibly-stale cached output.<lang>.docx")
    args = ap.parse_args()

    from pipeline.llm_provider import llm_configured

    do_score = not args.no_score and llm_configured()
    if not args.no_score and not do_score:
        print("[warn] no LLM API key — running render + structural only (no scoring)")

    run_dir = os.path.join(RUNS_ROOT, args.run_id)
    os.makedirs(run_dir, exist_ok=True)

    doc_ids = _resolve_doc_ids(args.storage, args.doc_ids, args.lang)
    print(f"Diagnosing {len(doc_ids)} docs -> {run_dir}")

    client = None
    if do_score:
        import anthropic
        client = anthropic.Anthropic()

    for doc_id in doc_ids:
        try:
            diagnose_doc(args.storage, doc_id, args.lang, run_dir,
                         do_score=do_score, force=args.force,
                         limit_pages=args.limit_pages,
                         rerender_source=args.rerender_source,
                         fresh=args.fresh, client=client)
        except Exception as e:  # noqa: BLE001 — one bad doc shouldn't sink the run
            print(f"  [error] {doc_id}: {e}")

    report = aggregate.aggregate(run_dir)
    md_path, json_path = aggregate.write_reports(report, run_dir)
    print(f"\nReport: {md_path}\n        {json_path}")


if __name__ == "__main__":
    main()
