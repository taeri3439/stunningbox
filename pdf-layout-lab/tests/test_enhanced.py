import fitz
import pytest
from pathlib import Path

from engine.enhanced import analyze_page
from engine.models import RenderRequest
from engine.service import render


def test_table_cells_merge_fonts_and_preserve_grid(tmp_path):
    source=tmp_path/'table.pdf'
    with fitz.open() as doc:
        p=doc.new_page(width=400,height=300)
        for x in [30,180,370]:p.draw_line((x,30),(x,230),color=(0,0,0))
        for y in [30,100,230]:p.draw_line((30,y),(370,y),color=(0,0,0))
        p.insert_text((40,55),'First ',fontsize=9)
        p.insert_text((65,55),'cell',fontsize=9,fontname='hebo')
        p.insert_text((190,55),'Second cell',fontsize=9)
        p.insert_text((40,125),'Third cell',fontsize=7)
        p.insert_text((190,125),'Fourth cell',fontsize=9)
        doc.save(source)
    with fitz.open(source) as doc:page=analyze_page(doc[0],'en',use_ocr=False)
    assert len(page['regions'])==4
    assert all(r['kind']=='table_cell' for r in page['regions'])
    layout={'pages':[page],'region_count':4}
    policy=RenderRequest(mode='fit',replacements={r['id']:'Translated cell with longer content' for r in page['regions']},min_scale=.55,min_font_size=5,line_height=1.1)
    report=render(source,tmp_path/'output.pdf',layout,policy)
    assert all(r['status']!='NEEDS_REVIEW' for r in report['regions']),report
    assert all(r['text_check'] for r in report['regions']),report
    with fitz.open(source) as a,fitz.open(tmp_path/'output.pdf') as b:
        assert len(a[0].get_drawings())==len(b[0].get_drawings())
        assert 'First' not in b[0].get_text()


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1]/'models'/'tessdata'/'eng.traineddata').exists(),reason='Install local OCR model with setup-ocr.ps1')
def test_scanned_text_is_ocr_translated_without_original_ink(tmp_path):
    source=tmp_path/'scan.pdf'
    with fitz.open() as text:
        p=text.new_page(width=300,height=180)
        p.insert_text((40,70),'Annual revenue',fontsize=18)
        pix=p.get_pixmap(dpi=180)
        with fitz.open() as doc:
            doc.new_page(width=300,height=180).insert_image(p.rect,stream=pix.tobytes('png'))
            doc.save(source)
    with fitz.open(source) as doc:page=analyze_page(doc[0],'en')
    assert any(r['ocr'] and 'Annual' in r['text'] for r in page['regions'])
    layout={'pages':[page],'region_count':len(page['regions'])}
    report=render(source,tmp_path/'output.pdf',layout,RenderRequest(mode='fit',replacements={r['id']:'New revenue' for r in page['regions']},min_scale=.55,min_font_size=5,line_height=1.1))
    assert report['validation']['skipped']==0,report
    with fitz.open(tmp_path/'output.pdf') as doc:
        assert 'New revenue' in doc[0].get_text()
        p=doc[0]
        tp=p.get_textpage_ocr(language='eng',dpi=180,full=True,tessdata=str(__import__('engine.enhanced',fromlist=['ROOT']).ROOT/'models'/'tessdata'))
        seen=p.get_text(textpage=tp)
        assert 'Annual' not in seen,seen


def test_translation_cache_tracks_region_content(tmp_path,monkeypatch):
    from engine import legacy
    legacy.setup()
    from engine.inspect import inspect_pdf
    from test_lab import make_pdf
    from test_translation import CountingTranslator
    from pipeline import translator
    monkeypatch.setattr(legacy,'configuration',lambda:{'configured':True,'provider':'openai','model':'test'})
    counter=CountingTranslator()
    monkeypatch.setattr(translator,'get_translator',lambda:counter)
    layout=inspect_pdf(make_pdf(tmp_path/'source.pdf'))
    a=legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    layout['pages'][0]['regions'][0]['text']='Corrected OCR text'
    b=legacy.translate_shared(layout,'en','ko',tmp_path/'cache')
    assert a['id']!=b['id']


def test_image_transcript_reads_individual_crop_and_marks_unreadable(tmp_path,monkeypatch):
    from engine import legacy
    from engine.ocr_review import correct_image_sources
    legacy.setup()
    from pipeline import llm_provider
    source=tmp_path/'source.pdf'
    with fitz.open() as doc:
        doc.new_page(width=100,height=100).insert_text((10,30),'Header')
        doc.save(source)
    region={'id':'image1','page':0,'text':'[OCR]','bbox':[5,10,90,40],'patch':True,
            'spans':[{'font':'OCR'}],'warnings':[]}
    model={'pages':[{'number':0,'regions':[region]}]}
    monkeypatch.setattr(legacy,'configuration',lambda:{'configured':True,'provider':'openai','model':'test'})
    def answer(**kwargs):
        assert len(kwargs['user_content'])==1
        return {'text':'Header','kind':'text'},'tool_calls'
    monkeypatch.setattr(llm_provider,'openai_tool_call',answer)
    correct_image_sources(model,source,tmp_path/'cache')
    assert region['text']=='Header' and region['ocr_verified_by']=='vision-single'
    monkeypatch.setattr(llm_provider,'openai_tool_call',lambda **kw:({'text':'','kind':'unreadable'},'tool_calls'))
    correct_image_sources(model,source,tmp_path/'other-cache')
    assert region['warnings']


def test_unreadable_ocr_is_not_counted_as_unchanged_success(tmp_path):
    source=tmp_path/'source.pdf'
    with fitz.open() as doc:
        doc.new_page(width=100,height=100)
        doc.save(source)
    region={'id':'x','text':'[OCR]','bbox':[10,10,30,20],'display_bbox':[10,10,30,20],
            'warnings':['Unreadable text'],'spans':[]}
    layout={'pages':[{'regions':[region],'warnings':[]}],'region_count':1}
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit'))
    assert report['validation']['skipped']==1
    assert report['validation']['status']=='NEEDS_REVIEW'


def test_paragraph_does_not_expand_into_unused_page_margin():
    with fitz.open() as doc:
        p=doc.new_page(width=600,height=400)
        p.insert_text((50,60),'Short heading',fontsize=10)
        region=analyze_page(p,'en',use_ocr=False)['regions'][0]
        assert region['bbox'][2] <= region['source_bbox'][2]+1


def test_missing_outer_table_border_recovers_last_column():
    with fitz.open() as doc:
        p=doc.new_page(width=400,height=300)
        for y in [30,80,160,230]:p.draw_line((30,y),(370,y),color=(0,0,0))
        for x in [30,180]:p.draw_line((x,30),(x,230),color=(0,0,0))
        p.draw_line((370,30),(370,80),color=(0,0,0))
        for y in [55,110,190]:
            p.insert_text((40,y),'Left',fontsize=10)
            p.insert_text((200,y),'Right',fontsize=10)
        regions=analyze_page(p,'en',use_ocr=False)['regions']
        assert len(regions)==6
        assert all(r['kind']=='table_cell' for r in regions)


def test_vision_crop_pads_narrow_strip_without_stretching():
    import base64,io
    from PIL import Image
    from engine.ocr_review import crop_for_vision
    with fitz.open() as doc:
        pix=doc.new_page(width=300,height=30).get_pixmap()
        image=Image.open(io.BytesIO(base64.b64decode(crop_for_vision(pix))))
        assert image.width==460 and image.height==300


def test_adjacent_image_lines_do_not_share_placement_space():
    from engine.enhanced import partition_regions
    regions=[{'source_bbox':[10,10,100,15],'bbox':[9,8,101,17]},
             {'source_bbox':[10,16,100,21],'bbox':[9,14,101,23]}]
    partition_regions(regions)
    assert regions[0]['bbox'][3]==regions[1]['bbox'][1]==15.5
    assert not (fitz.Rect(regions[0]['bbox']) & fitz.Rect(regions[1]['bbox'])).get_area()


def test_image_paragraph_merges_lines_but_keeps_neighbor_column():
    import numpy as np
    from engine.enhanced import merge_image_paragraphs
    def region(box,text):
        return {'source_bbox':box,'bbox':box[:],'text':text,'kind':'paragraph',
                'font_size':6,'spans':[{'font':'OCR'}],'dominant_color':0,
                'mask_boxes':[box[:]],'warnings':[]}
    regions=[region([10,10,90,16],'First'),region([12,18,90,24],'second'),
             region([110,10,180,16],'Neighbor')]
    with fitz.open() as doc:
        page=doc.new_page(width=200,height=100)
        result=merge_image_paragraphs(regions,page,np.full((100,200,3),255,dtype=np.uint8))
    assert len(result)==2
    assert result[0]['text']=='First\nsecond'
    assert result[1]['text']=='Neighbor'
