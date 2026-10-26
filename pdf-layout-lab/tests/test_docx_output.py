from zipfile import ZipFile
from lxml import etree

import fitz
from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

import app as web
from engine.docx_output import create_docx
from engine.docx_output import _text_box, _original_lines, _ocr_font
from test_lab import make_pdf


def test_pdf_nul_and_xml_controls_do_not_break_editable_text():
    line = {'bbox': (10, 10, 200, 30), 'spans': [{'text': 'A\x00B\x0b<&>한글😀',
            'size': 12, 'flags': 0, 'font': 'Arial', 'color': 0, 'bbox': (10, 10, 200, 30)}]}
    box = _text_box(line, 1)
    text = ''.join(n.text or '' for n in box.iter(qn('w:t')))
    assert text == 'A�B�<&>한글😀'


def test_optional_docx_failure_preserves_pdf(tmp_path, monkeypatch):
    source = make_pdf(tmp_path / 'output.pdf')
    original = source.read_bytes()
    def fail(*args):
        raise etree.XMLSyntaxError('invalid XML', 0, 1, 1)
    monkeypatch.setattr(web, 'create_layout_docx', fail)
    report = {}
    web.make_layout_output(tmp_path, report)
    assert not report['docx_output']['ready']
    assert source.read_bytes() == original


def test_docx_preview_never_overwrites_canonical_layout_pdf(tmp_path, monkeypatch):
    source = make_pdf(tmp_path / 'output.pdf')
    original = source.read_bytes()
    def preview(docx, output):
        with fitz.open() as pdf:
            pdf.new_page().insert_text((20,40),'Changed Word rendering')
            pdf.save(output)
    monkeypatch.setattr(web,'convert_run_docx',preview)
    report={'regions':[]}
    web.make_layout_output(tmp_path,report)
    assert report['pdf_source']=='layout'
    assert report['docx_output']['preview_status']=='ready'
    assert source.read_bytes()==original
    assert (tmp_path/'docx-preview.pdf').exists()


def test_text_is_native_editable_and_background_retains_artwork(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf')
    output = tmp_path / 'layout.docx'
    report = create_docx(source, output)
    assert report['editable_lines'] == 3
    with ZipFile(output) as archive:
        xml = etree.fromstring(archive.read('word/document.xml'))
        paragraphs = xml.findall('.//' + qn('w:txbxContent'))
        text = ''.join(xml.itertext())
        assert len(paragraphs) == 3
        assert 'Layout preservation' in text
        assert 'Original text stays inside its region.' in text
        assert len([n for n in archive.namelist() if n.startswith('word/media/')]) == 1
        assert len(xml.findall('.//' + qn('wp:anchor'))) == 4
    section = Document(output).sections[0]
    assert section.page_width.pt == 420 and section.page_height.pt == 595


def test_mixed_page_sizes_and_rotated_text_remain_editable(tmp_path):
    source = tmp_path / 'mixed.pdf'
    with fitz.open() as pdf:
        first = pdf.new_page(width=420, height=595)
        first.insert_text((30, 40), 'First page')
        second = pdf.new_page(width=595, height=420)
        second.insert_text((40, 250), 'Rotated label', rotate=90)
        pdf.save(source)
    output = tmp_path / 'layout.docx'
    report = create_docx(source, output)
    assert report['editable_lines'] == 2
    assert report['pages'][1]['retained_lines'] == 0
    assert report['editable_rotated_lines'] == 1
    with ZipFile(output) as archive:
        xml = etree.fromstring(archive.read('word/document.xml'))
        assert 'Rotated label' in ''.join(xml.itertext())
        assert xml.xpath('count(//*[local-name()="bodyPr"][@vert="vert270"])') == 1
    assert [(s.page_width.pt, s.page_height.pt) for s in Document(output).sections] == [(420, 595), (595, 420)]


def test_retained_scanned_original_is_editable_without_changing_pdf(tmp_path):
    from io import BytesIO
    from PIL import Image
    from test_typography import region
    source, output = tmp_path/'source.pdf', tmp_path/'layout.docx'
    with fitz.open() as scan:
        page = scan.new_page(width=420,height=595)
        page.insert_text((40,139),'Old scanned ink',fontsize=12)
        page.draw_line((25,150),(390,150),color=(0,0,0),width=1)
        png = page.get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
    with fitz.open() as pdf:
        page = pdf.new_page(width=420,height=595)
        page.insert_image(page.rect,stream=png)
        page.insert_text((40,60),'Translated native text')
        pdf.save(source)
    original = source.read_bytes()
    r = region('retained','Corrected source',[[38,125,135,143]],[30,120,250,146],'table_cell')
    r['spans'][0]['text'] = 'Wrong OCR draft'
    report = create_docx(source,output,{'pages':[{'regions':[r]}]},
                         {'regions':[{'id':'retained','status':'NEEDS_REVIEW','text':'Unapplied translation'}]})
    assert source.read_bytes() == original
    assert report['original_regions'][0]['editable']
    assert report['editable_original_regions'] == 1
    with ZipFile(output) as archive:
        xml = etree.fromstring(archive.read('word/document.xml'))
        text = ''.join(xml.itertext())
        assert 'Corrected source' in text and 'Translated native text' in text
        assert 'Wrong OCR draft' not in text and 'Unapplied translation' not in text
        background = Image.open(BytesIO(archive.read('word/media/image1.png'))).convert('RGB')
        # Erase original ink, not the rule underneath. Text is present only once.
        assert background.crop((80,254,264,282)).getextrema()[0][0] > 230
        assert background.getpixel((200,300))[0] < 50


def test_unknown_ocr_and_protected_marks_are_reported_as_background(tmp_path):
    from test_typography import region
    source = make_pdf(tmp_path/'source.pdf')
    unknown = region('unknown','[OCR]',[[30,250,80,265]],[29,249,81,266])
    protected = region('protected','☑',[[30,280,40,290]],[29,279,41,291])
    protected['keep_original'] = True
    report = create_docx(source,tmp_path/'layout.docx',{'pages':[{'regions':[unknown,protected]}]},
                         {'regions':[{'id':r['id'],'status':'UNCHANGED'} for r in (unknown,protected)]})
    assert report['editable_original_regions'] == 0
    assert all(not r['editable'] and r['reason'] for r in report['original_regions'])


def test_ocr_does_not_cover_an_already_translated_neighbour(tmp_path):
    from test_typography import region
    source = make_pdf(tmp_path/'source.pdf')
    overlapping = region('old','Old OCR',[[35,40,200,60]],[34,39,201,61])
    report = create_docx(source,tmp_path/'layout.docx',{'pages':[{'regions':[overlapping]}]},
                         {'regions':[{'id':'old','status':'NEEDS_REVIEW'}]})
    assert report['editable_original_regions'] == 0
    assert '겹쳐' in report['original_regions'][0]['reason']


def test_corrected_ocr_font_fits_without_relying_on_word_fittext():
    from test_typography import region
    r = region('name','박찬욱',[[40,100,67.5,120]],[39,99,68.5,121])
    r['font_size'] = 17
    span = _original_lines(r)[0]['spans'][0]
    assert 8 <= span['size'] <= 9
    assert _ocr_font(True).text_length(span['text'],fontsize=span['size']) < 27.5


def test_dotted_original_is_removed_with_its_label_and_value(tmp_path):
    from io import BytesIO
    from PIL import Image
    from test_typography import region
    source = tmp_path/'source.pdf'
    with fitz.open() as scan:
        page = scan.new_page(width=420,height=595)
        page.insert_text((40,139),'Label',fontsize=10)
        page.insert_text((230,139),'20g',fontsize=10)
        page.draw_line((100,136),(220,136),color=(0,0,0),dashes='[1 2] 0')
        image = page.get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
    with fitz.open() as pdf:
        page = pdf.new_page(width=420,height=595)
        page.insert_image(page.rect,stream=image)
        pdf.save(source)
    r = region('row','Label....................20g',[[38,125,75,143],[228,125,250,143]],
               [30,120,260,150],'table_cell')
    result = create_docx(source,tmp_path/'layout.docx',{'pages':[{'regions':[r]}]},
                         {'regions':[{'id':'row','status':'UNCHANGED'}]})
    assert result['editable_original_regions'] == 1
    with ZipFile(tmp_path/'layout.docx') as z:
        background = Image.open(BytesIO(z.read('word/media/image1.png'))).convert('RGB')
        assert background.crop((200,260,440,280)).getextrema()[0][0] > 230


def test_editable_docx_upgrade_forks_and_preserves_pdf_and_manual_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'DATA', tmp_path/'data')
    web.DATA.mkdir()
    client = TestClient(web.app)
    source = make_pdf(tmp_path/'source.pdf')
    item = client.post('/api/documents', files={'file':('source.pdf',source.read_bytes())}).json()
    prefix = f"/api/documents/{item['id']}"
    item = client.post(prefix+'/render',json={}).json()
    old = item['latest_run']
    old_folder = web.DATA/item['id']/'runs'/old
    pdf_bytes, docx_bytes = (old_folder/'output.pdf').read_bytes(),(old_folder/'layout.docx').read_bytes()
    upgraded = client.post(prefix+f'/editable-docx?run={old}')
    assert upgraded.status_code == 200
    new = upgraded.json()['latest_run']
    assert new != old
    assert (web.DATA/item['id']/'runs'/new/'output.pdf').read_bytes() == pdf_bytes
    assert (old_folder/'layout.docx').read_bytes() == docx_bytes
    assert client.post(prefix+f'/editable-docx?run={old}').status_code == 409
    report_path = web.DATA/item['id']/'runs'/new/'report.json'
    report = web.read_json(report_path)
    report['docx_output']['edited'] = True
    web.write_json(report_path,report)
    assert client.post(prefix+f'/editable-docx?run={new}').status_code == 409


def test_lab_render_generates_own_docx_and_editor_requires_jwt(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'DATA', tmp_path / 'data')
    web.DATA.mkdir()
    client = TestClient(web.app)
    source = make_pdf(tmp_path / 'source.pdf')
    item = client.post('/api/documents', files={'file': ('source.pdf', source.read_bytes())}).json()
    prefix = f"/api/documents/{item['id']}"
    generated = client.post(prefix + '/render', json={})
    assert generated.status_code == 200
    item = generated.json()
    run = item['latest_run']
    assert item['report']['docx_output']['ready']
    assert client.get(prefix + f'/download/new-docx?run={run}').content.startswith(b'PK')
    assert client.get(prefix + f'/download/docx?run={run}').status_code == 404
    web.legacy.setup()
    import onlyoffice
    monkeypatch.setattr(onlyoffice, 'JWT_SECRET', '')
    assert client.get(prefix + f'/editor-config?run={run}').status_code == 503
    monkeypatch.setattr(onlyoffice, 'JWT_SECRET', 'test-secret')
    config = client.get(prefix + f'/editor-config?run={run}').json()
    decoded = onlyoffice.verify(config['config']['token'])
    assert decoded['document']['fileType'] == 'docx'
    assert client.get('/api/conversion-input/' + config['session']).content.startswith(b'PK')
    # A save callback must never accept unsigned replacement files.
    assert client.post('/api/editor-callback/' + config['session'], json={'status': 2}).status_code == 403
    # A correctly signed callback persists only this lab run and marks its preview stale.
    from io import BytesIO
    import httpx
    before = client.get(prefix + f'/download/new-docx?run={run}').content
    changed = BytesIO()
    with ZipFile(BytesIO(before)) as original, ZipFile(changed, 'w') as updated:
        for entry in original.infolist():
            data = original.read(entry.filename)
            if entry.filename == 'word/document.xml':
                data = data.replace(b'Layout preservation', b'Edited title')
            updated.writestr(entry, data)

    class SavedFileClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            assert url.startswith(onlyoffice.DS_INTERNAL_URL)
            return httpx.Response(200, content=changed.getvalue(), request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx, 'AsyncClient', SavedFileClient)
    payload = {'key': decoded['document']['key'], 'status': 6, 'url': 'http://documentserver/cache/edited.docx'}
    signed = {**payload, 'token': onlyoffice.sign(payload)}
    assert client.post('/api/editor-callback/' + config['session'], json=signed).json() == {'error': 0}
    assert client.get(prefix + f'/download/new-docx?run={run}').content == changed.getvalue()
    assert client.get(prefix).json()['report']['docx_output']['preview_status'] == 'stale'
    assert (web.DATA / item['id'] / 'runs' / run / 'layout.generated.docx').read_bytes() == before
