import copy
import fitz
from fastapi.testclient import TestClient
import app as web
from engine import legacy
from engine.inspect import inspect_pdf
from test_lab import make_pdf
from test_translation import CountingTranslator


def test_segment_cache_survives_geometry_and_schema_change(tmp_path,monkeypatch):
    legacy.setup()
    from pipeline import translator
    delegate=CountingTranslator()
    monkeypatch.setattr(legacy,'configuration',lambda:dict(configured=True,provider='test',model='test'))
    monkeypatch.setattr(translator,'get_translator',lambda:delegate)
    layout=inspect_pdf(make_pdf(tmp_path/'source.pdf'))
    legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    layout['schema_version']+=1
    for r in layout['pages'][0]['regions']:
        r['id']+='-edited';r['bbox'][2]+=10
    result=legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    assert delegate.calls==1
    assert result['recovery']['cached_segments']>0
    legacy.translate_shared(layout,'en','ko',tmp_path/'cache',force=True)
    assert delegate.calls==2


def test_invalid_response_recovery_has_one_bounded_budget():
    delegate=CountingTranslator()
    def invalid(*args,**kwargs):
        delegate.calls+=1
        return []
    delegate.translate=invalid
    checked=legacy.CheckedTranslator(delegate)
    source=[f'line {i}' for i in range(40)]
    assert checked.translate(source)==source
    assert delegate.calls<=7
    assert len(checked.review_texts)==40


def test_merge_resize_translate_is_scoped_and_preserves_history(tmp_path,monkeypatch):
    monkeypatch.setattr(web,'DATA',tmp_path/'data');web.DATA.mkdir()
    source=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        for i in range(2):
            p=pdf.new_page(width=420,height=595)
            p.insert_text((40,80),'First heading',fontsize=15)
            p.insert_text((40,115),'Continuation',fontsize=15)
            p.insert_text((40,250),'Separate content',fontsize=12)
        pdf.save(source)
    client=TestClient(web.app)
    item=client.post('/api/documents',files={'file':('source.pdf',source.read_bytes())}).json()
    prefix=f"/api/documents/{item['id']}"
    item=client.post(prefix+'/render',json={'mode':'fit','replacements':{}}).json()
    before=item['latest_run']; old=client.get(prefix+f'/download/new?run={before}').content
    regions=item['layout']['pages'][0]['regions'];ids=[r['id'] for r in regions[:2]]
    called=[]
    def translate(layout,*args,**kwargs):
        called.append(copy.deepcopy(layout))
        assert kwargs['force']
        assert len(layout['pages'])==1 and len(layout['pages'][0]['regions'])==1
        r=layout['pages'][0]['regions'][0]
        assert r['text']=='First heading Continuation'
        return {'replacements':{r['id']:'Combined translated heading'},'recovery':{}}
    monkeypatch.setattr(legacy,'translate_shared',translate)
    payload=dict(base_run=before,region_ids=ids,bbox=[35,55,370,145],
                 source_text='First heading\nContinuation',action='translate_apply')
    response=client.post(prefix+'/regions/edit',json=payload)
    assert response.status_code==200,response.text
    edited=response.json();report=edited['report'];id=report['region_edit']['region_id']
    assert report['render_scope']=={'incremental':True,'rendered_pages':[0]}
    assert report['docx_output']['deferred']
    assert len(edited['layout']['pages'][0]['regions'])==len(regions)-1
    r=next(r for r in edited['layout']['pages'][0]['regions'] if r['id']==id)
    assert r['bbox']==payload['bbox']
    assert r['source_bbox']!=r['bbox']
    assert len(called)==1
    assert client.post(prefix+'/regions/edit',json=payload).status_code==409
    new=client.get(prefix+f"/download/new?run={edited['latest_run']}").content
    with fitz.open(stream=old,filetype='pdf') as a,fitz.open(stream=new,filetype='pdf') as b:
        assert 'Combined translated heading' in b[0].get_text()
        assert a[1].get_pixmap().samples==b[1].get_pixmap().samples
    assert client.get(prefix+f'/download/new?run={before}').content==old
    payload.update(base_run=edited['latest_run'],region_ids=[id],bbox=[20,20,400,300],action='apply')
    assert client.post(prefix+'/regions/edit',json=payload).status_code==422
    payload['bbox']=[35,55,370,145]
    def fail(*args,**kwargs):raise RuntimeError('API unavailable')
    monkeypatch.setattr(legacy,'translate_shared',fail)
    payload['action']='translate_apply'
    assert client.post(prefix+'/regions/edit',json=payload).status_code==400
    assert client.get(prefix).json()['latest_run']==edited['latest_run']
    assert item['id'] not in web.ACTIVE
