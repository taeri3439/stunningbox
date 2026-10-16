"""No-API structural cross-check: compare the source.json inventory against the rendered
DOCX. This is the deterministic backstop behind the vision pass — if the source has 3
tables but the rendering has 1, that's a hard table_structure loss even if the model
misses it. Doc-level (not per-page), because the rendered docx paginates differently.
"""
from __future__ import annotations

import json

from docx import Document
from docx.oxml.ns import qn

from diagnostics.schema import StructuralCheck


def source_inventory_from_json(source_json_path: str) -> dict:
    """Count block types straight from source.json by the `type` discriminator.

    Deliberately avoids `SemanticDocument.model_validate` so legacy/schema-drifted
    source.json files (e.g. an older `running_header` stored as a bare string) still
    yield structural counts instead of erroring the whole doc out."""
    with open(source_json_path, encoding="utf-8") as fh:
        data = json.load(fh)
    blocks = data.get("blocks", []) if isinstance(data, dict) else []
    types = [b.get("type") for b in blocks if isinstance(b, dict)]
    return {
        "tables": types.count("table"),
        "figures": types.count("figure"),
        "headings": types.count("heading"),
    }


def rendered_inventory(docx_path: str) -> dict:
    """Count tables and embedded images in the rendered docx. Images are counted via
    `a:blip` so both inline and floated pictures are included."""
    d = Document(docx_path)
    tables = len(d.tables)
    blips = d.element.body.findall(".//" + qn("a:blip"))
    return {"tables": tables, "figures": len(blips)}


def cross_check(source_json_path: str, docx_path: str) -> StructuralCheck:
    src = source_inventory_from_json(source_json_path)
    ren = rendered_inventory(docx_path)
    chk = StructuralCheck(
        source_tables=src["tables"], rendered_tables=ren["tables"],
        source_figures=src["figures"], rendered_figures=ren["figures"],
        source_headings=src["headings"],
    )
    if ren["tables"] < src["tables"]:
        chk.notes.append(
            f"table loss: source has {src['tables']} tables, rendering has {ren['tables']}"
        )
    if ren["figures"] < src["figures"]:
        chk.notes.append(
            f"figure loss: source has {src['figures']} figures, rendering has {ren['figures']}"
        )
    return chk
