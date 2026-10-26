import fitz

from engine.enhanced import analyze_page, structured_cell_parts
from engine.legacy import CheckedTranslator
from engine.models import RenderRequest
from engine.service import render


def test_explicit_lines_blank_line_indentation_and_html_are_preserved(tmp_path):
    source = tmp_path / 'source.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page(width=350,height=250)
        page.draw_rect(fitz.Rect(20,20,330,230))
        page.insert_text((30,50),'Original',fontsize=12)
        pdf.save(source)
        layout_page = analyze_page(page,'en',use_ocr=False)
    region = layout_page['regions'][0]
    text = 'First line\n    Indented <literal>\n\nLast line'
    report = render(source,tmp_path/'out.pdf',{'pages':[layout_page]},
                    RenderRequest(mode='fit',replacements={region['id']:text},min_scale=.4,min_font_size=3))
    assert report['regions'][0]['status'] != 'NEEDS_REVIEW'
    with fitz.open(tmp_path/'out.pdf') as pdf:
        words = {word[4]:word[:4] for word in pdf[0].get_text('words')}
        assert words['Indented'][0] > words['First'][0]+10
        assert words['Indented'][1] > words['First'][1]
        assert words['Last'][1]-words['Indented'][1] > (words['Indented'][1]-words['First'][1])*1.5
        assert '<literal>' in words


def test_translation_preserves_lines_markers_check_states_and_indent():
    class Translator:
        def translate(self,texts,*args,**kwargs):
            assert all('\n' not in t and '☑' not in t and '□' not in t for t in texts)
            return [t.upper() for t in texts]
    text='1) pump\n    detail\n\n2) mixer  ☑ pass □ fail'
    assert CheckedTranslator(Translator()).translate([text]) == [text.upper()]


def test_cell_list_splits_into_rows_and_check_columns():
    with fitz.open() as pdf:
        page=pdf.new_page(width=400,height=300)
        page.draw_rect(fitz.Rect(20,20,380,280))
        for i,y in enumerate((60,120,180),1):
            page.insert_text((40,y),f'{i}) Equipment',fontsize=10)
            page.insert_text((280,y),'Pass / Fail',fontsize=10)
        model=analyze_page(page,'en',use_ocr=False)
    items=[r for r in model['regions'] if r.get('structure')=='list_item']
    checks=[r for r in model['regions'] if r.get('structure')=='check_column']
    assert len(items)==len(checks)==3
    assert all(a['bbox'][2]<b['bbox'][0] for a,b in zip(items,checks))
    assert [r['text'] for r in items]==['1) Equipment','2) Equipment','3) Equipment']


def test_wrapped_prose_is_not_split_into_list():
    tokens=[{'text':text,'bbox':[20,y,180,y+10],'size':10} for text,y in [('This is prose',20),('continued below',32)]]
    assert structured_cell_parts(tokens,fitz.Rect(10,10,200,60)) is None


def test_scanned_checkbox_ink_is_preserved_while_labels_translate(tmp_path):
    from engine.enhanced import raster, separate_check_marks
    source=tmp_path/'checks.pdf'
    with fitz.open() as artwork:
        page=artwork.new_page(width=220,height=100)
        for x in (40,110): page.draw_rect(fitz.Rect(x,42,x+8,50))
        page.draw_line((40,44),(44,48)); page.draw_line((44,48),(51,38))
        page.insert_text((56,50),'PASS',fontsize=9)
        page.insert_text((126,50),'FAILED',fontsize=9)
        png=page.get_pixmap(dpi=240).tobytes('png')
        with fitz.open() as pdf:
            page=pdf.new_page(width=220,height=100)
            page.insert_image(page.rect,stream=png)
            region={'id':'check','source_bbox':[38,36,170,53],'bbox':[38,36,190,65],
                    'text':'draft','structure':'check_column','kind':'table_cell','patch':True,
                    'ocr':True,'warnings':[],'spans':[{'font':'OCR'}]}
            regions=separate_check_marks([region],page,raster(page))
            for i,r in enumerate(regions): r['id']=f'check-{i}'
            pdf.save(source)
    labels=[r for r in regions if r.get('structure')=='checkbox_label']
    marks=[r for r in regions if r.get('structure')=='checkbox_mark']
    assert len(labels)==len(marks)==2
    for r in labels: r['text']='Label'
    output=tmp_path/'out.pdf'
    report=render(source,output,{'pages':[{'regions':regions,'warnings':[]}]},
                  RenderRequest(mode='fit',replacements={r['id']:'Good' for r in labels},min_font_size=3,min_scale=.4))
    assert all(r['status']!='NEEDS_REVIEW' for r in report['regions']), report
    with fitz.open(source) as before,fitz.open(output) as after:
        for mark in marks:
            box=fitz.Rect(mark['source_bbox'])
            assert before[0].get_pixmap(clip=box,dpi=240).samples==after[0].get_pixmap(clip=box,dpi=240).samples
