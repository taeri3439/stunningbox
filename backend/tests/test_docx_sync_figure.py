# backend/tests/test_docx_sync_figure.py
import copy

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from models import SemanticDocument, FigureBlock, FigureOverlay
from renderers.figure_group import build_figure_group_xml
from pipeline.docx_sync import apply_docx_edits_to_model

_WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WSP_TAG = f"{{{_WPS_NS}}}wsp"
_CNVPR_TAG = f"{{{_WPS_NS}}}cNvPr"
_TXBX_TAG = f"{{{_WPS_NS}}}txbx"


def _find_shape(doc, name):
    """Locate the wps:wsp shape named `name` (e.g. "ov0") inside any figure group."""
    for drawing in doc.element.body.iter(qn("w:drawing")):
        for wsp in drawing.iter(_WSP_TAG):
            cNvPr = wsp.find(_CNVPR_TAG)
            if cNvPr is not None and cNvPr.get("name") == name:
                return wsp
    return None


def _set_txbx_content(wsp, paragraphs_xml: str) -> None:
    """Replace a shape's <w:txbxContent> children with raw `<w:p>...</w:p>` XML
    (caller supplies one or more paragraphs)."""
    content = wsp.find(_TXBX_TAG).find(qn("w:txbxContent"))
    for child in list(content):
        content.remove(child)
    for p_xml in paragraphs_xml:
        content.append(parse_xml(f'<w:p xmlns:w="{_W_NS}">{p_xml}</w:p>'))


def _doc_with_group(tmp_path):
    block = FigureBlock(block_id="F1", base_image_ref="b.png",
        overlays=[FigureOverlay(id="o0", target_text="원래 텍스트", bbox=(10, 10, 120, 20), font_size=16),
                  FigureOverlay(id="o1", target_text="둘째", bbox=(10, 40, 120, 20), font_size=16)])
    from PIL import Image
    Image.new("RGB", (200, 150), "white").save(tmp_path / "b.png")
    d = Document()
    run = d.add_paragraph().add_run()
    host = d.paragraphs[0].add_run()
    pic = host.add_picture(str(tmp_path / "b.png"))
    rid = pic._inline.graphic.graphicData.pic.blipFill.blip.get(qn("r:embed"))
    host._r.getparent().remove(host._r)
    run._r.append(parse_xml(build_figure_group_xml(block, rid, 914400, 685800, 200, 150)))
    path = str(tmp_path / "out.docx")
    d.save(path)
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
        if t.text == "원래 텍스트":
            t.text = "수정된 텍스트"
    d.save(path)
    out = apply_docx_edits_to_model(model, path)
    fig = [b for b in out.blocks if isinstance(b, FigureBlock)][0]
    assert fig.overlays[0].target_text == "수정된 텍스트"
    assert fig.overlays[1].target_text == "둘째"


def test_reads_mc_alternatecontent_wrapped(tmp_path):
    """The OnlyOffice DS re-saves each wps:wsp wrapped in
    <mc:AlternateContent><mc:Choice>...</mc:Choice><mc:Fallback>...</mc:Fallback></mc:AlternateContent>,
    duplicating the same shape (by ov{n} name) into both branches. Sync must still return
    each overlay exactly once, using the mc:Choice text.

    The ov0 copy in mc:Choice and mc:Fallback carry DIFFERENT text (CHOICE_TEXT vs.
    FALLBACK_TEXT) so this test can actually distinguish "Choice wins" (correct) from
    "Fallback wins" (a silent regression) — identical text in both branches can't tell
    the two apart.
    """
    path, model, _ = _doc_with_group(tmp_path)
    d = Document(path)
    for drawing in d.element.body.iter(qn("w:drawing")):
        for wsp in list(drawing.iter(_WSP_TAG)):
            cNvPr = wsp.find(_CNVPR_TAG)
            name = cNvPr.get("name") if cNvPr is not None else None
            parent = wsp.getparent()
            idx = list(parent).index(wsp)
            parent.remove(wsp)
            fallback_wsp = copy.deepcopy(wsp)
            if name == "ov0":
                for t in wsp.iter(qn("w:t")):
                    t.text = "CHOICE_TEXT"
                for t in fallback_wsp.iter(qn("w:t")):
                    t.text = "FALLBACK_TEXT"
            alt = parse_xml(
                f'<mc:AlternateContent xmlns:mc="{_MC_NS}" xmlns:wps="{_WPS_NS}">'
                f'<mc:Choice Requires="wps"/>'
                f'<mc:Fallback/>'
                f'</mc:AlternateContent>'
            )
            choice = alt.find(f"{{{_MC_NS}}}Choice")
            fallback = alt.find(f"{{{_MC_NS}}}Fallback")
            choice.append(wsp)
            fallback.append(fallback_wsp)
            parent.insert(idx, alt)
    d.save(path)

    out = apply_docx_edits_to_model(model, path)
    fig = [b for b in out.blocks if isinstance(b, FigureBlock)][0]
    assert fig.overlays[0].target_text == "CHOICE_TEXT"
    assert fig.overlays[1].target_text == "둘째"


def test_shape_multiple_paragraphs_join_with_space(tmp_path):
    """OnlyOffice serializes an Enter keypress inside a text box as a NEW <w:p> in
    txbxContent (NOT a <w:br/>). Two paragraphs must rejoin with a single space, not
    glue together with no separator at all."""
    path, model, _ = _doc_with_group(tmp_path)
    d = Document(path)
    wsp = _find_shape(d, "ov0")
    _set_txbx_content(wsp, ["<w:r><w:t>첫째</w:t></w:r>", "<w:r><w:t>둘째</w:t></w:r>"])
    d.save(path)

    out = apply_docx_edits_to_model(model, path)
    fig = [b for b in out.blocks if isinstance(b, FigureBlock)][0]
    assert fig.overlays[0].target_text == "첫째 둘째"
    assert fig.overlays[1].target_text == "둘째"  # untouched shape


def test_shape_line_break_within_paragraph_still_joins_with_space(tmp_path):
    """A <w:br/> line break WITHIN a single <w:p> (render_docx's pre-wrap) must still
    rejoin with a space, same as before — the <w:p>-boundary fix must not regress this."""
    path, model, _ = _doc_with_group(tmp_path)
    d = Document(path)
    wsp = _find_shape(d, "ov0")
    _set_txbx_content(wsp, ["<w:r><w:t>위</w:t></w:r><w:r><w:br/></w:r><w:r><w:t>아래</w:t></w:r>"])
    d.save(path)

    out = apply_docx_edits_to_model(model, path)
    fig = [b for b in out.blocks if isinstance(b, FigureBlock)][0]
    assert fig.overlays[0].target_text == "위 아래"


def test_out_of_range_overlay_index_does_not_crash(tmp_path):
    """A docx group can carry a stray `ov{n}` shape whose index exceeds the model's
    overlay count (e.g. leftover from a previous render, or DS corruption). Applying
    it must not IndexError and must leave the in-range overlays untouched."""
    path, model, block = _doc_with_group(tmp_path)
    assert len(block.overlays) == 2
    d = Document(path)
    ov0 = _find_shape(d, "ov0")
    stray = copy.deepcopy(ov0)
    stray_cNvPr = stray.find(_CNVPR_TAG)
    stray_cNvPr.set("name", "ov5")
    for t in stray.iter(qn("w:t")):
        t.text = "OUT_OF_RANGE"
    ov0.getparent().append(stray)
    d.save(path)

    out = apply_docx_edits_to_model(model, path)  # must not raise IndexError
    fig = [b for b in out.blocks if isinstance(b, FigureBlock)][0]
    assert len(fig.overlays) == 2
    assert [o.target_text for o in fig.overlays] == ["원래 텍스트", "둘째"]
