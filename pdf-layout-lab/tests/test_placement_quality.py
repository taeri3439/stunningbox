import copy

import fitz
import pytest

from engine.enhanced import analyze_page
from engine.models import RenderRequest, normalized
from engine.service import render
from engine.source_styles import prepare_page


def test_short_bold_heading_uses_table_outer_margin_before_shrinking(tmp_path):
    source=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=500,height=400)
        p.insert_text((40,22),'Evaluation Methods and Criteria',fontsize=12,fontname='hebo')
        p.insert_text((40,42),'1) Goal',fontsize=12,fontname='hebo')
        p.draw_rect((40,85,460,320))
        p.draw_line((250,85),(250,320));p.draw_line((40,120),(460,120))
        p.insert_text((50,105),'Item');p.insert_text((270,105),'Value')
        model=analyze_page(p,'en',use_ocr=False);pdf.save(source)
    heading=next(r for r in model['regions'] if 'Goal' in r['text'])
    original=copy.deepcopy(heading['source_bbox']); masks=copy.deepcopy(heading['mask_boxes'])
    policy=RenderRequest(mode='fit',replacements={heading['id']:'1) Technology Development Objective (70%)'},min_scale=.4,min_font_size=6)
    layout={'pages':[model]}
    report=render(source,tmp_path/'out.pdf',layout,policy)
    result=next(r for r in report['regions'] if r['id']==heading['id'])
    assert result['font_size']==pytest.approx(12)
    assert result['typography_quality']['line_count']==1
    assert heading['source_bbox']==original and heading['mask_boxes']==masks
    assert heading['bbox'][2]>400 and heading['bbox'][3]<85
    with fitz.open(tmp_path/'out.pdf') as pdf:
        spans=[s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans']]
        assert any('Technology' in s['text'] and s['flags']&16 and abs(s['size']-12)<.01 for s in spans)
    bounds=copy.deepcopy(heading['bbox'])
    render(source,tmp_path/'again.pdf',layout,policy)
    assert heading['bbox']==bounds


def test_column_reservation_stops_at_staggered_second_column():
    with fitz.open() as pdf:
        p=pdf.new_page(width=600,height=400)
        p.insert_text((40,45),'Heading',fontsize=12,fontname='hebo')
        p.insert_text((40,100),'Left column body',fontsize=12)
        p.insert_text((320,72),'Right column paragraph one',fontsize=12)
        p.insert_text((320,115),'Right column paragraph two',fontsize=12)
        model=analyze_page(p,'en',use_ocr=False)
        prepare_page(p,model)
        heading=next(r for r in model['regions'] if 'Heading' in r['text'])
        assert heading['bbox'][2]<320


def test_native_cell_reclaims_only_its_own_blank_space(tmp_path):
    source=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=500,height=300)
        p.draw_rect((40,40,460,200));p.draw_line((260,40),(260,200));p.draw_line((40,100),(460,100))
        p.insert_text((50,65),'Label',fontsize=12,fontname='hebo')
        p.insert_text((275,65),'Neighbor',fontsize=12)
        p.insert_text((50,130),'Next row',fontsize=12)
        model=analyze_page(p,'en',use_ocr=False);pdf.save(source)
    label=next(r for r in model['regions'] if r['text']=='Label')
    report=render(source,tmp_path/'out.pdf',{'pages':[model]},RenderRequest(mode='fit',
                  replacements={label['id']:'Translated label'},min_scale=.4,min_font_size=6))
    result=next(r for r in report['regions'] if r['id']==label['id'])
    assert result['font_size']==pytest.approx(12)
    assert label['bbox'][2]<260 and label['bbox'][3]<100
    with fitz.open(source) as before,fitz.open(tmp_path/'out.pdf') as after:
        assert before[0].get_pixmap(clip=fitz.Rect(265,42,458,98)).samples==after[0].get_pixmap(clip=fitz.Rect(265,42,458,98)).samples


def test_manual_newline_survives_heading_quality_pass(tmp_path):
    source=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=500,height=300)
        p.insert_text((40,45),'Bold heading',fontsize=12,fontname='hebo')
        p.insert_text((40,140),'Next paragraph',fontsize=12)
        model=analyze_page(p,'en',use_ocr=False);pdf.save(source)
    r=next(r for r in model['regions'] if 'heading' in r['text'])
    replacement='First line\n    Indented second line'
    render(source,tmp_path/'out.pdf',{'pages':[model]},RenderRequest(mode='fit',
           replacements={r['id']:replacement},manual_regions=[r['id']],min_scale=.4,min_font_size=6))
    with fitz.open(tmp_path/'out.pdf') as pdf:
        words={w[4]:w for w in pdf[0].get_text('words')}
        assert words['Indented'][0]>words['First'][0]+8
        assert words['Indented'][1]>words['First'][1]+10
        assert normalized(replacement) in normalized(pdf[0].get_text())


def test_blank_space_does_not_cross_graphic_rule_or_manual_box():
    with fitz.open() as pdf:
        p=pdf.new_page(width=500,height=300)
        p.insert_text((40,45),'Heading',fontsize=12,fontname='hebo')
        p.draw_line((200,15),(200,90))
        model=analyze_page(p,'en',use_ocr=False)
        r=model['regions'][0]
        prepare_page(p,model)
        assert r['bbox'][2]<200
        r.update(user_geometry=True,user_bbox=[40,30,150,55])
        prepare_page(p,model)
        assert r['bbox']==[40,30,150,55]


def test_cached_native_bold_is_retained_inside_user_box(tmp_path):
    source=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=200)
        p.insert_text((40,45),'Bold title',fontsize=12,fontname='hebo')
        model=analyze_page(p,'en',use_ocr=False);pdf.save(source)
    r=model['regions'][0]
    r.update(dominant_flags=0,user_geometry=True,user_bbox=[35,25,350,80])
    model['source_style_version']=1
    render(source,tmp_path/'out.pdf',{'pages':[model]},RenderRequest(mode='fit',
           replacements={r['id']:'Translated bold title'},min_scale=.9,min_font_size=9))
    with fitz.open(tmp_path/'out.pdf') as pdf:
        assert all(s['flags']&16 for b in pdf[0].get_text('dict')['blocks'] if b['type']==0
                   for line in b['lines'] for s in line['spans'])


def test_repeat_layout_reuses_source_geometry_but_invalidates_changed_source(monkeypatch):
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=300)
        p.insert_text((40,45),'Heading',fontsize=12,fontname='hebo')
        model=analyze_page(p,'en',use_ocr=False)
        prepare_page(p,model)
        def unexpected(*args,**kwargs):raise AssertionError('Repeated table analysis')
        monkeypatch.setattr(fitz.Page,'find_tables',unexpected)
        prepare_page(p,model)
        p.draw_line((180,20),(180,90))
        with pytest.raises(AssertionError,match='Repeated table analysis'):
            prepare_page(p,model)
