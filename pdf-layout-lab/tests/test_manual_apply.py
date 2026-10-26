import fitz
from fastapi.testclient import TestClient

import app as web
from engine.inspect import inspect_pdf
from engine.models import RenderRequest, normalized
from engine.service import render
from test_lab import make_pdf


def test_manual_text_overrides_ocr_review_but_not_overflow(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf')
    layout = inspect_pdf(source)
    region = layout['pages'][0]['regions'][0]
    region['warnings'] = ['이미지 원문을 정확히 읽지 못했습니다.']
    region['keep_original'] = True
    policy = RenderRequest(mode='fit', replacements={region['id']: 'Confirmed title'})
    assert render(source, tmp_path / 'auto.pdf', layout, policy)['regions'][0]['status'] == 'NEEDS_REVIEW'
    policy.manual_regions = [region['id']]
    report = render(source, tmp_path / 'manual.pdf', layout, policy)
    assert report['regions'][0]['manual_applied']
    assert report['regions'][0]['text_check']
    assert report['validation']['warnings']
    with fitz.open(tmp_path / 'manual.pdf') as pdf:
        assert normalized('Confirmed title') in normalized(pdf[0].get_text())
        assert 'A separate paragraph to compare.' in pdf[0].get_text()
    policy.replacements[region['id']] = 'too long ' * 1000
    result = render(source, tmp_path / 'overflow.pdf', layout, policy)['regions'][0]
    assert result['status'] == 'NEEDS_REVIEW'
    assert not result.get('manual_applied')


def test_manual_overlap_is_recorded_and_protected_text_stays_blocked(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf')
    layout = inspect_pdf(source)
    first, other = layout['pages'][0]['regions'][:2]
    first['source_bbox'] = first['bbox']
    other['bbox'] = first['bbox']  # Model geometry overlap, separate native ink.
    policy = RenderRequest(mode='fit', replacements={first['id']: 'Confirmed'})
    assert render(source, tmp_path / 'auto.pdf', layout, policy)['regions'][0]['status'] == 'NEEDS_REVIEW'
    policy.manual_regions = [first['id']]
    result = render(source, tmp_path / 'manual.pdf', layout, policy)['regions'][0]
    assert result['manual_applied']
    assert any(other['id'] in reason for reason in result['overridden_reasons'])
    other['keep_original'] = True
    assert render(source, tmp_path / 'protected.pdf', layout, policy)['regions'][0]['status'] == 'NEEDS_REVIEW'


def test_apply_one_region_preserves_other_text_history_and_rejects_stale_run(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'DATA', tmp_path / 'data')
    web.DATA.mkdir()
    client = TestClient(web.app)
    source = make_pdf(tmp_path / 'source.pdf')
    uploaded = client.post('/api/documents', files={'file': ('source.pdf', source.read_bytes(), 'application/pdf')}).json()
    prefix = f"/api/documents/{uploaded['id']}"
    first, second = uploaded['layout']['pages'][0]['regions'][:2]
    initial = client.post(prefix + '/render', json={'mode': 'fit', 'replacements': {second['id']: 'Saved text'}}).json()
    run = initial['latest_run']
    old_bytes = client.get(prefix + f'/download/new?run={run}').content
    payload = {'apply_region_id': first['id'], 'base_run': run,
               'replacements': {first['id']: 'Confirmed title', second['id']: 'UNSAVED DRAFT'}}
    response = client.post(prefix + '/render', json=payload)
    assert response.status_code == 200, response.text
    current = response.json()
    report = current['report']
    assert report['manual_edit'] == {'region_id': first['id'], 'region_ids': [first['id']], 'parent_run': run}
    assert report['policy']['replacements'][second['id']] == 'Saved text'
    assert not report['policy']['harmonize_repeats']
    result = next(r for r in report['regions'] if r['id'] == first['id'])
    assert result['manual_applied'] and result['pdf_text_found']
    assert client.get(prefix + f'/download/new?run={run}').content == old_bytes
    assert client.post(prefix + '/render', json=payload).status_code == 409
    payload['base_run'] = current['latest_run']
    payload['replacements'][first['id']] = ' '
    assert client.post(prefix + '/render', json=payload).status_code == 422


def test_batch_apply_reuses_other_pages_defers_docx_and_preserves_history(tmp_path, monkeypatch):
    from engine import validate as validation
    monkeypatch.setattr(web, 'DATA', tmp_path / 'data')
    web.DATA.mkdir()
    source = tmp_path / 'multi.pdf'
    with fitz.open() as pdf:
        for number in range(3):
            page = pdf.new_page(width=420, height=595)
            page.insert_text((40,80), f'Heading {number}', fontsize=15)
            page.insert_text((40,150), f'Body {number}', fontsize=12)
        pdf.save(source)
    client = TestClient(web.app)
    uploaded = client.post('/api/documents', files={'file': ('source.pdf', source.read_bytes())}).json()
    prefix = f"/api/documents/{uploaded['id']}"
    regions = [p['regions'] for p in uploaded['layout']['pages']]
    first = client.post(prefix + '/render', json={'mode':'fit', 'replacements':{regions[2][0]['id']:'Previously saved'}}).json()
    previous = first['latest_run']
    before = client.get(prefix + f'/download/new?run={previous}').content
    original_image = validation.image_of
    inspected = []
    def count(page):
        inspected.append(page.number)
        return original_image(page)
    monkeypatch.setattr(validation, 'image_of', count)
    ids = [r['id'] for r in regions[0]]
    payload = {'apply_region_ids':ids, 'base_run':previous,
               'replacements':{ids[0]:'New title', ids[1]:'New', regions[2][0]['id']:'UNSAVED'}}
    response = client.post(prefix + '/render', json=payload)
    assert response.status_code == 200, response.text
    item = response.json()
    report = item['report']
    assert report['render_scope'] == {'incremental':True, 'rendered_pages':[0]}
    assert inspected == [0,0]  # only source/output for the changed page are raster-checked
    assert report['docx_output']['deferred'] and not report['docx_output']['ready']
    assert all(r.get('manual_applied') for r in report['regions'] if r['id'] in ids), report['regions']
    after = client.get(prefix + f"/download/new?run={item['latest_run']}").content
    with fitz.open(stream=before,filetype='pdf') as old, fitz.open(stream=after,filetype='pdf') as new:
        assert 'New title' in new[0].get_text()
        assert '\nNew\n' in new[0].get_text()
        for number in [1,2]:
            assert old[number].get_pixmap().samples == new[number].get_pixmap().samples
            assert old[number].get_text() == new[number].get_text()
    assert client.get(prefix + f'/download/new?run={previous}').content == before
    assert report['policy']['replacements'][regions[2][0]['id']] == 'Previously saved'
    assert client.post(prefix + '/render', json=payload).status_code == 409
    generated = client.post(prefix + f"/editable-docx?run={item['latest_run']}")
    assert generated.status_code == 200, generated.text
    assert generated.json()['report']['docx_output']['ready']
    assert client.get(prefix + f"/download/new?run={generated.json()['latest_run']}").content == after


def test_interactive_pages_use_full_validation_path(tmp_path):
    source = make_pdf(tmp_path / 'source.pdf')
    with fitz.open(source) as pdf:
        pdf[0].insert_link({'kind':fitz.LINK_URI, 'from':fitz.Rect(300,400,380,430), 'uri':'https://example.com'})
        pdf.saveIncr()
    layout = inspect_pdf(source)
    previous_pdf = tmp_path / 'previous.pdf'
    previous = render(source, previous_pdf, layout, RenderRequest(mode='fit'))
    region = layout['pages'][0]['regions'][0]
    result = render(source, tmp_path/'after.pdf', layout,
                    RenderRequest(mode='fit',replacements={region['id']:'Confirmed'},manual_regions=[region['id']]),
                    previous_pdf=previous_pdf, previous_report=previous, changed_pages={0})
    assert not result['render_scope']['incremental']
    with fitz.open(tmp_path/'after.pdf') as pdf:
        assert pdf[0].get_links()[0]['uri'] == 'https://example.com'
