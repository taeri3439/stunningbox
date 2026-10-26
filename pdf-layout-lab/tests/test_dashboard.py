import threading
import time

from fastapi.testclient import TestClient

import app as web
from engine.inspect import inspect_pdf
from test_lab import make_pdf
from test_translation import wait_job


def upload(client, tmp_path, name='dashboard.pdf'):
    pdf = make_pdf(tmp_path / name)
    return client.post('/api/documents', files={'file': (name, pdf.read_bytes())}).json()


def setup_lab(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(web.native_api, 'DATA', tmp_path / 'native')
    web.DATA.mkdir()
    return TestClient(web.app)


def test_history_filter_pagination_and_run_scoped_reviews(tmp_path, monkeypatch):
    client = setup_lab(tmp_path, monkeypatch)
    item = upload(client, tmp_path)
    upload(client, tmp_path, 'other.pdf')
    prefix = f"/api/documents/{item['id']}"
    assert client.get('/api/history?limit=1').json()['total'] == 2
    assert len(client.get('/api/history?limit=1&offset=1').json()['items']) == 1
    assert client.get('/api/history?q=DASHBOARD').json()['items'][0]['id'] == item['id']
    assert client.get('/api/history?offset=-1').status_code == 422
    rendered = client.post(prefix + '/render', json={}).json()
    run = rendered['latest_run']
    region = rendered['report']['regions'][0]['id']
    report_before = client.get(prefix + f'/download/report?run={run}').content
    payload = {'run':run, 'region_id':region, 'decision':'needs_fix', 'note':'Review line breaks\nand checks.'}
    assert client.post(prefix + '/reviews', json=payload).status_code == 200
    assert client.get(prefix + f'/reviews?run={run}').json()['items'][region]['note'] == payload['note']
    assert client.get(prefix + f'/download/report?run={run}').content == report_before
    assert client.post(prefix + '/reviews', json={**payload,'region_id':'unknown'}).status_code == 422
    assert client.post(prefix + '/reviews', json={**payload,'decision':'anything'}).status_code == 422
    second = client.post(prefix + '/render', json={}).json()['latest_run']
    assert client.get(prefix + f'/reviews?run={second}').json()['items'] == {}
    assert client.post(prefix + '/reviews', json=payload).status_code == 409
    assert len(client.get(prefix + '/runs').json()['items']) == 2
    assert client.get('/api/history?q=dashboard&status=uploaded').json()['total'] == 0
    assert client.get(prefix + '/reviews?run=..').status_code == 404


def stub_translation(monkeypatch):
    monkeypatch.setattr(web.legacy, 'configuration', lambda:{'configured':True, 'provider':'test', 'model':'test'})
    monkeypatch.setattr(web, 'prepare', lambda source,*a:inspect_pdf(source))
    monkeypatch.setattr(web, 'correct_image_sources', lambda layout,*a,**kw:layout)
    monkeypatch.setattr(web, 'correct_sources', lambda layout,*a,**kw:layout)
    def translate(layout, *args):
        return {'id':'test', 'provider':'test', 'model':'test', 'cache_hit':False, 'unchanged_ids':[],
                'replacements':{r['id']:'Short' for p in layout['pages'] for r in p['regions']}}
    monkeypatch.setattr(web.legacy, 'translate_shared', translate)


def test_new_only_skips_legacy_and_onlyoffice_and_checks_pdf(tmp_path, monkeypatch):
    client = setup_lab(tmp_path, monkeypatch)
    stub_translation(monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError('New-only mode must not call the old pipeline or OnlyOffice')
    monkeypatch.setattr(web.legacy, 'project_output', forbidden)
    monkeypatch.setattr(web.legacy, 'convert_docx', forbidden)
    item = upload(client, tmp_path)
    prefix = f"/api/documents/{item['id']}"
    started = client.post(prefix + '/compare', json={'include_baseline':False}).json()
    completed = wait_job(client, started['id'])
    assert completed['status'] == 'completed', completed
    assert not {'docx','legacy_pdf','new_preview'} & {s['stage'] for s in completed['steps']}
    result = client.get(prefix).json()
    assert result['comparison']['legacy_status'] == 'skipped'
    assert result['baseline'] is None
    assert result['report']['docx_output']['ready']
    assert all(r['pdf_text_found'] for r in result['report']['regions'] if r['status'] in {'FIT','ADJUSTED'})
    assert client.get(prefix + f"/download/new?run={result['latest_run']}").content.startswith(b'%PDF')


def test_cancel_at_translation_boundary_preserves_previous_result(tmp_path, monkeypatch):
    client = setup_lab(tmp_path, monkeypatch)
    stub_translation(monkeypatch)
    item = upload(client, tmp_path)
    prefix = f"/api/documents/{item['id']}"
    original = client.post(prefix + '/render', json={}).json()['latest_run']
    entered, release = threading.Event(), threading.Event()
    def blocked(layout, source, target, cache, progress):
        entered.set()
        assert release.wait(5)
        progress('translate', 1, 1)
        raise AssertionError('Cancellation should interrupt at the progress boundary')
    monkeypatch.setattr(web.legacy, 'translate_shared', blocked)
    started = client.post(prefix + '/compare', json={'include_baseline':False}).json()['id']
    try:
        assert entered.wait(5)
        # Cancellation must also remain responsive while a renderer owns LOCK.
        with web.LOCK:
            response = client.post(f'/api/jobs/{started}/cancel')
        assert response.json()['cancel_requested']
    finally:
        release.set()
    for _ in range(100):
        state = client.get(f'/api/jobs/{started}').json()
        if state['status'] == 'cancelled': break
        time.sleep(.02)
    assert state['status'] == 'cancelled', state
    assert state['steps'][-1]['status'] == 'cancelled'
    assert client.get(prefix).json()['latest_run'] == original
    assert client.get(prefix + f'/download/new?run={original}').status_code == 200
    assert client.post('/api/jobs/missing/cancel').status_code == 404


def test_queued_cancellation_never_starts_analysis(tmp_path, monkeypatch):
    client = setup_lab(tmp_path, monkeypatch)
    stub_translation(monkeypatch)
    class Queue:
        def submit(self, fn, *args): self.work=(fn,args)
    queue=Queue()
    monkeypatch.setattr(web,'EXECUTOR',queue)
    item=upload(client,tmp_path)
    started=client.post(f"/api/documents/{item['id']}/compare",json={}).json()['id']
    client.post(f'/api/jobs/{started}/cancel')
    def forbidden(*args): raise AssertionError('Cancelled queue item must not analyze')
    monkeypatch.setattr(web,'prepare',forbidden)
    queue.work[0](*queue.work[1])
    assert client.get(f'/api/jobs/{started}').json()['status']=='cancelled'
    assert item['id'] not in web.ACTIVE
