import copy

import fitz
import pytest

from engine.enhanced import analyze_page
from engine.legacy import CheckedTranslator
from engine.models import RenderRequest
from engine.service import render


def test_grey_first_line_and_black_continuation_are_one_unit(tmp_path):
    source = tmp_path/'source.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page(width=500,height=300)
        page.insert_text((40,55),'RegTech software retains subscriptions during',fontsize=12,color=(.12,.12,.12))
        page.insert_text((50,74),'a downturn because switching costs are high.',fontsize=12)
        page.insert_text((40,115),'- A separate list item follows.',fontsize=12)
        layout_page = analyze_page(page,'en',use_ocr=False)
        pdf.save(source)
    region = next(r for r in layout_page['regions'] if 'RegTech' in r['text'])
    assert 'switching costs' in region['text']
    assert 'separate' not in region['text']
    assert region['logical_paragraph']
    assert len(region['source_fragments']) == 2
    source_spans = [s for f in region['source_fragments'] for s in f['spans']]
    assert source_spans == region['spans']
    assert {s['color'] for s in source_spans} == {0,2039583}
    original_boxes = copy.deepcopy([s['bbox'] for s in region['spans']])
    calls = []
    class API:
        def translate(self, texts, *args, **kwargs):
            calls.extend(texts)
            return ['A complete translated paragraph explaining recurring revenue and switching costs.']
    translated = CheckedTranslator(API(), paragraph_texts=[region['text']]).translate([region['text']])[0]
    assert calls == [region['text']]
    report = render(source,tmp_path/'output.pdf',{'pages':[layout_page]},RenderRequest(
        mode='fit',replacements={region['id']:translated},min_scale=.4,min_font_size=6))
    assert next(r for r in report['regions'] if r['id']==region['id'])['status'] in {'FIT','ADJUSTED'}
    assert [s['bbox'] for s in region['spans']] == original_boxes
    assert len(region['typography']['parts']) == 1
    with fitz.open(tmp_path/'output.pdf') as pdf:
        text = pdf[0].get_text()
        assert 'translated paragraph' in text and 'RegTech' not in text
        assert 'separate list item' in text


@pytest.mark.parametrize('offset',[0,5])
def test_two_columns_remain_independent_with_interleaved_rows(offset):
    with fitz.open() as pdf:
        page = pdf.new_page(width=600,height=300)
        for x,label,shift in [(40,'Left',0),(320,'Right',offset)]:
            page.insert_text((x,60+shift),f'{label} column starts a sentence',fontsize=11)
            page.insert_text((x,76+shift),f'{label} continuation stays here',fontsize=11)
        regions = analyze_page(page,'en',use_ocr=False)['regions']
    assert len(regions)==2
    assert all(('Left' in r['text']) != ('Right' in r['text']) for r in regions)
    assert all('continuation' in r['text'] for r in regions)


def test_adjacent_table_cells_never_share_a_translation_unit():
    with fitz.open() as pdf:
        page = pdf.new_page(width=500,height=250)
        page.draw_rect((20,20,480,180))
        page.draw_line((250,20),(250,180))
        page.draw_line((20,100),(480,100))
        for x,y,label in [(35,48,'Left upper'),(265,48,'Right upper'),(35,130,'Left lower'),(265,130,'Right lower')]:
            page.insert_text((x,y),label+' sentence',fontsize=10)
            page.insert_text((x,y+15),'continued in this cell',fontsize=10)
        regions = analyze_page(page,'en',use_ocr=False)['regions']
    assert len(regions)==4
    assert all(r['kind']=='table_cell' for r in regions)
    for region in regions:
        assert sum(label in region['text'] for label in ['Left upper','Right upper','Left lower','Right lower'])==1


def test_close_columns_in_one_pdf_block_keep_separate_paragraphs():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600,height=200)
        left = 'Left column text'
        right_x = 40 + fitz.get_text_length(left,fontsize=11) + 12
        for y in (50,66):
            page.insert_text((40,y),left,fontsize=11)
            page.insert_text((right_x,y),'Right column text',fontsize=11)
        assert len(page.get_text('dict')['blocks']) == 1
        regions = analyze_page(page,'en',use_ocr=False)['regions']
    assert len(regions) == 2
    assert all(('Left' in r['text']) != ('Right' in r['text']) for r in regions)
    assert all(len(r['source_fragments']) == 2 for r in regions)
    left_region,right_region = sorted(regions,key=lambda r:r['bbox'][0])
    assert left_region['bbox'][2] < right_region['bbox'][0]


def test_sentence_gap_is_not_a_column_when_neighboring_lines_bridge_it():
    with fitz.open() as pdf:
        page = pdf.new_page(width=550,height=250)
        page.insert_text((40,50),'A paragraph explaining document preparation and manual work',fontsize=11)
        page.insert_text((40,66),'continues across the width of the document on another line',fontsize=11)
        prefix = 'and remains manual.'
        page.insert_text((40,82),prefix,fontsize=11)
        page.insert_text((40+fitz.get_text_length(prefix,fontsize=11)+16,82),
                         'This increases time and risk',fontsize=11)
        page.insert_text((40,98),'for the company when preparing regulatory submissions.',fontsize=11)
        page.insert_text((40,140),'- First distinct list item',fontsize=11)
        page.insert_text((40,156),'- Second distinct list item',fontsize=11)
        regions = analyze_page(page,'en',use_ocr=False)['regions']
    paragraph = next(r for r in regions if r['text'].startswith('A paragraph'))
    assert len(paragraph['source_fragments']) == 4
    assert 'and remains manual. This increases time and risk' in paragraph['text']
    assert 'regulatory submissions.' in paragraph['text']
    assert len(regions) == 3


def test_separator_and_new_heading_are_hard_boundaries():
    with fitz.open() as pdf:
        page=pdf.new_page(width=500,height=250)
        page.insert_text((40,50),'Unfinished sentence on one side',fontsize=12)
        page.draw_line((30,55),(470,55))
        page.insert_text((40,69),'text beyond the separator',fontsize=12)
        page.insert_text((40,105),'Body paragraph at the same size',fontsize=12)
        page.insert_text((40,124),'(2) New heading',fontsize=12)
        regions=analyze_page(page,'en',use_ocr=False)['regions']
    assert len(regions)==4


def test_failed_paragraph_cannot_cache_or_apply_a_translated_half(tmp_path):
    source='RegTech soft\nware retains subscriptions'
    class API:
        def translate(self,texts,*args,**kwargs):
            assert texts == [source]
            return ['']
    checked=CheckedTranslator(API(),tmp_path,paragraph_texts=[source])
    assert checked.translate([source])==[source]
    assert checked.review_texts=={source}
    assert not list(tmp_path.glob('*.json'))


def test_paragraph_cache_and_checklist_paths_are_separate(tmp_path):
    calls=[]
    class API:
        def translate(self,texts,*args,**kwargs):
            calls.extend(texts)
            return [t.upper() for t in texts]
    paragraph='Sentence with a\nphysical wrap'
    checklist='1) pump\n  detail ☑ pass □ fail'
    first=CheckedTranslator(API(),tmp_path,paragraph_texts=[paragraph])
    assert first.translate([paragraph,checklist]) == [paragraph.upper(),checklist.upper()]
    assert calls==[paragraph,'pump','detail','pass','fail']
    calls.clear()
    CheckedTranslator(API(),tmp_path,paragraph_texts=[paragraph]).translate([paragraph,checklist])
    assert calls==[]
