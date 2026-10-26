import copy

import fitz
import pytest

from engine.models import RenderRequest, normalized
from engine.service import render
from engine.typography import prepare


def region(rid, text, boxes, bounds, kind='paragraph'):
    return dict(id=rid, text=text, bbox=bounds, source_bbox=bounds, display_bbox=bounds,
                kind=kind, patch=True, ocr=True, warnings=[], font_size=12,
                dominant_color=0, dominant_flags=0, mask_boxes=copy.deepcopy(boxes),
                spans=[dict(bbox=b, size=12, font='OCR', flags=0, color=0,
                            chars=[], text='Source') for b in boxes])


def source_pdf(path, pages):
    with fitz.open() as pdf:
        for model in pages:
            with fitz.open() as artwork:
                p=artwork.new_page(width=500,height=700)
                p.draw_rect(fitz.Rect(20,130,470,390))
                # Logo is deliberately outside text spans and must remain intact.
                p.draw_rect(fitz.Rect(148,27,167,44),fill=(0,0,0))
                for r in model['regions']:
                    for span in r['spans']:
                        b=span['bbox']
                        p.insert_text((b[0],b[3]-2),'Old',fontsize=9)
                png=p.get_pixmap(dpi=144).tobytes('png')
            p=pdf.new_page(width=500,height=700)
            p.insert_image(p.rect,stream=png)
        pdf.save(path)
    return path


def page(number, regions):
    return dict(number=number,width=500,height=700,regions=regions,warnings=[])


def test_merged_and_separate_repeated_headers_share_text_size_and_alignment(tmp_path):
    company=[175,25,330,45]; title=[70,65,430,85]
    merged=region('merged','Company\nRecord',[company,title],[68,23,432,90])
    pages=[page(0,[merged])]
    replacements={'merged':'Short company\nManufacturing Record'}
    for n in (1,2):
        a=region(f'company{n}','Company',[company],[173,23,332,48])
        b=region(f'title{n}','Record',[title],[68,63,432,90])
        pages.append(page(n,[a,b]))
        replacements.update({a['id']:'Acme Pharmaceuticals Ltd.',b['id']:'Manufacturing Record'})
    source=source_pdf(tmp_path/'source.pdf',pages)
    result=render(source,tmp_path/'out.pdf',{'pages':pages},
                  RenderRequest(mode='fit',replacements=replacements,min_font_size=3,min_scale=.4))
    assert all(r['text_check'] and r['status']!='NEEDS_REVIEW' for r in result['regions'])
    assert result['policy']['replacements']['merged']=='Acme Pharmaceuticals Ltd.\nManufacturing Record'
    assert result['layout_style']['normalized_translations']==1
    with fitz.open(tmp_path/'out.pdf') as out, fitz.open(source) as original:
        sizes=[]
        for p in out:
            words=p.get_text('words')
            company_words=[w for w in words if w[4] in ('Acme','Pharmaceuticals','Ltd.')]
            title_words=[w for w in words if w[4] in ('Manufacturing','Record')]
            assert max(w[3] for w in company_words)<min(w[1] for w in title_words)
            assert abs((company_words[0][0]+company_words[-1][2])/2-252.5)<2
            sizes.append(next(s['size'] for b in p.get_text('dict')['blocks'] if b['type']==0
                              for l in b['lines'] for s in l['spans'] if 'Acme' in s['text']))
            # Ignore resampling antialiasing on the patch's outer pixel boundary.
            logo=fitz.Rect(150,29,165,42)
            assert p.get_pixmap(clip=logo).samples==original[p.number].get_pixmap(clip=logo).samples
        assert max(sizes)-min(sizes)<.02


def test_source_row_gaps_quantity_column_and_internal_padding_survive(tmp_path):
    boxes=[[40,155,135,169],[410,157,450,171],
           [40,245,140,259],[410,247,450,261]]
    r=region('ingredients','Material A...20g\nMaterial B...50g',boxes,[22,132,468,385],'table_cell')
    layout={'pages':[page(0,[r])]}
    source=source_pdf(tmp_path/'source.pdf',layout['pages'])
    result=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
                  replacements={'ingredients':'Ethanol...20g\nPurified Water...50g'},min_font_size=3,min_scale=.4))
    assert result['regions'][0]['text_check']
    with fitz.open(tmp_path/'out.pdf') as pdf:
        words={normalized(w[4]):w for w in pdf[0].get_text('words')}
        assert words['Ethanol'][0]==pytest.approx(40,abs=1)
        assert words['Ethanol'][1]>150
        assert words['Purified'][1]-words['Ethanol'][1]==pytest.approx(90,abs=1)
        assert words['20g'][2]==pytest.approx(words['50g'][2],abs=.1)
        assert words['20g'][2]<=451
        assert normalized('Ethanol20gPurifiedWater50g')==normalized(pdf[0].get_text())


def test_user_added_blank_line_disables_row_anchors_but_keeps_line_break(tmp_path):
    r=region('lines','First\nSecond',[[40,155,130,170],[40,185,130,200]],
             [22,132,468,385],'table_cell')
    layout={'pages':[page(0,[r])]}
    source=source_pdf(tmp_path/'source.pdf',layout['pages'])
    result=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
                  replacements={'lines':'One\n\nTwo'},min_font_size=3,min_scale=.4))
    assert not result['regions'][0]['layout_style']['anchored']
    with fitz.open(tmp_path/'out.pdf') as pdf:
        words={w[4]:w for w in pdf[0].get_text('words')}
        assert words['Two'][1]-words['One'][1]>25


def test_exact_source_only_manual_translation_and_disable_options():
    pages=[page(n,[region(f'r{n}','Company' if n<3 else 'Different Company',
                                [[175,25,330,45]],[173,23,332,48])]) for n in range(4)]
    replacements={'r0':'Majority','r1':'Majority','r2':'Variant','r3':'Keep different'}
    policy=RenderRequest(mode='fit',replacements=replacements,harmonize_repeats=False)
    new,_=prepare({'pages':pages},policy)
    assert new.replacements==replacements
    assert 'repeat_group' not in pages[3]['regions'][0]['typography']['parts'][0]
    policy.harmonize_repeats=True
    policy.manual_regions=['r2']
    new,_=prepare({'pages':pages},policy)
    assert new.replacements==replacements


def test_spacing_off_uses_existing_fitting_path(tmp_path):
    r=region('body','Source',[[40,155,130,170]],[22,132,468,385],'table_cell')
    pages=[page(0,[r])]
    source=source_pdf(tmp_path/'source.pdf',pages)
    result=render(source,tmp_path/'out.pdf',{'pages':pages},RenderRequest(mode='fit',
                  preserve_spacing=False,replacements={'body':'Translated'},min_font_size=3))
    assert result['regions'][0]['text_check']
    assert not result.get('layout_style')
    assert 'typography' not in r


def test_only_process_name_rotates_numbers_and_roman_stage_stay_horizontal(tmp_path):
    header=region('step','Step',[[42,105,70,116]],[40,100,80,123],'table_cell')
    process=region('process','18\n건조\nII',
                   [[50,185,65,197],[52,220,62,231],[52,234,62,245],[53,270,61,282]],
                   [40,150,80,350],'table_cell')
    layout={'pages':[page(0,[header,process])]}
    source=source_pdf(tmp_path/'source.pdf',layout['pages'])
    policy=RenderRequest(mode='fit',replacements={'process':'18\nDrying\nII'},min_font_size=3,min_scale=.4)
    result=render(source,tmp_path/'out.pdf',layout,policy)
    outcome=next(r for r in result['regions'] if r['id']=='process')
    assert outcome['layout_style']['rotated_process'] and outcome['text_check']
    with fitz.open(tmp_path/'out.pdf') as pdf:
        text={''.join(s['text'] for s in line['spans']):line for b in pdf[0].get_text('dict')['blocks']
              if b['type']==0 for line in b['lines']}
        assert text['Drying']['dir']==pytest.approx((0,-1))
        assert text['18']['dir']==text['II']['dir']==(1,0)
        assert min(s['size'] for line in text.values() for s in line['spans'])>=8
        assert text['18']['bbox'][3]<text['Drying']['bbox'][1]<text['II']['bbox'][1]
    # A saved run must not progressively shrink its source geometry.
    first=copy.deepcopy(process['bbox'])
    render(source,tmp_path/'again.pdf',layout,policy)
    assert process['bbox']==first


def test_separate_vertical_ocr_fragment_uses_gap_without_moving_neighbour_numbers(tmp_path):
    header=region('step','Step',[[42,105,70,116]],[40,100,80,123],'table_cell')
    number=region('number','20',[[51,155,65,167]],[50,153,66,169])
    name=region('name','정립',[[53,186,63,196],[53,199,63,210]],[51,184,65,212])
    stage=region('stage','II',[[54,242,61,253]],[52,240,63,255])
    layout={'pages':[page(0,[header,number,name,stage])]}
    source=source_pdf(tmp_path/'source.pdf',layout['pages'])
    result=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
                  replacements={'name':'Granulation'},min_font_size=3,min_scale=.4))
    outcome=next(r for r in result['regions'] if r['id']=='name')
    assert outcome['text_check'] and outcome['status']!='NEEDS_REVIEW'
    assert name['bbox'][1]>number['bbox'][3] and name['bbox'][3]<stage['bbox'][1]
    with fitz.open(source) as before,fitz.open(tmp_path/'out.pdf') as after:
        for r in (number,stage):
            assert before[0].get_pixmap(clip=fitz.Rect(r['bbox'])).samples==after[0].get_pixmap(clip=fitz.Rect(r['bbox'])).samples


def test_long_rotated_process_wraps_and_spacing_off_restores_original_bounds(tmp_path):
    header=region('step','Step',[[42,105,70,116]],[40,100,80,123],'table_cell')
    process=region('process','18\n결합액조제\nII',
                   [[52,175,64,187],[54,200,62,240],[54,255,62,267]],
                   [40,150,80,300],'table_cell')
    original_bounds=copy.deepcopy(process['bbox'])
    layout={'pages':[page(0,[header,process])]}
    source=source_pdf(tmp_path/'source.pdf',layout['pages'])
    policy=RenderRequest(mode='fit',replacements={'process':'18\nBinding Solution Preparation\nII'},
                         min_font_size=3,min_scale=.4)
    result=render(source,tmp_path/'out.pdf',layout,policy)
    outcome=next(r for r in result['regions'] if r['id']=='process')
    assert outcome['text_check'] and outcome['status']!='NEEDS_REVIEW'
    with fitz.open(tmp_path/'out.pdf') as pdf:
        lines=[line for block in pdf[0].get_text('dict')['blocks'] if block['type']==0 for line in block['lines']]
        rotated=[line for line in lines if line['dir']==(0,-1)]
        assert len(rotated)>=2
        assert min(span['size'] for line in rotated for span in line['spans'])>=8
    policy.preserve_spacing=False
    render(source,tmp_path/'spacing-off.pdf',layout,policy)
    assert process['bbox']==original_bounds
    assert 'typography' not in process


def test_tall_body_without_process_column_context_does_not_rotate():
    body=region('body','검토내용',[[50,180,60,190],[50,194,60,204]],
                [40,150,80,350],'table_cell')
    layout={'pages':[page(0,[body])]}
    prepare(layout,RenderRequest(mode='fit',replacements={'body':'Review content'}))
    assert not any(p.get('rotate') for p in body['typography']['parts'])


def test_amount_column_expands_labels_wrap_and_other_rows_keep_readable_size(tmp_path):
    boxes=[[40,160,130,172],[273,160,291,172],
           [40,245,130,257],[250,245,291,257]]
    r=region('rows','항목 하나...2분\n항목 둘...110 RPM',boxes,[25,145,295,350],'table_cell')
    layout={'pages':[page(0,[r])]}
    source=source_pdf(tmp_path/'source.pdf',layout['pages'])
    replacement='Calibrating Screen Installation and Specification Confirmation...2 minutes\nAgitator Speed...110 RPM'
    result=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
                  replacements={'rows':replacement},min_font_size=3,min_scale=.4))
    assert result['regions'][0]['text_check']
    rows=r['typography']['parts']
    assert rows[0]['value_left']<260
    assert rows[0]['font_size']>=8 and rows[1]['font_size']>=10
    with fitz.open(tmp_path/'out.pdf') as pdf:
        words={normalized(w[4]):w for w in pdf[0].get_text('words')}
        assert words['Confirmation'][1]>words['Calibrating'][1]
        assert words['minutes'][1]==pytest.approx(words['Confirmation'][1],abs=.2)
        assert words['minutes'][2]==pytest.approx(words['RPM'][2],abs=.2)
        assert words['Agitator'][1]>words['minutes'][3]+25


def test_unreadable_quantity_is_reported_without_falling_back_to_tiny_type(tmp_path):
    r=region('rows','항목...20g',[[40,160,130,172],[273,160,291,172]],
             [25,145,295,350],'table_cell')
    layout={'pages':[page(0,[r])]}
    source=source_pdf(tmp_path/'source.pdf',layout['pages'])
    result=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
                  replacements={'rows':'Material...'+'9'*600+'g'},min_font_size=3,min_scale=.4))
    assert result['regions'][0]['status']=='NEEDS_REVIEW'
    assert '읽기' in result['regions'][0]['reason']
    assert not result['regions'][0]['layout_style'].get('spacing_relaxed')


def test_compatibility_volume_unit_remains_searchable(tmp_path):
    r=region('flow','유량...1,500~2,800 ㎥/h',[[40,160,130,172],[210,160,291,172]],
             [25,145,295,350],'table_cell')
    layout={'pages':[page(0,[r])]}
    source=source_pdf(tmp_path/'source.pdf',layout['pages'])
    result=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
                  replacements={'flow':'Air Flow...1,500~2,800 ㎥/h'},min_font_size=3,min_scale=.4))
    assert result['regions'][0]['text_check']
    with fitz.open(tmp_path/'out.pdf') as pdf:
        assert 'm³/h' in pdf[0].get_text()
