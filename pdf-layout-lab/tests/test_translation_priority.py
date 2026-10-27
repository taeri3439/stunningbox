import copy

import fitz
import pytest
from fastapi.testclient import TestClient

import app as web
from engine.enhanced import analyze_page
from engine.inspect import inspect_pdf
from engine.models import RenderRequest
from engine.service import render
from engine.validate import characters, character_key
from test_lab import make_pdf


def test_restore_and_reapply_preserve_translation_history_and_other_regions(tmp_path, monkeypatch):
    monkeypatch.setattr(web,'DATA',tmp_path/'data');web.DATA.mkdir()
    client=TestClient(web.app)
    source=make_pdf(tmp_path/'source.pdf')
    uploaded=client.post('/api/documents',files={'file':('source.pdf',source.read_bytes())}).json()
    prefix=f"/api/documents/{uploaded['id']}"
    first,second=uploaded['layout']['pages'][0]['regions'][:2]
    translations={first['id']:'Translated heading',second['id']:'Translated body'}
    initial=client.post(prefix+'/render',json={'mode':'fit','replacements':translations}).json()
    initial_bytes=client.get(prefix+f"/download/new?run={initial['latest_run']}").content
    payload={'base_run':initial['latest_run'],'restore_region_ids':[first['id']]}
    response=client.post(prefix+'/render',json=payload)
    assert response.status_code==200,response.text
    restored=response.json();report=restored['report']
    outcome=next(r for r in report['regions'] if r['id']==first['id'])
    assert outcome['restored'] and not outcome['applied'] and outcome['text_check']
    assert outcome['source_text']==first['text'] and outcome['translated_text']==translations[first['id']]
    assert report['policy']['replacements']==translations
    assert next(r for r in report['regions'] if r['id']==second['id'])['applied']
    with fitz.open(stream=client.get(prefix+f"/download/new?run={restored['latest_run']}").content,filetype='pdf') as pdf,fitz.open(source) as original:
        original_keys={character_key(c) for c in characters(original[0]) if fitz.Rect(first['bbox']).contains(fitz.Point(c['origin']))}
        assert original_keys <= {character_key(c) for c in characters(pdf[0])}
        assert 'Translated heading' not in pdf[0].get_text() and 'Translated body' in pdf[0].get_text()
    assert client.post(prefix+'/render',json=payload).status_code==409
    response=client.post(prefix+'/render',json={'base_run':restored['latest_run'],
        'apply_region_ids':[first['id']],'replacements':{first['id']:translations[first['id']]}})
    assert response.status_code==200,response.text
    reapplied=response.json()
    assert not reapplied['report']['policy']['restored_regions']
    assert next(r for r in reapplied['report']['regions'] if r['id']==first['id'])['applied']
    assert client.get(prefix+f"/download/new?run={initial['latest_run']}").content==initial_bytes


def test_table_peers_share_font_size_without_flattening_header(tmp_path):
    source=tmp_path/'table.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=420,height=320)
        p.draw_rect((30,40,390,230))
        for y in (80,130,180):p.draw_line((30,y),(390,y))
        p.draw_line((210,80),(210,230))
        p.insert_text((40,65),'Table heading',fontsize=15,fontname='hebo')
        for i,(x,y) in enumerate([(40,105),(220,105),(40,155),(220,155),(40,205),(220,205)]):
            p.insert_text((x,y),f'Cell {i}',fontsize=11)
        model=analyze_page(p,'en',use_ocr=False);pdf.save(source)
    replacements={r['id']:('A longer translated sentence filling several lines inside this cell.' if r['text']=='Cell 0' else 'Short translated entry')
                  for r in model['regions'] if r['text'].startswith('Cell')}
    layout={'pages':[model]}
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',replacements=replacements,min_scale=.6,min_font_size=6))
    body=[r for r in report['regions'] if r['id'] in replacements]
    assert all(r['applied'] and r['text_check'] for r in body),body
    matched=[r for r in body if r.get('table_font',{}).get('matched')]
    assert len(matched)>=4
    assert max(r['font_size'] for r in matched)-min(r['font_size'] for r in matched)<.01
    assert min(r['font_size'] for r in matched)>=11*.85
    with fitz.open(tmp_path/'out.pdf') as pdf:
        header=[s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans'] if 'Table heading' in s['text']]
        assert header[0]['size']==pytest.approx(15) and header[0]['flags']&16
    assert report['validation']['structure_ok']


def test_coincident_unowned_glyphs_remain_blocked_without_source_damage(tmp_path):
    from engine.placement_resolution import deletion_boxes
    region={'bbox':[0,0,50,30],'spans':[{'font':'Helvetica','chars':[{'origin':[10,20]}]}]}
    chars=[{'c':'A','origin':[10,20],'bbox':[10,10,20,22]},
           {'c':'B','origin':[11,20],'bbox':[9,9,21,23]}]
    with pytest.raises(ValueError,match='분리'):
        deletion_boxes(region,chars)


def test_empty_space_overlap_does_not_create_false_review_or_retain_text(tmp_path):
    source=make_pdf(tmp_path/'source.pdf');layout=inspect_pdf(source)
    first,second=layout['pages'][0]['regions'][:2]
    first['source_bbox']=list(first['bbox']);second['bbox']=list(first['bbox'])
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',replacements={first['id']:'Translated'}))
    result=report['regions'][0]
    assert result['applied'] and not result['review_required'] and result['text_check']
    assert result['model_overlaps']==[second['id']]
    assert not any(p['outside_changed_pixels'] for p in report['validation']['pages'])


def test_restore_raster_region_preserves_original_pixels(tmp_path):
    with fitz.open() as text,fitz.open() as pdf:
        page=text.new_page(width=320,height=200);page.insert_text((35,75),'Original scan',fontsize=16)
        p=pdf.new_page(width=320,height=200);p.insert_image(p.rect,stream=page.get_pixmap(dpi=180).tobytes('png'))
        source=tmp_path/'scan.pdf';pdf.save(source)
    with fitz.open(source) as pdf:model=analyze_page(pdf[0],'en')
    layout={'pages':[model]};region=model['regions'][0]
    policy=RenderRequest(mode='fit',replacements={region['id']:'Translated scan'},min_scale=.6,min_font_size=8)
    initial=render(source,tmp_path/'translated.pdf',layout,policy)
    assert initial['regions'][0]['applied']
    report=render(source,tmp_path/'restored.pdf',layout,policy.model_copy(update={'restored_regions':[region['id']]}))
    assert report['regions'][0]['restored'] and report['regions'][0]['translated_text']=='Translated scan'
    with fitz.open(source) as before,fitz.open(tmp_path/'restored.pdf') as after:
        assert before[0].get_pixmap().samples==after[0].get_pixmap().samples


def test_unresolved_capacity_is_distinguished_from_applied_review(tmp_path):
    source=make_pdf(tmp_path/'source.pdf');layout=inspect_pdf(source);region=layout['pages'][0]['regions'][0]
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',replacements={region['id']:'Excessively long text '*500}))
    result=report['regions'][0]
    assert not result['applied'] and result['review_required']
    assert result['failure_category']=='insufficient_space'
    assert result['translated_text']!=result['source_text']


def test_patch_is_transparent_outside_erase_masks_and_preserves_neighbor(tmp_path):
    from io import BytesIO
    from PIL import Image
    from engine.enhanced import cleaned_patch
    with fitz.open() as pdf:
        p=pdf.new_page(width=200,height=130)
        p.insert_text((20,45),'Erase',fontsize=11)
        p.insert_text((85,45),'Keep',fontsize=11)
        r=dict(bbox=[10,20,150,65],mask_boxes=[[18,32,75,48]])
        patch=Image.open(BytesIO(cleaned_patch(p,r,protected_boxes=[fitz.Rect(83,31,115,48)])))
        assert patch.mode=='RGBA'
        assert patch.getpixel((int((95-10)*240/72),int((40-20)*240/72)))[3]==0
        assert patch.getpixel((int((30-10)*240/72),int((40-20)*240/72)))[3]==255


def test_pixel_recheck_excludes_rounding_strip_but_detects_real_damage(tmp_path):
    from engine.validate import retained_pixels
    with fitz.open() as before,fitz.open() as after:
        a=before.new_page(width=150,height=100);b=after.new_page(width=150,height=100)
        box=fitz.Rect(20.8,40.8,80.8,70.8)
        b.draw_rect((20,39,80,40.5),color=None,fill=(0,0,0))
        assert retained_pixels(a,b,box)
        b.draw_rect((30,50,35,55),color=None,fill=(0,0,0))
        assert not retained_pixels(a,b,box)


@pytest.mark.parametrize('symbol,applied',[('\uf09f',True),('\ue123',False)])
def test_saved_marker_repair_preserves_raw_source_and_translation(tmp_path,symbol,applied):
    source=tmp_path/'marker.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=300,height=200)
        p.insert_text((30,60),'X Original list item',fontsize=12)
        pdf.save(source)
    layout=inspect_pdf(source);region=layout['pages'][0]['regions'][0]
    region.update(text=symbol+' Original list item',source_bbox=list(region['bbox']),
                  patch=True,warnings=['특수 글꼴 문자를 정확히 읽지 못했습니다. 원문을 유지합니다.'])
    translated=symbol+' Translated item'
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',replacements={region['id']:translated}))
    result=report['regions'][0]
    assert result['applied']==applied
    assert result['source_text']==symbol+' Original list item'
    assert result['translated_text']==translated and report['policy']['replacements'][region['id']]==translated
    if applied:assert result['text_check'] and result['logical_text']=='• Translated item'


@pytest.mark.parametrize('unit',['㎥','㎛','㎎'])
@pytest.mark.parametrize('spacing',[False,True])
def test_compatibility_units_render_completely_without_mutating_translation(tmp_path,unit,spacing):
    source=make_pdf(tmp_path/'source.pdf');layout=inspect_pdf(source);region=layout['pages'][0]['regions'][0]
    translated='Measure 15 '+unit
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',preserve_spacing=spacing,
        replacements={region['id']:translated}))
    result=report['regions'][0]
    assert result['applied'] and result['text_check']
    assert result['translated_text']==translated and report['policy']['replacements'][region['id']]==translated


def test_remaining_collision_applies_translation_with_review_and_restores(tmp_path):
    source=make_pdf(tmp_path/'source.pdf'); layout=inspect_pdf(source)
    first,neighbor=layout['pages'][0]['regions'][:2]
    first['source_bbox']=list(first['bbox'])
    first['bbox']=list(neighbor['bbox'])
    first['user_geometry']=True; first['user_bbox']=list(first['bbox'])
    first['font_size']=neighbor['font_size']
    policy=RenderRequest(mode='fit',preserve_spacing=False,min_scale=1,min_font_size=10,
                         replacements={first['id']:'Translated entry'})
    report=render(source,tmp_path/'out.pdf',layout,policy)
    result=report['regions'][0]
    assert result['applied'] and result['text_check'],result
    assert result['review_required'] and result['layout_review_applied'],result
    assert not result['placement_checks']['clear_of_neighbors']
    assert report['regions'][1]['text_check']  # Never erase the neighbor.
    assert report['regions'][2]['text_check'] and not report['regions'][2]['review_required']
    with fitz.open(tmp_path/'out.pdf') as pdf:
        assert 'Translated entry' in pdf[0].get_text()
        assert 'Original text stays inside its region.' in pdf[0].get_text()
    restored=render(source,tmp_path/'restored.pdf',layout,policy.model_copy(update={'restored_regions':[first['id']]}))
    assert restored['regions'][0]['restored']
    with fitz.open(source) as a,fitz.open(tmp_path/'restored.pdf') as b:
        assert a[0].get_pixmap().samples==b[0].get_pixmap().samples


def test_reviewable_layout_never_accepts_missing_text_or_page_clipping():
    from engine.validate import check_placement
    with fitz.open() as pdf:
        page=pdf.new_page(width=200,height=100)
        page.insert_text((40,50),'Visible text',fontsize=12)
        result=check_placement(page,{'bbox':[40,35,60,55]},'Visible text',review_layout=True)
        assert not result['within_bounds']
        with pytest.raises(ValueError,match='누락'):
            check_placement(page,{'bbox':[0,0,200,100]},'Missing text',review_layout=True)


def test_recognition_warning_with_valid_translation_becomes_applied_review(tmp_path):
    source=make_pdf(tmp_path/'source.pdf'); layout=inspect_pdf(source)
    region=layout['pages'][0]['regions'][0]
    region['warnings']=['원문 인식에 불확실한 문자가 있습니다.']
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',replacements={region['id']:'Confirmed translation'}))
    assert report['regions'][0]['applied'] and report['regions'][0]['review_required']
    assert report['regions'][0]['text_check']


def test_duplicate_ocr_transcript_renders_once_and_restores_as_one_source(tmp_path):
    from engine.source_duplicates import aliases,edit_groups
    source=make_pdf(tmp_path/'source.pdf');layout=inspect_pdf(source)
    model=layout['pages'][0];first=model['regions'][0]
    duplicate=copy.deepcopy(first);duplicate.update(id='ocr-copy',ocr=True)
    for s in duplicate['spans']:s['font']='OCR'
    model['regions'].append(duplicate)
    assert aliases(model)=={'ocr-copy':first['id']}
    assert edit_groups(layout,['ocr-copy'])['ocr-copy']=={'ocr-copy',first['id']}
    policy=RenderRequest(mode='fit',replacements={first['id']:'Preferred translation','ocr-copy':'Alternative translation'})
    report=render(source,tmp_path/'out.pdf',layout,policy)
    result=next(r for r in report['regions'] if r['id']=='ocr-copy')
    assert result['applied'] and result['text_check'] and result['duplicate_of']==first['id']
    assert result['translated_text']=='Alternative translation' and result['effective_translated_text']=='Preferred translation'
    with fitz.open(tmp_path/'out.pdf') as pdf:
        assert pdf[0].get_text().count('Preferred translation')==1
        assert 'Alternative translation' not in pdf[0].get_text()
    restored=render(source,tmp_path/'restored.pdf',layout,policy.model_copy(update={'restored_regions':['ocr-copy']}))
    assert all(r['restored'] for r in restored['regions'] if r['id'] in {first['id'],'ocr-copy'})
    duplicate['source_bbox']=[30,250,300,280]
    assert not aliases(model)  # Same wording at a different location is distinct.


def test_ocr_number_record_with_whole_cell_transcript_is_duplicate():
    from engine.source_duplicates import aliases
    native=dict(id='native',text='1. A sufficiently long question in a table cell?',ocr=False,
                kind='table_cell',font_size=12,bbox=[20,30,240,70],source_bbox=[42,32,230,62],
                spans=[dict(font='Helvetica',text='A sufficiently long question in a table cell?')])
    ocr=dict(id='ocr',text='1. A sufficiently long question in the table cell?',ocr=True,
             kind='table_cell',font_size=12,bbox=[20,30,240,70],source_bbox=[22,32,33,46],
             spans=[dict(font='OCR',text='1.')])
    assert aliases({'regions':[native,ocr]})=={'ocr':'native'}
    ocr['bbox']=[20,90,240,130];ocr['source_bbox']=[22,92,33,106]
    assert not aliases({'regions':[native,ocr]})


def test_duplicate_alias_manual_apply_and_restore_update_one_physical_group(tmp_path,monkeypatch):
    import json
    monkeypatch.setattr(web,'DATA',tmp_path/'data');web.DATA.mkdir()
    client=TestClient(web.app);source=make_pdf(tmp_path/'source.pdf')
    doc=client.post('/api/documents',files={'file':('source.pdf',source.read_bytes())}).json()
    prefix=f"/api/documents/{doc['id']}";path=web.DATA/doc['id']/'layout.json'
    layout=json.loads(path.read_text(encoding='utf-8'));first=layout['pages'][0]['regions'][0]
    duplicate=copy.deepcopy(first);duplicate.update(id='ocr-copy',ocr=True)
    for s in duplicate['spans']:s['font']='OCR'
    layout['pages'][0]['regions'].append(duplicate)
    path.write_text(json.dumps(layout),encoding='utf-8')
    initial=client.post(prefix+'/render',json={'mode':'fit','replacements':{first['id']:'First version','ocr-copy':'Other version'}})
    assert initial.status_code==200,initial.text
    applied=client.post(prefix+'/render',json={'base_run':initial.json()['latest_run'],
        'apply_region_ids':['ocr-copy'],'replacements':{'ocr-copy':'Confirmed'}})
    assert applied.status_code==200,applied.text
    report=applied.json()['report']
    assert report['policy']['replacements'][first['id']]=='Confirmed'
    assert all(r['applied'] and r['pdf_text_found'] for r in report['regions'] if r['id'] in {first['id'],'ocr-copy'})
    restored=client.post(prefix+'/render',json={'base_run':applied.json()['latest_run'],'restore_region_ids':['ocr-copy']})
    assert restored.status_code==200,restored.text
    assert all(r['restored'] for r in restored.json()['report']['regions'] if r['id'] in {first['id'],'ocr-copy'})


def test_wrapped_table_rows_use_one_size_inside_cell(tmp_path):
    source=tmp_path/'wrapped-table.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=300)
        p.draw_rect((30,40,370,220));p.draw_line((30,130),(370,130))
        p.insert_text((40,65),'Original first wrapped row',fontsize=12)
        p.insert_text((40,82),'Original second wrapped row',fontsize=12)
        p.insert_text((40,155),'Another table cell',fontsize=12)
        model=analyze_page(p,'en',use_ocr=False);pdf.save(source)
    region=next(r for r in model['regions'] if 'first wrapped' in r['text'])
    text='A longer translated first row that needs more horizontal space than the source line.\nShort final row.'
    report=render(source,tmp_path/'out.pdf',{'pages':[model]},RenderRequest(mode='fit',
        replacements={region['id']:text},min_scale=.4,min_font_size=5))
    outcome=next(r for r in report['regions'] if r['id']==region['id'])
    assert outcome['applied'] and outcome['text_check']
    sizes={key[4] for key in outcome['placement_keys']}
    assert len(sizes)==1


def test_incomplete_ocr_translation_is_distinct_from_layout_review(tmp_path):
    source=make_pdf(tmp_path/'source.pdf');layout=inspect_pdf(source)
    region=layout['pages'][0]['regions'][0];region['text']='[OCR] original'
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
        replacements={region['id']:'Translated [OCR]'}))
    result=report['regions'][0]
    assert not result['applied'] and result['failure_category']=='recognition_or_ownership'
    confirmed=render(source,tmp_path/'confirmed.pdf',layout,RenderRequest(mode='fit',
        replacements={region['id']:'Confirmed'},manual_regions=[region['id']]))
    assert confirmed['regions'][0]['applied']
