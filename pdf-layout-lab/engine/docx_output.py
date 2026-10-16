"""Lab-only fixed-layout DOCX: preserved artwork plus editable text boxes.

The fitted PDF is an intermediate. The delivered DOCX contains Word text, not
page screenshots containing the translated text. Artwork remains a background;
table borders are visual artwork, not reconstructed Word table cells.
"""
from io import BytesIO
import re

import fitz
from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Pt
from xml.sax.saxutils import escape, quoteattr
from engine.xml_text import clean, INVALID

WPS = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'


def _emu(value):
    return round(value * 12700)


def _anchor(shape, identifier, x, y, width, height, behind=False):
    return parse_xml(f'''<w:drawing {nsdecls('w', 'wp', 'a', 'pic', 'r')} xmlns:wps="{WPS}">
      <wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0"
        relativeHeight="{identifier}" behindDoc="{int(behind)}" locked="0" layoutInCell="1" allowOverlap="1">
        <wp:simplePos x="0" y="0"/>
        <wp:positionH relativeFrom="page"><wp:posOffset>{_emu(x)}</wp:posOffset></wp:positionH>
        <wp:positionV relativeFrom="page"><wp:posOffset>{_emu(y)}</wp:posOffset></wp:positionV>
        <wp:extent cx="{_emu(width)}" cy="{_emu(height)}"/>
        <wp:wrapNone/><wp:docPr id="{identifier}" name="Lab object {identifier}"/>
        <wp:cNvGraphicFramePr/><a:graphic>{shape}</a:graphic>
      </wp:anchor></w:drawing>''')


def _font(span):
    name = re.sub(r'^[A-Z]{6}\+', '', span['font'])
    if any('\uac00' <= c <= '\ud7a3' for c in span['text']):
        return 'Noto Sans CJK KR'
    if any('\u3040' <= c <= '\u9fff' for c in span['text']):
        return 'Noto Sans CJK JP'
    if 'times' in name.lower() or name.lower() in {'tiro', 'tibo', 'tiit', 'tibi'}:
        return 'Times New Roman'
    if 'courier' in name.lower():
        return 'Courier New'
    return 'Arial'


def _text_box(line, identifier):
    x0, y0, x1, y1 = line['bbox']
    width, height = max(1, x1 - x0), max(1, y1 - y0)
    runs = []
    for span_index, span in enumerate(line['spans']):
        text = clean(span['text'])
        if not text:
            continue
        size = max(2, round(span['size'] * 2))
        flags = span['flags']
        style = ('<w:b/>' if flags & 16 else '') + ('<w:i/>' if flags & 2 else '')
        font = quoteattr(_font(span))
        # Fitted PDF spans define the desired physical run width. Word's fitText
        # retains editable characters while compensating for substitute metrics.
        run_width = max(1, round((span['bbox'][2] - span['bbox'][0]) * 20))
        runs.append(f'''<w:r><w:rPr><w:rFonts w:ascii={font} w:hAnsi={font} w:eastAsia={font}/>
          <w:sz w:val="{size}"/><w:szCs w:val="{size}"/><w:color w:val="{span['color']:06X}"/>
          <w:fitText w:val="{run_width}" w:id="{identifier * 1000 + span_index}"/>{style}</w:rPr>
          <w:t xml:space="preserve">{escape(text)}</w:t></w:r>''')
    content = f'''<a:graphicData uri="{WPS}"><wps:wsp><wps:cNvSpPr txBox="1"/>
      <wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{_emu(width)}" cy="{_emu(height)}"/></a:xfrm>
      <a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></wps:spPr>
      <wps:txbx><w:txbxContent><w:p><w:pPr><w:spacing w:before="0" w:after="0"
      w:line="{max(20, round(height * 20))}" w:lineRule="exact"/></w:pPr>{''.join(runs)}</w:p></w:txbxContent></wps:txbx>
      <wps:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" anchor="ctr"><a:noAutofit/></wps:bodyPr>
      </wps:wsp></a:graphicData>'''
    return _anchor(content, identifier, x0, y0, width, height)


def create_docx(fitted_pdf, output):
    """Export page geometry/artwork and editable native text; report raster-only areas."""
    document = Document()
    document.core_properties.title = 'Layout Lab editable output'
    warnings, page_reports = [], []
    identifier = 1
    with fitz.open(fitted_pdf) as source:
        for pno, page in enumerate(source):
            section = document.sections[0] if pno == 0 else document.add_section(WD_SECTION_START.NEW_PAGE)
            section.page_width, section.page_height = Pt(page.rect.width), Pt(page.rect.height)
            section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Pt(0)
            section.header_distance = section.footer_distance = Pt(0)
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = Pt(1)
            paragraph.add_run().font.size = Pt(1)
            lines, retained = [], 0
            for block in page.get_text('dict')['blocks']:
                if block['type'] != 0:
                    continue
                for line in block['lines']:
                    if not any(s['text'].strip() for s in line['spans']):
                        continue
                    if page.rotation or abs(line['dir'][0] - 1) > .01 or abs(line['dir'][1]) > .01:
                        retained += 1
                    else:
                        lines.append(line)
            # Redact only native text; pictures, table rules and decorations stay.
            with fitz.open() as background:
                background.insert_pdf(source, from_page=pno, to_page=pno)
                art = background[0]
                for line in lines:
                    art.add_redact_annot(fitz.Rect(line['bbox']), fill=False, cross_out=False)
                if lines:
                    art.apply_redactions(images=0, graphics=0, text=0)
                png = art.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes('png')
            host = paragraph.add_run()
            picture = host.add_picture(BytesIO(png), width=section.page_width, height=section.page_height)
            graphic = picture._inline.graphic.graphicData.xml
            host._r.remove(host._r.find(qn('w:drawing')))
            host._r.append(_anchor(graphic, identifier, 0, 0, page.rect.width, page.rect.height, behind=True))
            identifier += 1
            for line in lines:
                paragraph.add_run()._r.append(_text_box(line, identifier))
                identifier += 1
            invalid = sum(len(INVALID.findall(s['text'])) for line in lines for s in line['spans'])
            if invalid:
                warnings.append(f'{pno + 1}페이지: PDF의 XML 비호환 문자 {invalid}개를 �로 표시했습니다. 원본과 대조하세요.')
            if retained:
                warnings.append(f'{pno + 1}페이지: 회전 텍스트 {retained}줄은 배경으로 보존되어 직접 편집할 수 없습니다.')
            if not lines:
                warnings.append(f'{pno + 1}페이지: 편집 가능한 텍스트가 없습니다. 이미지/OCR 결과를 확인하세요.')
            page_reports.append({'page': pno, 'editable_lines': len(lines), 'retained_lines': retained})
    document.save(str(output))
    return {'ready': True, 'pages': page_reports, 'editable_lines': sum(p['editable_lines'] for p in page_reports),
            'warnings': warnings, 'preview_status': 'pending',
            'scope': '텍스트는 줄별 Word 텍스트 상자로 편집됩니다. 표 선·그림·배경은 이미지로 보존되며 Word 표 셀은 아닙니다.'}
