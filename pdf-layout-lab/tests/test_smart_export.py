from fastapi.testclient import TestClient

import app as web
from engine.smart_export import options
from test_lab import make_pdf


def test_image_only_output_and_stale_pdf_are_filtered(tmp_path):
    (tmp_path / 'output.pdf').touch()
    (tmp_path / 'layout.docx').touch()
    assert [x['format'] for x in options(tmp_path, {})['options']] == ['pdf']
    report = {'docx_output': {'editable_lines': 2, 'edited': True, 'preview_status': 'stale'}}
    policy = options(tmp_path, report)
    assert policy['default_format'] == 'pdf'
    assert policy['options'][0]['available'] is False
    assert policy['options'][1]['available'] is True


def test_exports_use_actual_result_and_reject_unsupported_format(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'DATA', tmp_path / 'data')
    web.DATA.mkdir()
    client = TestClient(web.app)
    source = make_pdf(tmp_path / 'source.pdf')
    item = client.post('/api/documents', files={'file': ('source.pdf', source.read_bytes())}).json()
    prefix = f"/api/documents/{item['id']}"
    assert item['exports']['options'] == []
    generated = client.post(prefix + '/render', json={}).json()
    run = generated['latest_run']
    assert [x['format'] for x in generated['exports']['options']] == ['pdf', 'docx']
    assert client.get(prefix + f'/export/pdf?run={run}').content.startswith(b'%PDF')
    assert client.get(prefix + f'/export/docx?run={run}').content.startswith(b'PK')
    assert client.get(prefix + f'/export/xlsx?run={run}').status_code == 422
    run_dir = web.DATA / item['id'] / 'runs' / run
    report = web.read_json(run_dir / 'report.json')
    report['docx_output'].update(edited=True, preview_status='stale')
    web.write_json(run_dir / 'report.json', report)
    assert client.get(prefix + f'/export/pdf?run={run}').status_code == 409
