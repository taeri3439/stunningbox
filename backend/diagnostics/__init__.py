"""Read-only fidelity-diagnosis harness.

Compares the ORIGINAL source PDF pages against the rendered TRANSLATED DOCX, page by
page, and produces a ranked, evidence-backed fidelity gap report. It NEVER mutates the
pipeline or the `storage/` corpus — every output lands under `diagnostics/runs/<run_id>/`.

See `run.py` for the CLI entrypoint:  python -m diagnostics.run --doc-ids all --lang en
"""
