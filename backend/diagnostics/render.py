"""Render the translated DOCX to per-page PNGs for visual comparison.

Reuses the SAME machinery the pipeline already uses so the rendered pages are
apples-to-apples with the existing source `assets/page{N}.png`:
  * DOCX -> PDF via `convert._run_soffice(docx, out, "pdf")` (LibreOffice headless).
  * PDF page -> PNG via PyMuPDF `get_pixmap(dpi=RENDER_DPI)` (150 DPI, same as extractor).

All output lands in `out_dir` (always under diagnostics/runs/), never in storage/.
"""
from __future__ import annotations

import os

import fitz  # PyMuPDF

from pipeline import convert
from pipeline.extractor import RENDER_DPI


def render_docx_to_pngs(docx_path: str, out_dir: str, dpi: int = RENDER_DPI) -> list[str]:
    """Convert `docx_path` to PDF (LibreOffice) then rasterise each page to
    `rendered{N}.png` in `out_dir`. Returns the ordered PNG paths."""
    os.makedirs(out_dir, exist_ok=True)
    pdf_path = convert._run_soffice(docx_path, out_dir, "pdf")
    doc = fitz.open(pdf_path)
    paths: list[str] = []
    try:
        for pno in range(doc.page_count):
            pix = doc[pno].get_pixmap(dpi=dpi)
            out = os.path.join(out_dir, f"rendered{pno}.png")
            pix.save(out)
            paths.append(out)
    finally:
        doc.close()
    return paths


def render_pdf_to_pngs(pdf_path: str, out_dir: str, dpi: int = RENDER_DPI) -> list[str]:
    """Rasterise a source PDF to `source{N}.png` — used by --rerender-source for a fresh
    PDF that has no cached assets/page{N}.png yet."""
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    paths: list[str] = []
    try:
        for pno in range(doc.page_count):
            pix = doc[pno].get_pixmap(dpi=dpi)
            out = os.path.join(out_dir, f"source{pno}.png")
            pix.save(out)
            paths.append(out)
    finally:
        doc.close()
    return paths
