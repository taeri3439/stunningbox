import copy
import json

import fitz

from engine import image_ocr


def make_layout(tmp_path, count=13):
    source=tmp_path/'source.pdf'
    with fitz.open() as doc:
        doc.new_page(width=300,height=400)
        doc.save(source)
    regions=[{'id':f'r{i}','text':'[OCR]','bbox':[10,10+i*20,150,25+i*20],
              'patch':True,'spans':[{'font':'OCR'}],'warnings':[]} for i in range(count)]
    return source,{'pages':[{'number':0,'regions':regions}]}


def test_one_raster_per_page_batch_count_and_cache_before_raster(tmp_path,monkeypatch):
    source,layout=make_layout(tmp_path)
    original=copy.deepcopy(layout)
    monkeypatch.setattr(image_ocr,'ocr_configuration',lambda:{'provider':'openai','model':'fake'})
    renders=[];calls=[];events=[]
    real=image_ocr.raster_page
    def raster(*args,**kwargs):
        renders.append(args[1]);return real(*args,**kwargs)
    def request(items,config):
        calls.append(len(items))
        return {r['id']:{'text':'Text '+r['id'],'kind':'text'} for r,b in items}
    monkeypatch.setattr(image_ocr,'raster_page',raster)
    monkeypatch.setattr(image_ocr,'request_batch',request)
    image_ocr.correct_images(layout,source,tmp_path/'cache',prepare_progress=lambda *x:events.append(x))
    assert renders==[0]
    assert sorted(calls)==[1,6,6]
    assert events[0]==(1,1,0,13) and events[-1]==(1,1,13,13)
    assert all(r['text']=='Text '+r['id'] for r in layout['pages'][0]['regions'])
    image_ocr.correct_images(original,source,tmp_path/'cache')
    assert len(calls)==3 and len(renders)==1
    # A model change invalidates OCR while an identical re-upload does not.
    monkeypatch.setattr(image_ocr,'ocr_configuration',lambda:{'provider':'openai','model':'changed'})
    image_ocr.correct_images(original,source,tmp_path/'cache')
    assert len(calls)==6


def test_missing_and_duplicate_ids_retry_only_affected_regions(monkeypatch):
    from engine import legacy
    legacy.setup()
    from pipeline import llm_provider
    seen=[]
    def request(**kwargs):
        content=kwargs['user_content'];seen.append(len(content))
        if len(content)>1:
            return {'regions':[{'id':'r0','text':'Good','kind':'text'},
                               {'id':'r1','text':'Bad','kind':'text'},
                               {'id':'r1','text':'Duplicate','kind':'text'}]},'tool_calls'
        return {'text':'Recovered','kind':'text'},'tool_calls'
    monkeypatch.setattr(llm_provider,'openai_tool_call',request)
    result=image_ocr.request_batch([({'id':f'r{i}'},'image') for i in range(3)],{'provider':'openai','model':'fake'})
    assert seen==[6,1,1]
    assert result['r0']['text']=='Good'
    assert result['r1']['text']==result['r2']['text']=='Recovered'


def test_identical_uploads_share_cache(tmp_path,monkeypatch):
    import app
    monkeypatch.setattr(app,'DATA',tmp_path)
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    (a/'source.pdf').write_bytes(b'identical PDF');(b/'source.pdf').write_bytes(b'identical PDF')
    (a/'analysis').mkdir();(a/'analysis'/'saved.json').write_text('{}')
    first=app.document_cache(a);second=app.document_cache(b)
    assert first==second and (second/'analysis'/'saved.json').exists()
    (b/'source.pdf').write_bytes(b'different PDF')
    assert app.document_cache(b)!=first
