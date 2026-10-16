"""Page-to-slide export for uniform landscape PDFs, with editable text lines."""
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape
from pathlib import Path
from lxml import etree
import fitz
from engine.xml_text import clean

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'


def eligible(path):
    with fitz.open(path) as pdf:
        size = pdf[0].rect
        return (1.3 <= size.width / size.height <= 1.9 and
                all(page.rect == size and not page.rotation and len(page.get_text()) < 3000 for page in pdf))


def create_pptx(path, output):
    if not eligible(path):
        raise ValueError('동일 크기의 가로형 페이지에서만 슬라이드 Export를 지원합니다.')
    emu = lambda x: round(x * 12700)
    def rels(items):
        return f'<Relationships xmlns="{REL}">' + ''.join(f'<Relationship Id="rId{i}" Type="{R}/{kind}" Target="{target}"/>' for i,kind,target in items) + '</Relationships>'
    with fitz.open(path) as pdf, ZipFile(output, 'w', ZIP_DEFLATED) as archive, ZipFile(Path(__file__).parent / 'templates' / 'blank.pptx') as template:
        width, height = pdf[0].rect.width, pdf[0].rect.height
        for entry in template.infolist():
            if entry.filename not in {'[Content_Types].xml','_rels/.rels','ppt/presentation.xml','ppt/_rels/presentation.xml.rels'}:
                archive.writestr(entry, template.read(entry.filename))
        types = template.read('[Content_Types].xml').decode().replace('</Types>', '')
        if 'Extension="png"' not in types:
            types += '<Default Extension="png" ContentType="image/png"/>'
        presentation = etree.fromstring(template.read('ppt/presentation.xml'))
        slide_ids = etree.Element('{' + P + '}sldIdLst')
        presentation.insert(1, slide_ids)
        size = presentation.find('{' + P + '}sldSz')
        size.set('cx',str(emu(width))); size.set('cy',str(emu(height)))
        relationships = etree.fromstring(template.read('ppt/_rels/presentation.xml.rels'))
        next_id = max(int(r.get('Id')[3:]) for r in relationships) + 1
        for number, page in enumerate(pdf, 1):
            lines = [line for block in page.get_text('dict')['blocks'] if block['type'] == 0 for line in block['lines'] if abs(line['dir'][0]-1)<.01 and abs(line['dir'][1])<.01]
            with fitz.open() as background:
                background.insert_pdf(pdf, from_page=number-1, to_page=number-1)
                art = background[0]
                for line in lines:
                    art.add_redact_annot(fitz.Rect(line['bbox']), fill=False, cross_out=False)
                art.apply_redactions(images=0, graphics=0, text=0)
                archive.writestr(f'ppt/media/image{number}.png', art.get_pixmap(matrix=fitz.Matrix(2,2), alpha=False).tobytes('png'))
            shapes = f'<p:pic><p:nvPicPr><p:cNvPr id="2" name="Page artwork"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{emu(width)}" cy="{emu(height)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
            for index, line in enumerate(lines, 3):
                x,y,x1,y1 = line['bbox']
                runs = ''.join(f'<a:r><a:rPr lang="en-US" sz="{max(100,round(s["size"]*100))}" b="{int(bool(s["flags"] & 16))}" i="{int(bool(s["flags"] & 2))}"><a:solidFill><a:srgbClr val="{s["color"]:06X}"/></a:solidFill></a:rPr><a:t>{escape(clean(s["text"]))}</a:t></a:r>' for s in line['spans'])
                shapes += f'<p:sp><p:nvSpPr><p:cNvPr id="{index}" name="Editable line {index}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(max(1,x1-x+2))}" cy="{emu(max(1,y1-y+2))}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr><p:txBody><a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0"><a:normAutofit/></a:bodyPr><a:lstStyle/><a:p><a:pPr/>{runs}<a:endParaRPr/></a:p></p:txBody></p:sp>'
            archive.writestr(f'ppt/slides/slide{number}.xml', f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
            archive.writestr(f'ppt/slides/_rels/slide{number}.xml.rels', rels([(1,'image',f'../media/image{number}.png'), (2,'slideLayout','../slideLayouts/slideLayout7.xml')]))
            types += f'<Override PartName="/ppt/slides/slide{number}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            relation_id = 'rId' + str(next_id + number)
            etree.SubElement(slide_ids, '{' + P + '}sldId', id=str(255+number), attrib={'{' + R + '}id':relation_id})
            etree.SubElement(relationships, '{' + REL + '}Relationship', Id=relation_id, Type=R+'/slide', Target=f'slides/slide{number}.xml')
        archive.writestr('[Content_Types].xml', types + '</Types>')
        archive.writestr('_rels/.rels', rels([(1,'officeDocument','ppt/presentation.xml')]))
        archive.writestr('ppt/presentation.xml', etree.tostring(presentation, xml_declaration=True, encoding='UTF-8'))
        archive.writestr('ppt/_rels/presentation.xml.rels', etree.tostring(relationships, xml_declaration=True, encoding='UTF-8'))
