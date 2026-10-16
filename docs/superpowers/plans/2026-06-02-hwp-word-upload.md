# HWP/Word Upload (DOCX-native ingestion) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept Word (.docx/.doc) and HWP (.hwp/.hwpx) uploads by normalizing them to .docx with LibreOffice and reading their native structure directly into the existing `SemanticDocument`, bypassing the PDF vision pipeline.

**Architecture:** A second ingestion path. Office files are converted to .docx (`convert.py` → LibreOffice headless), then mapped to `SemanticDocument` blocks (`docx_extractor.py` → python-docx). Both the PDF vision path and the new office path converge on `SemanticDocument`, after which translation, rendering, and OnlyOffice editing are shared and unchanged. Routing happens by file extension in `main._run_job`.

**Tech Stack:** Python 3, FastAPI, python-docx (already a dependency, used for rendering), LibreOffice headless (new system dependency), pytest + httpx (new dev/test dependencies).

**Spec:** `docs/superpowers/specs/2026-06-02-hwp-word-upload-design.md`

---

## Task 0: Gating spike — verify HWP→DOCX structure preservation (MANUAL, do this first)

This is a go/no-go gate. If LibreOffice flattens HWP headings/tables/lists, Path B's value collapses for HWP and we fall back to PDF-vision for HWP (or defer it). `.docx`/`.doc` are already structured and are not at risk.

**Files:** none (throwaway).

- [ ] **Step 1: Install LibreOffice locally (Windows)**

Download and install LibreOffice (https://www.libreoffice.org/download/). Note the path to `soffice.exe` (typically `C:\Program Files\LibreOffice\program\soffice.exe`).

- [ ] **Step 2: Convert a real `.hwp` and `.hwpx` sample to .docx**

Run (PowerShell, adjust paths):
```
& "C:\Program Files\LibreOffice\program\soffice.exe" --headless --convert-to docx --outdir "C:\temp\spike" "C:\path\to\sample.hwp"
& "C:\Program Files\LibreOffice\program\soffice.exe" --headless --convert-to docx --outdir "C:\temp\spike" "C:\path\to\sample.hwpx"
```
Expected: `sample.docx` files appear in `C:\temp\spike`.

- [ ] **Step 3: Inspect structure preservation**

Open each produced `.docx` in Word (or run the snippet below) and check: ① headings carry a `Heading N` style (not all `Normal`), ② tables keep their grid/merges, ③ lists keep numbering/levels, ④ images survive.

```python
from docx import Document
d = Document(r"C:\temp\spike\sample.docx")
for p in d.paragraphs[:60]:
    if p.text.strip():
        print(repr(p.style.name), "|", p.text[:60])
print("tables:", len(d.tables), "inline_shapes:", len(d.inline_shapes))
```

- [ ] **Step 4: Record the decision**

Append a short result note to the spec file under a new `## 9. Spike result` heading: which formats preserved structure well enough to proceed with Path B, and whether `.hwpx` is included, fallback (PDF vision), or deferred. Commit:
```
git add docs/superpowers/specs/2026-06-02-hwp-word-upload-design.md
git commit -m "docs: record HWP->DOCX spike result"
```

**Gate:** Only proceed to Task 1+ for the formats the spike approved. If `.hwpx` is deferred, drop it from `_OFFICE_EXTS`/`_CONVERTIBLE`/frontend `accept` in the tasks below.

---

## Task 1: Test scaffold (pytest)

The backend has no tests today. Add pytest so the new modules can be developed test-first.

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/pytest.ini`

- [ ] **Step 1: Add test dependencies**

Append to `backend/requirements.txt`:
```
pytest==8.3.4
httpx==0.28.1
```

- [ ] **Step 2: Create pytest config**

Create `backend/pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
```

- [ ] **Step 3: Create the tests package**

Create empty `backend/tests/__init__.py`.

- [ ] **Step 4: Install and verify pytest runs**

Run (from `backend/`): `pip install -r requirements.txt && python -m pytest -q`
Expected: pytest runs and reports "no tests ran" (exit 5) — no errors.

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/pytest.ini backend/tests/__init__.py
git commit -m "test: add pytest scaffold for backend"
```

---

## Task 2: `convert.py` — Office → DOCX via LibreOffice

**Files:**
- Create: `backend/pipeline/convert.py`
- Test: `backend/tests/test_convert.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_convert.py`:
```python
import os
from pipeline import convert


def test_needs_conversion_by_extension():
    assert convert.needs_conversion("a.hwp") is True
    assert convert.needs_conversion("a.HWPX") is True
    assert convert.needs_conversion("a.doc") is True
    assert convert.needs_conversion("a.docx") is False
    assert convert.needs_conversion("a.pdf") is False


def test_to_docx_is_noop_for_docx(tmp_path):
    src = tmp_path / "already.docx"
    src.write_bytes(b"PK\x03\x04")  # not opened, just must exist
    out = convert.to_docx(str(src), str(tmp_path / "out"))
    assert out == str(src)


def test_to_docx_rejects_unknown_format(tmp_path):
    src = tmp_path / "weird.rtf"
    src.write_bytes(b"x")
    import pytest
    with pytest.raises(ValueError):
        convert.to_docx(str(src), str(tmp_path / "out"))


def test_soffice_cmd_builds_isolated_profile_url():
    cmd = convert.soffice_cmd("/in/a.hwp", "/out", "/tmp/prof", soffice="soffice")
    assert cmd[0] == "soffice"
    assert "--headless" in cmd and "docx" in cmd
    assert "--outdir" in cmd and "/out" in cmd
    assert cmd[-1] == "/in/a.hwp"
    assert any(a.startswith("-env:UserInstallation=file:///tmp/prof") for a in cmd)
```

- [ ] **Step 2: Run to verify it fails**

Run (from `backend/`): `python -m pytest tests/test_convert.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.convert'`.

- [ ] **Step 3: Implement `convert.py`**

Create `backend/pipeline/convert.py`:
```python
"""Office (.doc/.docx/.hwp/.hwpx) -> .docx normalization via LibreOffice headless.

Office uploads are normalized to .docx so docx_extractor can read their native
structure. PDF never passes through here. LibreOffice runs as a one-shot subprocess;
each call gets an isolated user profile so concurrent conversions never share (and
corrupt) one profile.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import uuid

# Formats routed through LibreOffice. .docx is already the target → no-op.
_CONVERTIBLE = {".doc", ".hwp", ".hwpx"}
SOFFICE_BIN = os.environ.get("SOFFICE_BIN", "soffice")


def needs_conversion(src_path: str) -> bool:
    return os.path.splitext(src_path)[1].lower() in _CONVERTIBLE


def _profile_url(profile_dir: str) -> str:
    # file:// + absolute path. Linux "/tmp/p" → file:///tmp/p ;
    # Windows "C:\\p" → file:///C:/p . Both work after slashing + lstrip.
    return "file:///" + profile_dir.replace(os.sep, "/").lstrip("/")


def soffice_cmd(
    src_path: str, out_dir: str, profile_dir: str, soffice: str = SOFFICE_BIN
) -> list[str]:
    """Build the LibreOffice headless argv converting `src_path` to .docx in `out_dir`,
    isolated to `profile_dir` (concurrency-safe)."""
    return [
        soffice,
        "--headless",
        "--convert-to", "docx",
        "--outdir", out_dir,
        f"-env:UserInstallation={_profile_url(profile_dir)}",
        src_path,
    ]


def to_docx(src_path: str, out_dir: str) -> str:
    """Return `src_path` as a .docx path. .docx is returned unchanged; .doc/.hwp/.hwpx
    are converted with LibreOffice. Raises ValueError for unsupported formats and
    RuntimeError if conversion fails."""
    ext = os.path.splitext(src_path)[1].lower()
    if ext == ".docx":
        return src_path
    if ext not in _CONVERTIBLE:
        raise ValueError(f"Unsupported office format: {ext}")

    os.makedirs(out_dir, exist_ok=True)
    profile_dir = os.path.join(tempfile.gettempdir(), f"lo_profile_{uuid.uuid4().hex}")
    cmd = soffice_cmd(src_path, out_dir, profile_dir)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(
            f"LibreOffice conversion failed for {os.path.basename(src_path)} "
            f"(exit {proc.returncode}): {proc.stderr.strip()}"
        )
    out_path = os.path.join(
        out_dir, os.path.splitext(os.path.basename(src_path))[0] + ".docx"
    )
    if not os.path.exists(out_path):
        raise RuntimeError(
            f"LibreOffice reported success but produced no .docx at {out_path}. "
            f"stdout: {proc.stdout.strip()}"
        )
    return out_path
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_convert.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/convert.py backend/tests/test_convert.py
git commit -m "feat: add convert.to_docx (office -> docx via LibreOffice)"
```

- [ ] **Step 6: Manual integration check (requires LibreOffice from Task 0)**

Run a Python REPL from `backend/` with `SOFFICE_BIN` set to the local soffice path:
```python
import os; os.environ["SOFFICE_BIN"]=r"C:\Program Files\LibreOffice\program\soffice.exe"
from pipeline import convert
print(convert.to_docx(r"C:\path\to\sample.hwp", r"C:\temp\convout"))
```
Expected: prints a path to a produced `.docx` that exists. (Not an automated test — LibreOffice is environment-specific.)

---

## Task 3: `docx_extractor.py` — DOCX → SemanticDocument (paragraphs & headings)

Build the extractor incrementally. This task covers body iteration, paragraphs, and headings.

**Files:**
- Create: `backend/pipeline/docx_extractor.py`
- Test: `backend/tests/test_docx_extractor.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_docx_extractor.py`:
```python
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from models import HeadingBlock, ParagraphBlock
from pipeline import docx_extractor


def _save(d, tmp_path, name="in.docx"):
    p = tmp_path / name
    d.save(str(p))
    return str(p)


def test_heading_and_paragraph_mapping(tmp_path):
    d = Document()
    d.add_heading("Title One", level=1)
    d.add_heading("Sub", level=2)
    p = d.add_paragraph("Body text here.")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    path = _save(d, tmp_path)

    doc = docx_extractor.extract_docx(path, str(tmp_path / "assets"))
    kinds = [(type(b).__name__, getattr(b, "text", None)) for b in doc.blocks]
    assert ("HeadingBlock", "Title One") in kinds
    h1 = next(b for b in doc.blocks if isinstance(b, HeadingBlock) and b.text == "Title One")
    assert h1.level == 1
    h2 = next(b for b in doc.blocks if isinstance(b, HeadingBlock) and b.text == "Sub")
    assert h2.level == 2
    body = next(b for b in doc.blocks if isinstance(b, ParagraphBlock))
    assert body.text == "Body text here."
    assert body.align == "center"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_docx_extractor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.docx_extractor'`.

- [ ] **Step 3: Implement the extractor (paragraphs/headings + body iteration)**

Create `backend/pipeline/docx_extractor.py`:
```python
"""DOCX -> SemanticDocument. The office-ingest counterpart to extractor+analyzer+
reconciler: instead of recovering structure from PDF geometry with a vision model, it
reads the structure DOCX already carries (heading styles, lists, tables, images).

PDF-only fields (page, source_span_ids, font geometry) are left at their defaults —
they are Optional in the model and unused by the translator/renderers for this path.
"""
from __future__ import annotations

import os
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.table import Table
from docx.text.paragraph import Paragraph

from models import (
    CaptionBlock,
    HeadingBlock,
    ListItemBlock,
    ParagraphBlock,
    SemanticDocument,
)

_HEADING_RE = re.compile(r"^Heading (\d+)$")
_ALIGN = {WD_ALIGN_PARAGRAPH.CENTER: "center", WD_ALIGN_PARAGRAPH.RIGHT: "right"}


def _align(p: Paragraph):
    return _ALIGN.get(p.alignment)


def _font_size_pt(p: Paragraph):
    for run in p.runs:
        if run.font.size is not None:
            return round(run.font.size.pt, 1)
    return None


def _bold(p: Paragraph):
    flags = [r.bold for r in p.runs if r.text.strip()]
    return bool(flags) and all(flags)


def _heading_level(style_name: str):
    m = _HEADING_RE.match(style_name or "")
    return int(m.group(1)) if m else None


def _paragraph_block(p: Paragraph):
    """Map a paragraph to a single Block, or None for an empty paragraph."""
    text = p.text.strip()
    if not text:
        return None
    style = p.style.name if p.style else ""
    level = _heading_level(style)
    if level is not None:
        return HeadingBlock(
            level=level, text=text, bold=_bold(p), align=_align(p),
            font_size=_font_size_pt(p),
        )
    if (style or "").lower().startswith("caption"):
        return CaptionBlock(text=text, align=_align(p), font_size=_font_size_pt(p))
    return ParagraphBlock(
        text=text, bold=_bold(p), align=_align(p), font_size=_font_size_pt(p),
    )


def extract_docx(docx_path: str, asset_dir: str, source_lang: str = "ko") -> SemanticDocument:
    os.makedirs(asset_dir, exist_ok=True)
    d = Document(docx_path)
    blocks = []
    for item in d.iter_inner_content():
        if isinstance(item, Paragraph):
            blk = _paragraph_block(item)
            if blk is not None:
                blocks.append(blk)
        elif isinstance(item, Table):
            pass  # tables handled in Task 5
    return SemanticDocument(source_lang=source_lang, blocks=blocks)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_docx_extractor.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_extractor.py backend/tests/test_docx_extractor.py
git commit -m "feat: docx_extractor headings + paragraphs"
```

---

## Task 4: `docx_extractor` — list items

**Files:**
- Modify: `backend/pipeline/docx_extractor.py`
- Modify: `backend/tests/test_docx_extractor.py`

- [ ] **Step 1: Add the failing test**

Append to `backend/tests/test_docx_extractor.py`:
```python
from models import ListItemBlock


def test_list_items_get_level(tmp_path):
    d = Document()
    d.add_paragraph("First bullet", style="List Bullet")
    d.add_paragraph("Second bullet", style="List Bullet")
    path = _save(d, tmp_path, "lists.docx")
    doc = docx_extractor.extract_docx(path, str(tmp_path / "assets2"))
    items = [b for b in doc.blocks if isinstance(b, ListItemBlock)]
    assert len(items) == 2
    assert items[0].text == "First bullet"
    assert items[0].level == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_docx_extractor.py::test_list_items_get_level -q`
Expected: FAIL — items are mapped to `ParagraphBlock`, so the list is empty.

- [ ] **Step 3: Implement list detection**

In `backend/pipeline/docx_extractor.py`, add the import and helper, and branch in `_paragraph_block`:
```python
from docx.oxml.ns import qn  # add to imports (alongside other docx imports)
```
Add helper after `_heading_level`:
```python
def _list_level(p: Paragraph):
    """0-based list level if the paragraph is numbered/bulleted, else None."""
    pPr = p._p.pPr
    if pPr is not None and pPr.numPr is not None:
        ilvl = pPr.numPr.ilvl
        return int(ilvl.val) if ilvl is not None and ilvl.val is not None else 0
    if (p.style.name or "").startswith("List"):
        return 0
    return None
```
In `_paragraph_block`, insert this branch **before** the caption/paragraph fallbacks (after the heading branch):
```python
    lvl = _list_level(p)
    if lvl is not None:
        return ListItemBlock(
            level=lvl, text=text, bold=_bold(p), align=_align(p),
            font_size=_font_size_pt(p),
        )
```
And add `ListItemBlock` to the `from models import (...)` block.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_docx_extractor.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_extractor.py backend/tests/test_docx_extractor.py
git commit -m "feat: docx_extractor list items with levels"
```

---

## Task 5: `docx_extractor` — tables (with colspan/rowspan)

**Files:**
- Modify: `backend/pipeline/docx_extractor.py`
- Modify: `backend/tests/test_docx_extractor.py`

- [ ] **Step 1: Add the failing test**

Append to `backend/tests/test_docx_extractor.py`:
```python
from models import TableBlock


def test_table_basic_and_colspan(tmp_path):
    d = Document()
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "A"
    t.cell(0, 1).text = "B"
    t.cell(1, 0).text = "C"
    t.cell(1, 1).text = "D"
    # merge the top row into one cell spanning 2 columns
    t.cell(0, 0).merge(t.cell(0, 1))
    path = _save(d, tmp_path, "table.docx")
    doc = docx_extractor.extract_docx(path, str(tmp_path / "assets3"))
    tables = [b for b in doc.blocks if isinstance(b, TableBlock)]
    assert len(tables) == 1
    rows = tables[0].rows
    assert rows[0][0].colspan == 2
    assert rows[0][0].text == "A"
    assert [c.text for c in rows[1]] == ["C", "D"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_docx_extractor.py::test_table_basic_and_colspan -q`
Expected: FAIL — no `TableBlock` produced (tables are skipped).

- [ ] **Step 3: Implement table mapping**

In `backend/pipeline/docx_extractor.py`, add `TableBlock, TableCell` to the `from models import (...)` block, then add this helper:
```python
def _tc_text(tc) -> str:
    return "".join(n.text or "" for n in tc.findall(".//" + qn("w:t"))).strip()


def _table_block(table: Table) -> TableBlock:
    """Map a python-docx Table to a TableBlock, deriving colspan from w:gridSpan and
    rowspan from w:vMerge (restart starts a span; continue extends the cell above)."""
    rows = []
    grid_open: dict[int, "TableCell"] = {}  # grid column -> cell spanning down
    for tr in table._tbl.tr_lst:
        cells = []
        grid_col = 0
        header_row = tr.find(qn("w:trPr")) is not None and \
            tr.find(qn("w:trPr")).find(qn("w:tblHeader")) is not None
        for tc in tr.tc_lst:
            tcPr = tc.tcPr
            colspan = 1
            vmerge = None
            if tcPr is not None:
                gs = tcPr.find(qn("w:gridSpan"))
                if gs is not None:
                    colspan = int(gs.get(qn("w:val")))
                vm = tcPr.find(qn("w:vMerge"))
                if vm is not None:
                    vmerge = vm.get(qn("w:val")) or "continue"
            if vmerge == "continue":
                origin = grid_open.get(grid_col)
                if origin is not None:
                    origin.rowspan += 1
                grid_col += colspan
                continue
            cell = TableCell(
                text=_tc_text(tc), colspan=colspan, rowspan=1, is_header=header_row
            )
            if vmerge == "restart":
                grid_open[grid_col] = cell
            else:
                grid_open.pop(grid_col, None)
            cells.append(cell)
            grid_col += colspan
        rows.append(cells)
    return TableBlock(rows=rows)
```
Then in `extract_docx`, replace the `pass  # tables handled in Task 5` line with:
```python
            blocks.append(_table_block(item))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_docx_extractor.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_extractor.py backend/tests/test_docx_extractor.py
git commit -m "feat: docx_extractor tables with colspan/rowspan"
```

---

## Task 6: `docx_extractor` — inline images → FigureBlock

**Files:**
- Modify: `backend/pipeline/docx_extractor.py`
- Modify: `backend/tests/test_docx_extractor.py`

- [ ] **Step 1: Add the failing test**

Append to `backend/tests/test_docx_extractor.py` (uses a tiny 1x1 PNG):
```python
import base64, os as _os
from models import FigureBlock

_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_inline_image_becomes_figure(tmp_path):
    img = tmp_path / "pic.png"
    img.write_bytes(_PNG_1x1)
    d = Document()
    d.add_paragraph("Before image.")
    d.add_picture(str(img))
    path = _save(d, tmp_path, "img.docx")
    assets = tmp_path / "assets_img"
    doc = docx_extractor.extract_docx(path, str(assets))
    figs = [b for b in doc.blocks if isinstance(b, FigureBlock)]
    assert len(figs) == 1
    assert figs[0].image_ref
    assert _os.path.exists(_os.path.join(str(assets), figs[0].image_ref))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_docx_extractor.py::test_inline_image_becomes_figure -q`
Expected: FAIL — no `FigureBlock` produced.

- [ ] **Step 3: Implement image extraction**

In `backend/pipeline/docx_extractor.py`, add `FigureBlock` to the `from models import (...)` block, and add a helper that pulls embedded images out of a paragraph:
```python
def _paragraph_images(p: Paragraph, d, asset_dir: str, counter: list) -> list:
    """Extract embedded images in `p` to asset_dir; return FigureBlocks (in order)."""
    figs = []
    for blip in p._p.findall(".//" + qn("a:blip")):
        rid = blip.get(qn("r:embed"))
        if not rid:
            continue
        part = d.part.related_parts.get(rid)
        if part is None:
            continue
        counter[0] += 1
        ext = os.path.splitext(part.partname)[1] or ".png"
        fname = f"docximg{counter[0]}{ext}"
        with open(os.path.join(asset_dir, fname), "wb") as fh:
            fh.write(part.blob)
        figs.append(FigureBlock(image_ref=fname))
    return figs
```
Change `extract_docx`'s paragraph branch so images become their own blocks alongside text. Replace the paragraph-handling branch in the loop with:
```python
        if isinstance(item, Paragraph):
            for fig in _paragraph_images(item, d, asset_dir, img_counter):
                blocks.append(fig)
            blk = _paragraph_block(item)
            if blk is not None:
                blocks.append(blk)
```
And initialize the counter before the loop:
```python
    img_counter = [0]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_docx_extractor.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_extractor.py backend/tests/test_docx_extractor.py
git commit -m "feat: docx_extractor inline images -> FigureBlock"
```

---

## Task 7: `docx_extractor` — running header/footer → RunningBand

**Files:**
- Modify: `backend/pipeline/docx_extractor.py`
- Modify: `backend/tests/test_docx_extractor.py`

- [ ] **Step 1: Add the failing test**

Append to `backend/tests/test_docx_extractor.py`:
```python
def test_header_footer_become_bands(tmp_path):
    d = Document()
    d.sections[0].header.paragraphs[0].text = "My Running Header"
    d.add_paragraph("Body.")
    path = _save(d, tmp_path, "hf.docx")
    doc = docx_extractor.extract_docx(path, str(tmp_path / "assets_hf"))
    assert doc.running_header is not None
    assert "My Running Header" in (doc.running_header.center or "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_docx_extractor.py::test_header_footer_become_bands -q`
Expected: FAIL — `doc.running_header` is None.

- [ ] **Step 3: Implement header/footer mapping**

In `backend/pipeline/docx_extractor.py`, add `RunningBand` to the `from models import (...)` block, add the helper:
```python
def _band(hf):
    """A section header/footer -> RunningBand (all non-empty lines joined into center)."""
    if hf is None:
        return None
    texts = [p.text.strip() for p in hf.paragraphs if p.text.strip()]
    if not texts:
        return None
    return RunningBand(center=" ".join(texts))
```
In `extract_docx`, build bands from the first section and pass them to the result:
```python
    sec = d.sections[0]
    header = _band(sec.header)
    footer = _band(sec.footer)
    return SemanticDocument(
        source_lang=source_lang, running_header=header, running_footer=footer,
        blocks=blocks,
    )
```
(Replace the previous `return SemanticDocument(...)`.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_docx_extractor.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_extractor.py backend/tests/test_docx_extractor.py
git commit -m "feat: docx_extractor header/footer -> RunningBand"
```

---

## Task 8: `process.build_source_model_from_office` — orchestration

**Files:**
- Modify: `backend/pipeline/process.py`
- Test: `backend/tests/test_process_office.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_process_office.py`:
```python
import json, os
from docx import Document
from pipeline import process


def test_office_ingest_writes_source_json(tmp_path):
    # A .docx input means convert.to_docx is a no-op (no LibreOffice needed).
    d = Document()
    d.add_heading("Hello", level=1)
    d.add_paragraph("World")
    storage = str(tmp_path / "storage")
    doc_id = "doc123"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(base, exist_ok=True)
    src = os.path.join(base, "source.docx")
    d.save(src)

    stages = []
    model, report = process.build_source_model_from_office(
        src, doc_id, storage, source_lang="ko",
        on_progress=lambda s, k, t: stages.append(s),
    )
    assert any(b.text == "Hello" for b in model.blocks)
    assert os.path.exists(os.path.join(base, "source.json"))
    saved = json.load(open(os.path.join(base, "source.json"), encoding="utf-8"))
    assert saved["blocks"]
    assert "convert" in stages and "extract" in stages
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_process_office.py -q`
Expected: FAIL — `AttributeError: module 'pipeline.process' has no attribute 'build_source_model_from_office'`.

- [ ] **Step 3: Implement the orchestrator**

In `backend/pipeline/process.py`, add to the imports near the top:
```python
from pipeline import convert, docx_extractor
```
Update the ProgressFn stage comment (line ~22) to include the new stage:
```python
# A progress sink: (stage, done, total). Stages: "convert" | "extract" | "analyze" | "translate".
```
Add the function after `build_source_model` (before `rebuild_source_model`):
```python
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
    _write_json(os.path.join(base, "source.json"), doc.model_dump())
    _write_json(os.path.join(base, "report.json"), report)
    return doc, report
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_process_office.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (all tests from Tasks 2–8 green).

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/process.py backend/tests/test_process_office.py
git commit -m "feat: office ingest orchestrator (convert -> docx_extractor -> source.json)"
```

---

## Task 9: Route uploads by extension in `main.py`

**Files:**
- Modify: `backend/main.py` (`upload` ~110-131, `_run_job` ~88-107)
- Test: `backend/tests/test_upload_validation.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_upload_validation.py`:
```python
from main import _ext_ok


def test_allowed_extensions():
    assert _ext_ok("a.pdf") is True
    assert _ext_ok("a.PDF") is True
    assert _ext_ok("report.docx") is True
    assert _ext_ok("legacy.doc") is True
    assert _ext_ok("hangul.hwp") is True
    assert _ext_ok("hangul.hwpx") is True


def test_rejected_extensions():
    assert _ext_ok("note.txt") is False
    assert _ext_ok("image.png") is False
    assert _ext_ok("") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_upload_validation.py -q`
Expected: FAIL — `ImportError: cannot import name '_ext_ok' from 'main'`.

- [ ] **Step 3: Implement routing**

In `backend/main.py`, add the allow-list helper near the top (after imports):
```python
import os  # (only if not already imported)

_OFFICE_EXTS = (".docx", ".doc", ".hwp", ".hwpx")
_ALLOWED_EXTS = (".pdf",) + _OFFICE_EXTS


def _ext_ok(filename: str) -> bool:
    return os.path.splitext((filename or "").lower())[1] in _ALLOWED_EXTS
```
Replace the body of `upload` (the validation + save) with:
```python
@app.post("/api/documents")
async def upload(
    pdf: UploadFile = File(...),
    source_lang: str = Form("ko"),
    target_lang: str = Form("en"),
):
    filename = pdf.filename or ""
    if not _ext_ok(filename):
        raise HTTPException(
            400, "Supported formats: PDF, Word (.docx/.doc), HWP (.hwp/.hwpx)"
        )
    ext = os.path.splitext(filename.lower())[1]
    doc_id = uuid.uuid4().hex[:12]
    base = process.doc_dir(STORAGE, doc_id)
    os.makedirs(base, exist_ok=True)
    src_path = os.path.join(base, f"source{ext}")
    with open(src_path, "wb") as fh:
        fh.write(await pdf.read())

    jobs.start(doc_id, source_lang, target_lang)
    threading.Thread(
        target=_run_job, args=(doc_id, src_path, source_lang, target_lang), daemon=True
    ).start()
    return {"doc_id": doc_id, "source_lang": source_lang, "target_lang": target_lang}
```
Replace `_run_job` to branch by extension:
```python
def _run_job(doc_id: str, src_path: str, source_lang: str, target_lang: str) -> None:
    """Analyze + translate on a background thread, reporting progress to `jobs`."""
    def report(stage, done, total):
        jobs.progress(doc_id, stage, done, total)

    try:
        ext = os.path.splitext(src_path)[1].lower()
        if ext == ".pdf":
            process.build_source_model(
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
    except Exception as e:  # noqa: BLE001
        jobs.fail(doc_id, f"Processing failed: {e}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_upload_validation.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full suite + import-check the app**

Run: `python -m pytest -q && python -c "import main"`
Expected: all tests pass; `import main` exits cleanly (no syntax/import errors).

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_upload_validation.py
git commit -m "feat: accept office uploads and route by extension"
```

---

## Task 10: Frontend — accept office formats (MANUAL verification)

**Files:**
- Modify: `frontend/src/app/page.tsx` (~116-123)

- [ ] **Step 1: Widen the file input accept + copy**

In `frontend/src/app/page.tsx`, change the `<input>` accept attribute and nearby helper text:
```tsx
<input
  type="file"
  accept="application/pdf,.pdf,.docx,.doc,.hwp,.hwpx"
  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
  className="mono text-[0.8rem] file:mr-3 file:border file:border-rule-strong file:bg-paper file:px-3 file:py-1.5 file:text-[0.72rem]"
/>
```
If there is nearby copy stating "PDF only", update it to "PDF · Word · HWP".

- [ ] **Step 2: (Optional) progress label for the convert stage**

If the progress UI maps stage keys to labels, add `convert` (e.g. "변환 중") and ensure unknown stages degrade gracefully. If stages are shown verbatim, no change needed.

- [ ] **Step 3: Manual verification**

Run the frontend dev server and confirm the file picker now offers .docx/.doc/.hwp/.hwpx and that selecting one uploads without a client-side rejection. (Full end-to-end happens in Task 12.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/page.tsx
git commit -m "feat: frontend accepts Word/HWP uploads"
```

---

## Task 11: Deployment — install LibreOffice in the backend image (MANUAL)

**Files:**
- Create: `backend/nixpacks.toml`

- [ ] **Step 1: Add LibreOffice to the Nixpacks setup phase**

Create `backend/nixpacks.toml`:
```toml
[phases.setup]
aptPkgs = ["libreoffice"]
```
(If the Python provider's auto-detected setup is overridden by this file, instead use `nixPkgs = ["...existing...", "libreoffice"]`; verify against the Railway build log.)

- [ ] **Step 2: Document the local override**

Add a note to `DEPLOY.md` (or `backend/README` if present): install LibreOffice locally and set `SOFFICE_BIN` to `soffice.exe`'s full path on Windows.

- [ ] **Step 3: Verify the deploy build**

Push the branch and confirm the Railway build succeeds and that `soffice --version` runs in the deployed container (Railway shell or a temporary `/api/health`-style probe). Expected: LibreOffice version prints.

- [ ] **Step 4: Commit**

```bash
git add backend/nixpacks.toml DEPLOY.md
git commit -m "build: install LibreOffice in backend image for office conversion"
```

---

## Task 12: End-to-end verification (MANUAL)

**Files:** none.

- [ ] **Step 1: Start the stack**

Start OnlyOffice (`docker compose up -d`), the backend (`uvicorn main:app --port 8000` with `SOFFICE_BIN` set + `ANTHROPIC_API_KEY`), and the frontend dev server.

- [ ] **Step 2: Upload each office format**

Upload a `.docx`, a `.doc`, a `.hwp`, and (if approved in Task 0) a `.hwpx`. For each: confirm the progress UI shows `convert` → `extract`, then translation completes.

- [ ] **Step 3: Confirm editing works**

Open each result in the OnlyOffice editor pane and confirm the translated .docx opens and is editable (headings/tables/lists/images present). This proves the output-editing path is unaffected.

- [ ] **Step 4: Regression — PDF path unchanged**

Upload a PDF and confirm it still flows through the vision pipeline (`extract` → `analyze` stages) and produces an editable result.

- [ ] **Step 5: Concurrency**

Upload two office files within a second of each other; confirm both complete without a LibreOffice profile-lock error.

---

## Self-Review (completed during planning)

- **Spec coverage:** convert.py (§4.1)→Task 2; docx_extractor (§4.2)→Tasks 3-7; routing (§4.3)→Tasks 8-9; frontend (§4.4)→Task 10; deploy (§4.5)→Task 11; spike (§5)→Task 0; verification (§8)→Task 12. All spec sections mapped.
- **Placeholder scan:** no TBD/TODO; LibreOffice- and frontend-dependent steps are explicitly marked MANUAL with concrete commands.
- **Type consistency:** `to_docx`/`soffice_cmd`/`needs_conversion` (Task 2) match their callers in Task 8; `extract_docx(path, asset_dir, source_lang)` signature is consistent across Tasks 3-8; `build_source_model_from_office` signature matches its call in Task 9; `_ext_ok`/`_OFFICE_EXTS` defined in Task 9 and tested there.
- **Known limitations (acceptable for this scope):** office path sets no `page` (no page-break breaks) and no per-page render, so `page_size_mm` returns None and the UI uses default sheet sizing; header/footer collapse all lines into `center`. These match the spec's "fill what we know" stance.
```
