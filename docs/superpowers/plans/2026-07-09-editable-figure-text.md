# Editable In-Figure Text (OOXML Group Render) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render overlay figures as an inline OOXML group (redacted base PNG + one editable text box per overlay) so in-figure text is editable in the OnlyOffice editor and round-trips on save, while preserving the figure layout to ≥99% (validated on-screen).

**Architecture:** A new pure-function module (`renderers/figure_group.py`) emits the group `w:drawing` XML using the SAME `figure_overlay.fit_text` pre-fit the composite uses; `docx_renderer._add_figure` calls it when overlays exist, falling back to the composite PNG on any error; `docx_sync` gains a pass that reads figure text boxes back into `overlays[oi].target_text`. Font is unified on a bundled NanumGothic so the PIL measurer, the docx `w:rFonts`, and the DS renderer share one font file.

**Tech Stack:** python-docx, PyMuPDF (fitz), PIL/Pillow, raw OOXML (WordprocessingGroup / wps text boxes), pytest.

## Global Constraints

- RENDER_DPI = `pipeline.extractor.RENDER_DPI` (150.0). PX→PT = 72/RENDER_DPI = 0.48. EMU/inch = 914400.
- Font family string in `w:rFonts` MUST be exactly `NanumGothic` (matches the DS core-font AND the bundled TTF used for measurement).
- Declared half-point size = `floor(fitted_px * 72/RENDER_DPI * 2)` (floor, never round).
- Line rule: `<w:spacing w:before="0" w:after="0" w:line="{twips}" w:lineRule="exact"/>` where `twips = max(20, round((fitted_px+2) * 72/RENDER_DPI * 20))`.
- Text box body: `wrap="none"`, `anchor="ctr"`, `lIns=tIns=rIns=bIns=0`, `<a:noAutofit/>`.
- Rotated overlay (`rotation ∈ {90,-90}`): swap footprint `(w,h)→(h,w)` about bbox center; `<a:xfrm rot="{((-rotation)%360)*60000}">`. fit_text is called with swapped max box `(h, w)`.
- Group `wp:docPr name` = `fig{block_id}`; each shape `wps:cNvPr name` = `ov{oi}` (oi = overlay index).
- Never touch the composite path (`figure_overlay.composite_document`) — it stays as fallback + non-overlay behavior.
- Do NOT stage `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, `backend/_*.py`, `test-pdfs/`, `backend/storage/`, or root images.
- Bbox convention: `FigureOverlay.bbox` = `(x, y, w, h)` in figure-LOCAL pixels at RENDER_DPI, origin = figure top-left (per models.py docstring).

---

### Task 1: Bundle NanumGothic + unify font resolution

**Files:**
- Create: `backend/assets/fonts/NanumGothic.ttf` (copy from `C:/Users/danie/AppData/Local/Temp/claude/.../scratchpad/NanumGothic.ttf`, already pulled from the DS container; SIL OFL 1.1)
- Create: `backend/assets/fonts/NanumGothic.LICENSE.txt` (the OFL text)
- Modify: `backend/pipeline/figure_overlay.py` (`_FONT_CANDIDATES`, add `bundled_cjk_font()` helper)
- Test: `backend/tests/test_figure_font.py`

**Interfaces:**
- Produces: `figure_overlay.bundled_cjk_font() -> str | None` — absolute path to the bundled NanumGothic.ttf if present, else None. `figure_overlay.FONT_FAMILY = "NanumGothic"` module constant. `resolve_font` prefers the bundled Nanum first.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_figure_font.py
import os
from pipeline import figure_overlay

def test_bundled_cjk_font_is_nanum():
    p = figure_overlay.bundled_cjk_font()
    assert p is not None and os.path.exists(p)
    assert os.path.basename(p) == "NanumGothic.ttf"

def test_font_family_constant():
    assert figure_overlay.FONT_FAMILY == "NanumGothic"

def test_resolve_font_prefers_bundled_nanum():
    assert figure_overlay.resolve_font("ko") == figure_overlay.bundled_cjk_font()
```

- [ ] **Step 2: Run to verify it fails** — `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_figure_font.py -v` → FAIL (no `bundled_cjk_font`).

- [ ] **Step 3: Copy the font + license into `backend/assets/fonts/`** (create the dir). The TTF is at the scratchpad path above; the OFL text is in the DS container at `/var/www/onlyoffice/documentserver/core-fonts/nanum/LICENSE.txt`.

- [ ] **Step 4: Implement in `figure_overlay.py`** — add near `_FONTS_DIR`:
```python
FONT_FAMILY = "NanumGothic"  # the family the DS renders AND we measure with

def bundled_cjk_font() -> str | None:
    """Absolute path to the bundled NanumGothic.ttf (the family the DS renders with),
    or None if it isn't present. Used by both the PIL measurer and the group renderer
    so measurement and the docx w:rFonts family agree — zero substitution surprise."""
    p = os.path.join(_FONTS_DIR, "NanumGothic.ttf")
    return p if os.path.exists(p) else None
```
Prepend the bundled path to `_FONT_CANDIDATES` (before NotoSansKR) so `resolve_font` returns it first when present. Keep the rest of the list as-is (fallbacks for envs without the bundle).

- [ ] **Step 5: Run tests** — `pytest tests/test_figure_font.py -v` → PASS. Also `pytest tests/test_figure_overlay.py -q` stays green (composite still resolves a font).

- [ ] **Step 6: Commit** — `git add backend/assets/fonts/NanumGothic.ttf backend/assets/fonts/NanumGothic.LICENSE.txt backend/pipeline/figure_overlay.py backend/tests/test_figure_font.py` then commit `feat(figure): bundle NanumGothic (OFL) and unify font resolution on it`.

---

### Task 2: Group-figure XML builder (`renderers/figure_group.py`)

**Files:**
- Create: `backend/renderers/figure_group.py`
- Test: `backend/tests/test_figure_group_xml.py`

**Interfaces:**
- Consumes: `figure_overlay.fit_text`, `figure_overlay.bundled_cjk_font`, `figure_overlay.FONT_FAMILY`; `models.FigureBlock`/`FigureOverlay`; `pipeline.extractor.RENDER_DPI`.
- Produces:
  `build_figure_group_xml(block: FigureBlock, base_rid: str, emu_w: int, emu_h: int, px_w: int, px_h: int) -> str`
  — returns the full `<w:drawing>…</w:drawing>` string for an inline group. `scale = emu_w / px_w` (EMU per figure-local px). Uses `bundled_cjk_font()` (fallback `resolve_font("ko")`) as the measure font.
  Also `_BULLET_MAP = {"": "•", "": "•"}` applied to overlay text before measuring.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_figure_group_xml.py
import re
from lxml import etree
from models import FigureBlock, FigureOverlay
from renderers.figure_group import build_figure_group_xml

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

def _block():
    return FigureBlock(
        block_id="B1", base_image_ref="b.png",
        overlays=[
            FigureOverlay(id="o0", target_text="주요 기업", bbox=(10, 10, 120, 20), font_size=18, align="center"),
            FigureOverlay(id="o1", target_text="수요 측면", bbox=(2, 40, 16, 80), font_size=16, rotation=90),
        ],
    )

def _parse(block):
    xml = build_figure_group_xml(block, "rId5", emu_w=914400, emu_h=685800, px_w=200, px_h=150)
    return etree.fromstring(xml.encode("utf-8")), xml

def test_group_names_key_by_block_and_overlay_index():
    root, _ = _parse(_block())
    assert root.find(".//wp:docPr", NS).get("name") == "figB1"
    shapes = root.findall(".//wps:wsp", NS)
    assert [s.find(".//wps:cNvPr", NS).get("name") for s in shapes] == ["ov0", "ov1"]

def test_base_picture_uses_rid_and_full_extent():
    root, _ = _parse(_block())
    blip = root.find(".//pic:blipFill/a:blip", NS)
    assert blip.get("{%s}embed" % NS["r"]) == "rId5"
    ext = root.find(".//pic:spPr/a:xfrm/a:ext", NS)
    assert ext.get("cx") == "914400" and ext.get("cy") == "685800"

def test_exact_line_rule_and_no_wrap():
    root, _ = _parse(_block())
    sp = root.find(".//wps:wsp//w:p/w:pPr/w:spacing", NS)
    assert sp.get("{%s}lineRule" % NS["w"]) == "exact"
    body = root.find(".//wps:wsp/wps:bodyPr", NS)
    assert body.get("wrap") == "none" and body.get("anchor") == "ctr"

def test_declared_size_is_floor_quantized():
    # font_size 18px @150dpi → 18*0.48=8.64pt → floor half-points = floor(17.28)=17
    root, _ = _parse(_block())
    sz = root.find(".//wps:wsp[1]//w:rPr/w:sz", NS)
    assert int(sz.get("{%s}val" % NS["w"])) <= round(18 * 72/150 * 2)  # never exceeds rounded

def test_rotated_shape_swaps_footprint_and_sets_rot():
    root, _ = _parse(_block())
    xfrm = root.findall(".//wps:wsp", NS)[1].find(".//a:xfrm", NS)
    assert xfrm.get("rot") == str(((-90) % 360) * 60000)  # 270*60000
    # footprint swapped: bbox (2,40,16,80) → ext (h,w)=(80,16) scaled by 914400/200=4572
    ext = xfrm.find("a:ext", NS)
    assert int(ext.get("cx")) == int(80 * 4572) and int(ext.get("cy")) == int(16 * 4572)

def test_bullet_pua_mapped():
    b = FigureBlock(block_id="B", base_image_ref="b.png",
                    overlays=[FigureOverlay(id="o", target_text="", bbox=(0,0,50,20), font_size=14)])
    _, xml = build_figure_group_xml(b, "rId1", 914400, 685800, 200, 150), None
    assert "" not in _  # replaced by bullet
```
(Fix the last test to call build once; the harness detail is the implementer's — the asserted behavior is: `` never appears in the output, replaced by `•`.)

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_figure_group_xml.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `renderers/figure_group.py`.** Port the validated spike v4 logic. Structure:
```python
"""Emit an overlay figure as an inline OOXML group: a base picture + one editable
text box per overlay, pre-fitted so no renderer re-wraps. See
docs/superpowers/specs/2026-07-09-editable-figure-text-design.md for why each
discipline (pre-fit, font unification, floor-quantize, center-rotation) is required."""
from PIL import ImageDraw, Image
from pipeline.extractor import RENDER_DPI
from pipeline.figure_overlay import fit_text, bundled_cjk_font, resolve_font, FONT_FAMILY

PX_TO_PT = 72.0 / RENDER_DPI
_BULLET_MAP = {"": "•", "": "•"}
_NSDECL = ('xmlns:wp="..." xmlns:a="..." xmlns:pic="..." xmlns:wpg="..." '
           'xmlns:wps="..." xmlns:r="..." xmlns:w="..."')  # full URIs per Global Constraints

def _esc(s): ...  # &, <, >, "
def _norm(text):
    t = " ".join((text or "").split())
    for k, v in _BULLET_MAP.items(): t = t.replace(k, v)
    return t

def _textbox_xml(oi, ov, scale, font_path, draw): ...  # the four disciplines
def build_figure_group_xml(block, base_rid, emu_w, emu_h, px_w, px_h): ...
```
Use the exact `_textbox_xml` math from `scratchpad/spike_editable_figure_v4.py` (font-path = `bundled_cjk_font() or resolve_font("ko")`, floor half-points, exact line rule twips, rotated footprint swap + rot attr, `<w:br/>` between fit_text lines, jc per align, `w:rFonts` all = `FONT_FAMILY`). Skip an overlay whose normalized text is empty. Group wrapper exactly as the spike.

- [ ] **Step 4: Run tests** — `pytest tests/test_figure_group_xml.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add backend/renderers/figure_group.py backend/tests/test_figure_group_xml.py` then `feat(figure): OOXML group-figure XML builder with pre-fit + rotation`.

---

### Task 3: Wire the group into the renderer with composite fallback

**Files:**
- Modify: `backend/renderers/docx_renderer.py` (`_add_figure`, ~line 881; import `figure_group`, `Emu`)
- Test: `backend/tests/test_figure_group_render.py`

**Interfaces:**
- Consumes: `figure_group.build_figure_group_xml`.
- Produces: `_add_figure` renders the group when `block.overlays and block.base_image_ref` and the base asset is readable; else the current composite `image_ref` embed. Returns the paragraph (never None when it renders something). New helper `_embed_figure_group(d, block, asset_dir, content_width) -> paragraph | None` (returns None to signal fallback).

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_figure_group_render.py
import os
from docx import Document
from docx.oxml.ns import qn
from models import FigureBlock, FigureOverlay
from renderers.docx_renderer import _add_figure
from PIL import Image

def _fig(tmp_path, overlays=True):
    base = tmp_path / "b.png"; Image.new("RGB", (300, 200), "white").save(base)
    return FigureBlock(
        block_id="F1", image_ref="b.png", base_image_ref="b.png" if overlays else "",
        width=216.0, height=144.0,
        overlays=[FigureOverlay(id="o0", target_text="테스트 라벨", bbox=(20,20,120,24), font_size=16)] if overlays else [],
    )

def test_overlay_figure_renders_group_with_textbox(tmp_path):
    d = Document()
    _add_figure(d, _fig(tmp_path), str(tmp_path), d.sections[0].page_width)
    xml = d.element.body.xml
    assert "wordprocessingGroup" in xml and "테스트 라벨" in xml
    assert 'name="figF1"' in xml

def test_missing_base_falls_back_to_composite(tmp_path):
    fig = _fig(tmp_path); fig.base_image_ref = "nonexistent.png"
    d = Document()
    p = _add_figure(d, fig, str(tmp_path), d.sections[0].page_width)
    xml = d.element.body.xml
    assert "wordprocessingGroup" not in xml     # fell back
    assert p is not None and xml.count("blip") == 1  # composite image embedded

def test_opaque_figure_unchanged(tmp_path):
    d = Document()
    _add_figure(d, _fig(tmp_path, overlays=False), str(tmp_path), d.sections[0].page_width)
    assert "wordprocessingGroup" not in d.element.body.xml
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_figure_group_render.py -v` → FAIL.

- [ ] **Step 3: Implement.** Add `_embed_figure_group` and branch at the top of `_add_figure`:
```python
def _embed_figure_group(d, block, asset_dir, content_width):
    """Try to render `block` as an editable OOXML group (base picture + text boxes).
    Returns the paragraph, or None to signal the caller to fall back to the composite."""
    base_path = os.path.join(asset_dir, block.base_image_ref)
    if not (block.overlays and block.base_image_ref and os.path.exists(base_path)):
        return None
    try:
        with Image.open(base_path) as im:
            px_w, px_h = im.size
        w_emu = int(block.width / 72.0 * EMU_PER_IN) if block.width else int(px_w / RENDER_DPI * EMU_PER_IN)
        h_emu = int(w_emu * (px_h / px_w))
        cw_emu = int(content_width)  # Emu
        if w_emu > cw_emu:
            h_emu = int(h_emu * (cw_emu / w_emu)); w_emu = cw_emu
        p = d.add_paragraph()
        run = p.add_run(); host = p.add_run()
        pic = host.add_picture(base_path, width=Emu(w_emu))
        rid = pic._inline.graphic.graphicData.pic.blipFill.blip.get(qn("r:embed"))
        host._r.getparent().remove(host._r)
        from renderers.figure_group import build_figure_group_xml
        xml = build_figure_group_xml(block, rid, w_emu, h_emu, px_w, px_h)
        run._r.append(parse_xml(xml))
        p.alignment = _ALIGN.get(block.align, WD_ALIGN_PARAGRAPH.LEFT)
        return p
    except Exception:  # noqa: BLE001 — any failure → caller falls back to composite
        return None
```
Add `EMU_PER_IN = 914400` near the top; import `parse_xml` from `docx.oxml`. At the start of `_add_figure` (after the `image_ref` guard is fine), do:
```python
grouped = _embed_figure_group(d, block, asset_dir, content_width)
if grouped is not None:
    return grouped
```
Leave the rest of `_add_figure` (the composite embed) exactly as-is as the fallback.

- [ ] **Step 4: Run tests** — `pytest tests/test_figure_group_render.py tests/test_page_rhythm_render.py -v` → PASS (page-rhythm render tests confirm the figure paragraph still carries page-parity marks).

- [ ] **Step 5: Commit** — `git add backend/renderers/docx_renderer.py backend/tests/test_figure_group_render.py` then `feat(figure): render editable group in the docx with composite fallback`.

---

### Task 4: Read figure text boxes back on save (`docx_sync`)

**Files:**
- Modify: `backend/pipeline/docx_sync.py` (new `_read_figure_overlays`, call in `apply_docx_edits_to_model`)
- Test: `backend/tests/test_docx_sync_figure.py`

**Interfaces:**
- Consumes: the docx group emitted in Task 2/3 (`wp:docPr name="fig{id}"`, shapes `ov{oi}`), possibly wrapped in `mc:AlternateContent` by the DS.
- Produces: `_read_figure_overlays(docx_path) -> dict[str, dict[int, str]]` — `{block_id: {overlay_index: text}}`. `apply_docx_edits_to_model` applies it to matching `FigureBlock.overlays[oi].target_text`.

- [ ] **Step 1: Write the failing test** (build a docx via the Task-2 builder, edit one text box's `w:t`, assert it syncs back; and a round-trip identity test; and an `mc:AlternateContent`-wrapped variant):
```python
# backend/tests/test_docx_sync_figure.py
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from models import SemanticDocument, FigureBlock, FigureOverlay
from renderers.figure_group import build_figure_group_xml
from pipeline.docx_sync import apply_docx_edits_to_model

def _doc_with_group(tmp_path):
    block = FigureBlock(block_id="F1", base_image_ref="b.png",
        overlays=[FigureOverlay(id="o0", target_text="원래 텍스트", bbox=(10,10,120,20), font_size=16),
                  FigureOverlay(id="o1", target_text="둘째", bbox=(10,40,120,20), font_size=16)])
    from PIL import Image; Image.new("RGB",(200,150),"white").save(tmp_path/"b.png")
    d = Document(); run = d.add_paragraph().add_run()
    host = d.paragraphs[0].add_run(); pic = host.add_picture(str(tmp_path/"b.png"))
    rid = pic._inline.graphic.graphicData.pic.blipFill.blip.get(qn("r:embed"))
    host._r.getparent().remove(host._r)
    run._r.append(parse_xml(build_figure_group_xml(block, rid, 914400, 685800, 200, 150)))
    path = str(tmp_path/"out.docx"); d.save(path)
    model = SemanticDocument(blocks=[block], target_lang="ko")
    return path, model, block

def test_unedited_roundtrip_identity(tmp_path):
    path, model, _ = _doc_with_group(tmp_path)
    out = apply_docx_edits_to_model(model, path)
    fig = [b for b in out.blocks if isinstance(b, FigureBlock)][0]
    assert [o.target_text for o in fig.overlays] == ["원래 텍스트", "둘째"]

def test_edited_textbox_syncs_back(tmp_path):
    path, model, _ = _doc_with_group(tmp_path)
    d = Document(path)
    for t in d.element.iter(qn("w:t")):
        if t.text == "원래 텍스트": t.text = "수정된 텍스트"
    d.save(path)
    out = apply_docx_edits_to_model(model, path)
    fig = [b for b in out.blocks if isinstance(b, FigureBlock)][0]
    assert fig.overlays[0].target_text == "수정된 텍스트"
    assert fig.overlays[1].target_text == "둘째"
```
Plus `test_reads_mc_alternatecontent_wrapped`: wrap each `wps:wsp` in `<mc:AlternateContent><mc:Choice><wps:wsp/></mc:Choice><mc:Fallback/></mc:AlternateContent>` and assert the same sync result.

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_docx_sync_figure.py -v` → FAIL.

- [ ] **Step 3: Implement `_read_figure_overlays`.** Iterate `d.element.body.iter(qn("w:drawing"))`; within each, find `wp:docPr` with `name` starting `fig`; the block_id is `name[3:]`. Collect child shapes by `wps:cNvPr name` == `ov{n}` (search under `mc:Choice` first, then bare `wps:wsp`; dedupe by name so the AlternateContent Choice+Fallback doesn't double-count). For each shape read `txbxContent`, join `w:t` split on `<w:br/>` back into one space-joined string (mirror `_paragraph_lines` but rejoin, since an overlay is a single string). Return `{block_id: {int(n): text}}`. In `apply_docx_edits_to_model`, after `out.blocks` is rebuilt, apply: for each `FigureBlock` in `out.blocks`, if its id in the map, set `overlays[oi].target_text` for each present oi (guard index range).

- [ ] **Step 4: Run tests** — `pytest tests/test_docx_sync_figure.py tests/test_docx_sync_apply.py -v` → PASS (existing sync identity stays green).

- [ ] **Step 5: Commit** — `git add backend/pipeline/docx_sync.py backend/tests/test_docx_sync_figure.py` then `feat(figure): sync edited in-figure text boxes back into the model on save`.

---

### Task 5: Pipeline wiring + re-translation round-trip

**Files:**
- Modify: `backend/pipeline/process.py` if needed (verify `base_image_ref` survives into the rendered model; `composite_document` still runs for `image_ref` fallback)
- Test: `backend/tests/test_figure_editable_integration.py`

**Interfaces:**
- Consumes: everything above; `process.get_translated_model` render path.
- Produces: an integration test proving an overlay figure renders a group in the exported docx and a re-translated `overlay:{oi}` segment shows up in the group text box after re-render.

- [ ] **Step 1: Write the failing test** — build a tiny `SemanticDocument` with one `FigureBlock` (base png in a tmp asset dir, two overlays), `render_docx` it, reopen, assert the group + both overlay texts are present; then mutate `overlays[0].target_text`, re-render, assert the new text is in the docx and the old is gone.
```python
# backend/tests/test_figure_editable_integration.py
from docx import Document
from models import SemanticDocument, FigureBlock, FigureOverlay
from renderers.docx_renderer import render_docx
from PIL import Image

def test_overlay_edit_reflects_on_rerender(tmp_path):
    Image.new("RGB",(300,200),"white").save(tmp_path/"b.png")
    fig = FigureBlock(block_id="F", image_ref="b.png", base_image_ref="b.png",
                      width=216.0, height=144.0,
                      overlays=[FigureOverlay(id="o0", target_text="처음", bbox=(20,20,120,24), font_size=16)])
    doc = SemanticDocument(blocks=[fig], target_lang="ko")
    out = str(tmp_path/"o.docx"); render_docx(doc, out, str(tmp_path))
    assert "처음" in Document(out).element.body.xml
    fig.overlays[0].target_text = "다시"
    render_docx(doc, out, str(tmp_path))
    xml = Document(out).element.body.xml
    assert "다시" in xml and "처음" not in xml
```

- [ ] **Step 2: Run to verify it fails/passes** — `pytest tests/test_figure_editable_integration.py -v`. (May pass immediately if Tasks 2-3 suffice; if so, this task is a verification + any wiring gap it exposes.)

- [ ] **Step 3: Fix any wiring gap** the test exposes (e.g., ensure `base_image_ref` is not dropped by `composite_document`; it currently only sets `image_ref`, leaving `base_image_ref` — confirm). No change to the composite itself.

- [ ] **Step 4: Run the full backend suite** — `cd backend && ./.venv/Scripts/python.exe -m pytest -q`. Expect the pre-existing 15 auth failures only; everything else green.

- [ ] **Step 5: Commit** — `git add backend/pipeline/process.py backend/tests/test_figure_editable_integration.py` (process.py only if changed) then `feat(figure): editable-figure integration + re-translation round-trip`.

---

## Post-implementation (controller-run, not a task)

- Final whole-branch review (opus) via requesting-code-review; dispatch ONE fix subagent for any Critical/Important findings.
- **REAL-SCREEN 99% verification** (the user's hard gate): render the real vet doc (EN→KO) through the live pipeline; open the OnlyOffice editor + DS-rendered viewer in the browser; for each of the 8 overlay figures, DS-render and pixel-compare vs the composite proxy, require structurally-different pixels (>96/255) < 1%; verify one live edit round-trips. Screenshot every figure. This is orchestrated as a parallel per-figure Workflow (fan-out).

## Self-Review notes
- Spec coverage: Task 1↔font unification, Task 2↔pre-fit/rotation/bullet, Task 3↔render+fallback, Task 4↔sync (incl. mc:AlternateContent), Task 5↔re-translation. 99% gate is post-impl.
- Types consistent: `build_figure_group_xml(block, base_rid, emu_w, emu_h, px_w, px_h)` used identically in Tasks 2/3/4 tests. Names `fig{block_id}`/`ov{oi}` consistent across renderer + sync.
