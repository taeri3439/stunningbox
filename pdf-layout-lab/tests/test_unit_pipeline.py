import copy
import json

import fitz
import pytest
from fastapi.testclient import TestClient

import app as web
from engine import translation_units as units, unit_translation as translation, legacy
from engine.enhanced import analyze_page
from engine.models import RenderRequest
from engine.region_edit import edit_layout, RegionEditRequest
from engine.service import render


def model(tmp_path):
    path=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=500,height=300)
        p.insert_text((40,60),'A long source paragraph starts here',fontsize=12)
        p.insert_text((40,78),'and continues across the second line.',fontsize=12)
        p.insert_text((40,145),'- A separate list item',fontsize=12)
        layout=dict(source_hash='test-source',schema_version=15,pages=[analyze_page(p,'en',use_ocr=False)])
        pdf.save(path)
    layout['region_count']=len(layout['pages'][0]['regions'])
    units.ensure(layout)
    return path,layout


@pytest.fixture
def api(monkeypatch):
    legacy.setup()
    monkeypatch.setattr(legacy,'configuration',lambda:dict(configured=True,provider='test',model='test'))
    calls=[]
    def request(batch,*args):
        calls.append(copy.deepcopy(batch))
        return [dict(id=p['id'],text='Translated '+p['text']) for p in reversed(batch)]
    monkeypatch.setattr(translation,'request_segments',request)
    return calls


def test_unit_provenance_and_geometry_cache_are_independent(tmp_path,api):
    path,layout=model(tmp_path)
    original=copy.deepcopy(layout['source_regions'])
    region=layout['pages'][0]['regions'][0]
    assert len(region['source_region_ids'])==2
    result=legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    assert len(api)==1 and len(api[0])==2
    assert api[0][0]['text']==region['text']
    assert 'bbox' not in api[0][0]
    uid=region['translation_unit_id']
    layout,region,_,_=edit_layout(layout,RegionEditRequest(base_run='test',region_ids=[region['id']],bbox=[35,42,450,105]))
    second=legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    assert len(api)==1 and second['cache_hit']
    assert region['translation_unit_id']==uid
    assert layout['source_regions']==original
    assert layout['placement_spaces'][0]['bbox']==region['bbox'] or next(s for s in layout['placement_spaces'] if s['unit_id']==uid)['bbox']==region['bbox']
    report=render(path,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',replacements=result['replacements'],min_scale=.4,min_font_size=6))
    assert layout['source_regions']==original
    unit=next(u for u in layout['translation_units'] if u['id']==uid)
    assert unit['status']=='translated'
    assert next(r for r in report['regions'] if r['id']==region['id'])['translation_status']=='translated'


def test_response_ids_reorder_duplicate_missing_and_unknown():
    batch=[{'id':'a'},{'id':'b'},{'id':'c'}]
    assert translation.align_response(batch,[{'id':'b','text':'B'},{'id':'a','text':'A'}])=={'a':'A','b':'B'}
    assert translation.align_response(batch,[{'id':'a','text':'A'},{'id':'a','text':'wrong'},{'id':'b','text':'B'}])=={'b':'B'}
    assert translation.align_response(batch,[{'id':'unknown','text':'X'}])=={}


def test_translation_success_and_placement_failure_are_separate(tmp_path,monkeypatch,api):
    path,layout=model(tmp_path)
    monkeypatch.setattr(translation,'request_segments',lambda batch,*args:[{'id':p['id'],'text':'Long translated content. '*150} for p in batch])
    result=legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    report=render(path,tmp_path/'long.pdf',layout,RenderRequest(mode='fit',replacements=result['replacements'],min_scale=1,min_font_size=12))
    assert all(u['status']=='translated' for u in layout['translation_units'])
    assert any(s['status']=='NEEDS_REVIEW' for s in layout['placement_spaces'])
    assert any(r['translation_status']=='translated' and r['status']=='NEEDS_REVIEW' for r in report['regions'])


def test_source_echo_with_removed_wraps_is_unchanged(tmp_path,monkeypatch,api):
    _,layout=model(tmp_path)
    monkeypatch.setattr(translation,'request_segments',lambda batch,*args:[{'id':p['id'],'text':' '.join(p['text'].split())} for p in batch])
    result=legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    assert len(result['unchanged_ids'])==2
    assert all(u['status']=='unchanged' for u in layout['translation_units'])


def test_context_never_uses_other_column_or_table_cell(tmp_path):
    with fitz.open() as pdf:
        p=pdf.new_page(width=600,height=200)
        p.insert_text((40,50),'Left paragraph ends.',fontsize=11)
        p.insert_text((40,75),'Next left paragraph.',fontsize=11)
        p.insert_text((320,65),'Right column unrelated.',fontsize=11)
        layout=dict(pages=[analyze_page(p,'en',use_ocr=False)])
    units.ensure(layout)
    for u in layout['translation_units']:
        if 'Left' in u['source_text'] or 'left' in u['source_text']:
            assert 'Right' not in u['context_before']+u['context_after']
    source=layout['source_regions'][0]
    layout['source_regions'].remove(source)
    with pytest.raises(ValueError,match='연결이 손상'):units.project(layout)


def test_only_missing_id_retries_and_failure_is_not_placed(tmp_path,monkeypatch,api):
    _,layout=model(tmp_path); calls=[]
    bad=layout['translation_units'][0]['id']
    def request(batch,*args):
        calls.append([p['id'] for p in batch])
        return [dict(id=p['id'],text='Done') for p in batch if not p['id'].startswith(bad)]
    monkeypatch.setattr(translation,'request_segments',request)
    result=legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    assert len(calls)<=7
    assert all(all(id.startswith(bad) for id in batch) for batch in calls[1:])
    assert result['unit_results'][bad]['status']=='failed'
    r=layout['pages'][0]['regions'][0]
    assert result['replacements'][r['id']]==r['text']
    assert result['incomplete_ids']==[r['id']]
    good=next(u for u in layout['translation_units'] if u['id']!=bad)
    assert result['unit_results'][good['id']]['status']=='translated'


def test_context_changes_invalidate_only_affected_translation(tmp_path,api):
    _,layout=model(tmp_path)
    legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    target=layout['translation_units'][0]
    target['context_before']='A relevant preceding sentence.'
    legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    assert len(api)==2 and len(api[1])==1
    assert api[1][0]['context_before']=='A relevant preceding sentence.'


def test_structural_checkboxes_newlines_and_indentation_survive(tmp_path,api):
    _,layout=model(tmp_path)
    target=layout['translation_units'][0]
    target.update(source_text='1) pump\n  detail ☑ pass □ fail',flow=False)
    result=legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    assert result['unit_results'][target['id']]['translated_text']=='1) Translated pump\n  Translated detail ☑ Translated pass □ Translated fail'


def test_split_and_merge_preserve_sources_and_reset_only_changed_units(tmp_path,api):
    path,layout=model(tmp_path)
    original=copy.deepcopy(layout['source_regions'])
    legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    region=layout['pages'][0]['regions'][0]
    unaffected=copy.deepcopy(layout['translation_units'][1])
    parts,_,_=units.split_region(layout,region['id'],region['source_region_ids'][0])
    assert len(parts)==2 and layout['source_regions']==original
    assert next(u for u in layout['translation_units'] if u['id']==unaffected['id'])==unaffected
    assert all(u['status']=='pending' for u in layout['translation_units'] if u['id']!=unaffected['id'])
    layout,merged,_,_=edit_layout(layout,RegionEditRequest(base_run='x',region_ids=[r['id'] for r in parts]))
    assert merged['source_region_ids']==region['source_region_ids']
    assert layout['source_regions']==original
    assert merged['translation_unit_id']==region['translation_unit_id']


def test_unit_edit_endpoint_preserves_history_and_scoped_translation(tmp_path,monkeypatch,api):
    path,layout=model(tmp_path)
    monkeypatch.setattr(web,'DATA',tmp_path/'data');web.DATA.mkdir()
    client=TestClient(web.app)
    item=client.post('/api/documents',files={'file':('source.pdf',path.read_bytes())}).json()
    root=web.DATA/item['id'];(root/'layout.json').write_text(json.dumps(layout),encoding='utf8')
    prefix=f"/api/documents/{item['id']}"
    response=client.post(prefix+'/render',json={'mode':'fit','replacements':{}})
    assert response.status_code==200,response.text
    item=response.json();parent=item['latest_run'];original_pdf=client.get(prefix+f'/download/new?run={parent}').content
    region=item['layout']['pages'][0]['regions'][0]
    request=dict(base_run=parent,action='split',region_ids=[region['id']],split_after=region['source_region_ids'][0])
    response=client.post(prefix+'/units/edit',json=request)
    assert response.status_code==200,response.text
    assert api==[]
    split=response.json();ids=split['report']['unit_edit']['region_ids']
    assert split['layout']['source_regions']==item['layout']['source_regions']
    assert client.post(prefix+'/units/edit',json=request).status_code==409
    merged=client.post(prefix+'/units/edit',json=dict(base_run=split['latest_run'],action='merge',region_ids=ids))
    assert merged.status_code==200,merged.text
    item=merged.json();region_id=item['report']['unit_edit']['region_ids'][0]
    translated=client.post(prefix+'/regions/edit',json=dict(base_run=item['latest_run'],region_ids=[region_id],action='translate_apply',source_lang='en',target_lang='ko'))
    assert translated.status_code==200,translated.text
    assert len(api)==1 and len(api[0])==1
    assert translated.json()['report']['docx_output']['deferred']
    assert client.get(prefix+f'/download/new?run={parent}').content==original_pdf
