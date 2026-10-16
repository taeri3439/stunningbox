"""Read OnlyOffice-edited block text back out of a rendered .docx, keyed by the
`blk{block_id}` bookmarks that render_docx stamps. Phase 1: text only (heading/paragraph/
list/caption); tables + figures + structural diffs are Phase 2."""
import uuid
from docx import Document
from docx.oxml.ns import qn
from models import (
    SemanticDocument, HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock, FigureBlock,
)
from renderers.docx_renderer import _toc_split

# python-docx's qn() only knows the namespaces it ships with (w, wp, a, r, ...) — wps/mc
# are Word-2010-extension namespaces it never registers, so they're matched by literal
# Clark-notation tag below rather than qn("wps:...")/qn("mc:...") (which would KeyError).
_WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
_WSP_TAG = f"{{{_WPS_NS}}}wsp"
_CNVPR_TAG = f"{{{_WPS_NS}}}cNvPr"


def _paragraph_lines(p_elem) -> list[str]:
    """Text of a w:p, split into lines on <w:br/> line breaks. Mirrors render_docx's
    `_add_text`, which separates preserved `.lines` with WD_BREAK.LINE (<w:br/>)."""
    lines, cur = [], []
    for node in p_elem.iter(qn("w:t"), qn("w:br")):
        if node.tag == qn("w:br"):
            # A <w:br/> with no w:type (or type="textWrapping") is a line break.
            if node.get(qn("w:type")) in (None, "textWrapping"):
                lines.append("".join(cur))
                cur = []
        else:
            cur.append(node.text or "")
    lines.append("".join(cur))
    return lines


def _shape_text(wsp) -> str:
    """Text of a wps:wsp text-box shape's txbxContent, flattened to the ONE string a
    FigureOverlay.target_text is stored as. Both `<w:br/>` line breaks (added by
    render_docx's pre-wrap, mirrors `_paragraph_lines`'s split) AND `<w:p>` paragraph
    boundaries are rejoined with a single space. The `<w:p>` case matters because
    OnlyOffice serializes an Enter keypress INSIDE a text box as a new `<w:p>` (not a
    `<w:br/>`) — without this, `<w:p>Hello</w:p><w:p>World</w:p>` would glue into
    "HelloWorld" with no separator at all. Any resulting run of whitespace is
    collapsed to one space (and the result stripped) so a single unedited `<w:p>` with
    no `<w:br/>` still round-trips byte-identical."""
    para_texts = [" ".join(_paragraph_lines(p)) for p in wsp.iter(qn("w:p"))]
    return " ".join(" ".join(para_texts).split())


def _read_figure_overlays(docx_path: str) -> dict[str, dict[int, str]]:
    """Read edited in-figure text boxes back out of a rendered/edited .docx figure group
    (see `renderers.figure_group.build_figure_group_xml`): `{block_id: {overlay_index: text}}`.

    Each figure group is a `w:drawing` whose `wp:docPr name="fig{block_id}"`; each text-box
    shape inside is a `wps:wsp` whose `wps:cNvPr name="ov{overlay_index}"`.

    The OnlyOffice DS re-saves each `wps:wsp` wrapped in `<mc:AlternateContent><mc:Choice>
    ...</mc:Choice><mc:Fallback>...</mc:Fallback></mc:AlternateContent>`, duplicating the
    SAME shape (by `ov{n}` name) into both branches. `.iter()` walks into both branches
    regardless of the mc: wrapper, so we dedupe by shape name and keep the FIRST occurrence
    in document order — mc:Choice precedes mc:Fallback, so Choice wins automatically.
    """
    d = Document(docx_path)
    result: dict[str, dict[int, str]] = {}
    for drawing in d.element.body.iter(qn("w:drawing")):
        block_id = None
        for docPr in drawing.iter(qn("wp:docPr")):
            name = docPr.get("name") or ""
            if name.startswith("fig") and name[3:]:
                block_id = name[3:]
                break
        if block_id is None:
            continue
        seen: dict[str, str] = {}
        for wsp in drawing.iter(_WSP_TAG):
            cNvPr = wsp.find(_CNVPR_TAG)
            if cNvPr is None:
                continue
            name = cNvPr.get("name") or ""
            if not name.startswith("ov") or name in seen:
                continue  # first occurrence per shape name wins (prefers mc:Choice)
            seen[name] = _shape_text(wsp)
        overlays = {int(name[2:]): text for name, text in seen.items() if name[2:].isdigit()}
        if overlays:
            result[block_id] = overlays
    return result


def read_block_lines(docx_path: str) -> list[tuple[str | None, list[str]]]:
    """Ordered top-level text paragraphs from a rendered/edited .docx.

    (block_id, lines) for a paragraph carrying a `blk{block_id}` bookmark; (None, lines)
    for a text paragraph with NO blk bookmark (a user addition). Empty paragraphs and
    table interiors are skipped. Returned in document order (drives reorder/insert).
    """
    d = Document(docx_path)
    entries: list[tuple[str | None, list[str]]] = []
    for p in d.element.body.iterchildren(qn("w:p")):   # top-level paragraphs only (tables skipped)
        block_id = None
        for bm in p.iter(qn("w:bookmarkStart")):
            name = bm.get(qn("w:name")) or ""
            if name.startswith("blk") and name[3:]:
                block_id = name[3:]
                break
        lines = _paragraph_lines(p)
        if block_id is not None:
            entries.append((block_id, lines))
        elif any(ln.strip() for ln in lines):          # bookmark-less + has text => addition
            entries.append((None, lines))
        # else: empty bookmark-less paragraph => skip
    return entries


# Block kinds whose text Phase 1 syncs (single bookmarked paragraph each).
_TEXT_BLOCKS = (HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock)


def _strip_marker(first_line: str, marker: str | None) -> str:
    """render_docx prepends `"{marker} "` (list) or `"{marker} {text}"` (heading) — strip
    that exact prefix so the marker isn't doubled on the next render."""
    if marker:
        prefix = f"{marker} "
        if first_line.startswith(prefix):
            return first_line[len(prefix):]
    return first_line


def _apply_text(block, lines: list[str]) -> None:
    """Apply docx-extracted lines to a text block in-place.

    Preserves the existing .lines length (so segment counts stay stable), strips
    the rendered marker prefix, and skips TOC entries (round-trip-safe).
    """
    if not lines:
        return
    marker = getattr(block, "marker", None)
    lines = list(lines)
    lines[0] = _strip_marker(lines[0], marker)
    if getattr(block, "lines", None):
        # A wrapped TOC entry (multi-line .lines whose joined text ends in a dot-leader +
        # page number) renders as ONE dot-leader row (render_docx's _toc_split/_render_toc),
        # not as separate preserved lines. Reading that row back would glue the page number
        # onto the title and permanently destroy the leader — even on an unedited save.
        # Mirror the single-line TOC guard below: leave the block unchanged.
        if _toc_split(" ".join(l for l in block.lines if l.strip())) is not None:
            return
        # Always keep .lines at the ORIGINAL length so the block's segment count
        # (N lines → N segments in iter_segments) is unchanged; shifting it would
        # corrupt every downstream source↔translated positional pairing.
        if len(lines) == len(block.lines):
            block.lines = lines
        else:
            n = len(block.lines)
            # Best-effort capture of edited text; Phase 2 does true re-splitting.
            block.lines = (lines + [""] * n)[:n] if len(lines) < n \
                else lines[:n - 1] + ["\n".join(lines[n - 1:])]
    else:
        # Skip TOC entries: the renderer drops the dot-leader and emits "title\tpage",
        # so reading it back would permanently destroy the leader even on an unedited
        # save.  Leave the block unchanged so round-trips are lossless.
        if _toc_split(block.text) is not None:
            return
        block.text = "\n".join(lines) if len(lines) > 1 else lines[0]


def apply_docx_edits_to_model(model: SemanticDocument, docx_path: str) -> SemanticDocument:
    """Rebuild translated.blocks from the docx's surviving bookmark order.

    - Reorder: translated.blocks is rebuilt to match the docx's bookmark order.
    - Drop: text blocks whose block_id is absent from the docx are dropped (deletion
      is now structural — absent id ⇒ not in the rebuilt list; no tombstone flag).
    - Insert: bookmark-less paragraphs with text (user additions) become new first-class
      ParagraphBlock entries with a fresh uuid block_id and source_span_ids=[].
    - Ride-along: non-bookmarked blocks (figures/dividers, table interiors — blocks
      that never had a readable bookmark) are anchored to the preceding surviving
      text block so they aren't lost when text blocks are reordered or dropped.
    - Text update: each surviving text block's content is updated from the docx.
    """
    entries = read_block_lines(docx_path)
    out = model.model_copy(deep=True)
    by_id = {b.block_id: b for b in out.blocks if getattr(b, "block_id", None)}
    edit_ids = {bid for bid, _ in entries if bid is not None}

    # Group non-bookmarked blocks (figures/dividers/table interiors) under the
    # PRECEDING surviving bookmarked block so reorder/drop of text blocks doesn't
    # lose them. DELETED text blocks (block_id not in edit_ids) are dropped entirely —
    # they must NOT ride along.
    trailing: dict[str | None, list] = {}  # anchor_id → [ride-along blocks]
    leading: list = []                      # ride-alongs before the first anchor
    anchor: str | None = None

    for b in out.blocks:
        bid = getattr(b, "block_id", None)
        if isinstance(b, _TEXT_BLOCKS) and bid is not None:
            if bid in edit_ids:
                # This text block survived in the docx — it's an anchor.
                anchor = bid
            # else: a text block absent from edit_ids was deleted → skip entirely
            # (do NOT add to trailing/leading — deleted text blocks are dropped).
        else:
            # Non-bookmarked block (figure/divider/table interior, or a text block
            # with no block_id): ride along with the preceding anchor.
            (trailing.setdefault(anchor, []) if anchor else leading).append(b)

    new_blocks = list(leading)
    for bid, lines in entries:
        if bid is None:
            # User-added paragraph → new first-class block
            text = "\n".join(lines) if len(lines) > 1 else (lines[0] if lines else "")
            nb = ParagraphBlock(
                block_id=uuid.uuid4().hex,
                lines=lines if len(lines) > 1 else None,
                text="" if len(lines) > 1 else text,
                source_span_ids=[],
            )
            new_blocks.append(nb)
            continue
        b = by_id.get(bid)
        if b is None:
            continue
        _apply_text(b, lines)
        new_blocks.append(b)
        new_blocks.extend(trailing.get(bid, []))
    out.blocks = new_blocks

    overlays_by_block = _read_figure_overlays(docx_path)
    for b in out.blocks:
        if isinstance(b, FigureBlock) and b.block_id in overlays_by_block:
            for oi, text in overlays_by_block[b.block_id].items():
                if 0 <= oi < len(b.overlays):
                    b.overlays[oi].target_text = text

    return out
