import copy

import fitz
import pytest

from engine.enhanced import analyze_page, regroup_prose
from engine.models import RenderRequest, normalized
from engine.service import render
from engine.source_styles import prepare_page


def test_prose_wraps_share_size_and_preserve_bold(tmp_path):
    source = tmp_path/'source.pdf'
    with fitz.open() as pdf:
        p = pdf.new_page(width=500,height=300)
        p.insert_text((40,40),'(1) Heading',fontname='hebo',fontsize=12)
        p.insert_text((40,65),'- First paragraph has a long first physical line',fontsize=12)
        p.insert_text((49,83),'and a short continuation.',fontsize=12)
        p.insert_text((40,115),'- Second paragraph with a separate list marker',fontsize=12)
        p.insert_text((49,133),'and another continuation.',fontsize=12)
        p.insert_text((40,170),'Body text provides the same column width here.',fontsize=12)
        model = analyze_page(p,'en',use_ocr=False)
        pdf.save(source)
    assert any('line\nand' in r['text'] for r in model['regions'])
    heading = next(r for r in model['regions'] if '(1)' in r['text'])
    assert '\n' not in heading['text']
    replacements = {r['id']:r['text'].replace('Heading','Translated heading').replace('First','Changed first').replace('Second','Changed second')
                    for r in model['regions']}
    report = render(source,tmp_path/'out.pdf',{'pages':[model]},RenderRequest(
        mode='fit',replacements=replacements,min_scale=.4,min_font_size=3))
    assert all(r['status']!='NEEDS_REVIEW' for r in report['regions'])
    with fitz.open(tmp_path/'out.pdf') as pdf:
        spans = [s for b in pdf[0].get_text('dict')['blocks'] if b['type']==0 for line in b['lines'] for s in line['spans']]
        changed = [s for s in spans if 'Changed' in s['text'] or 'continuation' in s['text']]
        assert max(s['size'] for s in changed)-min(s['size'] for s in changed) < .03
        assert any(s['flags']&16 and 'Translated' in s['text'] for s in spans)


def test_legacy_caption_cannot_erase_raster_diagram_labels(tmp_path):
    source = tmp_path/'source.pdf'
    with fitz.open() as art:
        ap = art.new_page(width=300,height=150)
        ap.draw_rect((5,5,295,145))
        ap.insert_text((30,45),'Knowledge Sources',fontsize=12)
        ap.insert_text((150,100),'Query 2',fontsize=12)
        png = ap.get_pixmap().tobytes('png')
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=350)
        p.insert_image((50,50,350,200),stream=png)
        p.insert_text((100,230),'Original caption',fontsize=12)
        model=analyze_page(p,'en',use_ocr=False)
        r=next(r for r in model['regions'] if 'caption' in r['text'])
        r.update(kind='table_cell',patch=True,bbox=[50,50,350,240],source_bbox=[50,50,350,240],display_bbox=[50,50,350,240])
        fake=copy.deepcopy(r['spans'][0]);fake.update(font='OCR',text='Knowledge Sources',bbox=[80,80,210,98],chars=[])
        r['spans'].append(fake);r['mask_boxes'].append(fake['bbox'])
        pdf.save(source)
    report=render(source,tmp_path/'out.pdf',{'pages':[model]},RenderRequest(
        mode='fit',replacements={r['id']:'Translated caption'},min_scale=.4,min_font_size=3))
    assert report['regions'][0]['status']!='NEEDS_REVIEW'
    with fitz.open(source) as original,fitz.open(tmp_path/'out.pdf') as output:
        box=fitz.Rect(50,50,350,200)
        assert original[0].get_pixmap(clip=box).samples==output[0].get_pixmap(clip=box).samples
        assert 'Translated caption' in output[0].get_text()


def test_diagram_frame_does_not_collapse_ocr_labels_into_one_cell(tmp_path,monkeypatch):
    import engine.enhanced as enhanced
    with fitz.open() as art:
        p=art.new_page(width=300,height=150)
        p.draw_rect((1,1,299,149))
        p.insert_text((20,40),'Left label')
        p.insert_text((190,40),'Right label')
        png=p.get_pixmap().tobytes('png')
    def tokens(*args):
        return [dict(text=text,bbox=box,origin=[box[0],box[3]],size=10,font='OCR',flags=0,
                     color=0,native=False,block=200000+i,detected=True)
                for i,(text,box) in enumerate([('Left label',[70,78,125,92]),('Right label',[240,78,305,92])])]
    monkeypatch.setattr(enhanced,'image_tokens',tokens)
    monkeypatch.setattr(enhanced,'tessdata',lambda _:('', 'eng'))
    monkeypatch.setattr(fitz.Page,'get_textpage_ocr',lambda self,**kw:self.get_textpage())
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=350)
        p.insert_image((50,50,350,200),stream=png)
        p.insert_text((100,230),'Caption below diagram')
        model=analyze_page(p,'en')
    labels=[r for r in model['regions'] if r['patch']]
    assert len(labels)==2
    assert all(r['kind']=='paragraph' and r['source_bbox'][2]-r['source_bbox'][0]<80 for r in labels)
    assert not fitz.Rect(labels[0]['bbox']).intersects(fitz.Rect(labels[1]['bbox']))
    caption=next(r for r in model['regions'] if 'Caption' in r['text'])
    assert all(s['font']!='OCR' for s in caption['spans'])


def test_prose_space_expansion_is_idempotent_and_stops_at_neighbor(tmp_path):
    with fitz.open() as pdf:
        p=pdf.new_page(width=500,height=300)
        p.insert_text((40,40),'Heading',fontsize=12)
        p.insert_text((40,90),'A sufficiently long body paragraph in this column.',fontsize=12)
        p.insert_text((40,130),'Next paragraph',fontsize=12)
        model=analyze_page(p,'en',use_ocr=False)
        prepare_page(p,model)
        first=[list(r['bbox']) for r in model['regions']]
        prepare_page(p,model)
        assert first==[r['bbox'] for r in model['regions']]
        for i,a in enumerate(model['regions']):
            assert all((fitz.Rect(a['bbox'])&fitz.Rect(b['bbox'])).get_area()<.5 for b in model['regions'][i+1:])


@pytest.mark.parametrize('preserve_spacing',[True,False])
def test_unmapped_legacy_diagram_is_preserved_even_on_manual_apply(tmp_path,preserve_spacing):
    with fitz.open() as art:
        p=art.new_page(width=300,height=150)
        p.insert_text((20,40),'Left label')
        p.insert_text((190,40),'Right label')
        png=p.get_pixmap().tobytes('png')
    source=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=350)
        p.insert_image((50,50,350,200),stream=png)
        pdf.save(source)
    span=dict(font='OCR',text='Left label Right label',bbox=[60,60,340,190],size=10,
              flags=0,color=0,chars=[])
    region=dict(id='merged',text=span['text'],spans=[span],bbox=[50,50,350,200],
                source_bbox=[50,50,350,200],display_bbox=[50,50,350,200],kind='table_cell',
                patch=True,ocr=True,font_size=10,dominant_flags=0,dominant_color=0,
                warnings=[],mask_boxes=[span['bbox']])
    layout=dict(pages=[dict(number=0,width=400,height=350,regions=[region],warnings=[])])
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
        replacements={'merged':'New left label and new right label'},manual_regions=['merged'],
        preserve_spacing=preserve_spacing,min_font_size=3,min_scale=.4))
    assert report['regions'][0]['status']=='NEEDS_REVIEW'
    with fitz.open(source) as original,fitz.open(tmp_path/'out.pdf') as output:
        assert original[0].get_pixmap().samples==output[0].get_pixmap().samples


def test_manual_image_newlines_are_not_reflowed():
    from engine.typography import plan_region
    spans=[dict(font='OCR',text='source',bbox=[30,30+y,200,40+y],size=9,flags=0,color=0)
           for y in (0,14)]
    region=dict(id='label',text='first source line\nsecond source line',spans=spans,
                bbox=[28,28,250,60],source_bbox=[30,30,200,54],kind='paragraph',
                image_label=True,patch=True,font_size=9,dominant_flags=0,dominant_color=0)
    text='Translated first line\nTranslated second line'
    assert len(plan_region(copy.deepcopy(region),text,{'regions':[]},manual=False))==1
    parts=plan_region(copy.deepcopy(region),text,{'regions':[]},manual=True)
    assert len(parts)==2
    assert '\n'.join(p['text'] for p in parts)==text


def test_manual_native_paragraph_uses_whole_box_and_keeps_enter():
    from engine.typography import plan_region
    spans=[dict(font='Helvetica',text='source',bbox=[30,30+y,100,40+y],size=11,
                flags=0,color=0) for y in (0,14)]
    region=dict(id='body',text='short source\nnext source',spans=spans,
                bbox=[28,28,280,100],source_bbox=[30,30,100,54],kind='paragraph',
                patch=False,font_size=11,dominant_flags=0,dominant_color=0)
    text='A longer translated first line\nKeep this explicit second line'
    parts=plan_region(region,text,{'regions':[]},manual=True)
    assert len(parts)==1
    assert parts[0]['text']==text
    assert not parts[0]['anchored']
    assert parts[0]['bbox'][2]>270


def test_centered_short_title_tail_merges_but_subtitle_does_not():
    with fitz.open() as pdf:
        p=pdf.new_page(width=500,height=300)
        p.insert_text((160,45),'Separate subtitle',fontsize=12)
        p.insert_text((70,95),'Automatic multilingual platform',fontsize=22)
        center=70+fitz.get_text_length('Automatic multilingual platform',fontsize=22)/2
        p.insert_text((center-fitz.get_text_length('Build',fontsize=22)/2,120),'Build',fontsize=22)
        model=analyze_page(p,'en',use_ocr=False)
    title=next(r for r in model['regions'] if 'Automatic' in r['text'])
    assert 'Build' in title['text']
    assert title['text_align']=='center'
    assert 'subtitle' not in title['text']


def test_short_native_line_uses_available_width_in_typesetter():
    from engine.typography import plan_region
    with fitz.open() as pdf:
        p=pdf.new_page(width=500,height=300)
        p.insert_text((40,40),'Short red line',fontsize=12,color=(1,0,0))
        p.insert_text((40,100),'A long body paragraph establishing the right column edge.',fontsize=12)
        model=analyze_page(p,'en',use_ocr=False)
        prepare_page(p,model)
        r=next(r for r in model['regions'] if 'red' in r['text'])
        parts=plan_region(r,'A longer translated sentence using free space.',model)
        assert r['bbox'][2]>r['source_bbox'][2]+100
        assert parts[0]['bbox'][2]>r['source_bbox'][2]+100


def test_expansion_avoids_adjacent_table_font_descent():
    with fitz.open() as pdf:
        p=pdf.new_page(width=500,height=300)
        p.insert_text((40,60),'Short line',fontsize=12)
        p.insert_text((40,110),'Long paragraph establishing a safe column boundary.',fontsize=12)
        model=analyze_page(p,'en',use_ocr=False)
        r=next(r for r in model['regions'] if 'Short' in r['text'])
        y=r['bbox'][1]
        obstacle=dict(id='cell',kind='table_cell',bbox=[40,y-15,400,y+2],
                      source_bbox=[40,y-15,400,y+2],spans=[],font_size=12)
        model['regions'].append(obstacle)
        prepare_page(p,model)
        assert r['bbox'][1]>=obstacle['bbox'][3]
