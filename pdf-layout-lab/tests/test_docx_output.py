from zipfile import ZipFile
from lxml import etree

import fitz
from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

import app as web
from engine.docx_output import create_docx
from engine.docx_output import _text_box
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


def test_mixed_page_sizes_and_rotated_text_are_reported(tmp_path):
    source = tmp_path / 'mixed.pdf'
    with fitz.open() as pdf:
        first = pdf.new_page(width=420, height=595)
        first.insert_text((30, 40), 'First page')
        second = pdf.new_page(width=595, height=420)
        second.insert_text((40, 250), 'Rotated label', rotate=90)
        pdf.save(source)
    output = tmp_path / 'layout.docx'
    report = create_docx(source, output)
    assert report['editable_lines'] == 1
    assert report['pages'][1]['retained_lines'] == 1
    assert report['warnings']
    assert [(s.page_width.pt, s.page_height.pt) for s in Document(output).sections] == [(420, 595), (595, 420)]


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
