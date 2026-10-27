"""Cross-document contracts: real PDF geometry, content, objects and failure paths."""
import hashlib

import fitz
import pytest

from engine import enhanced
from engine.enhanced import analyze_page
from engine.geometry import display_rect
from engine.inspect import inspect_pdf
from engine.models import RenderRequest, normalized
from engine.native_refinement import refine
from engine.service import render
from engine.validate import validate


def fixture_pdf(path, *, rotation=0, crop=False, pages=1, variant='body'):
    """Small reproducible documents, independent of uploaded files and API keys."""
    with fitz.open() as doc:
        for index in range(pages):
            width, height = [(420, 595), (612, 792), (700, 440)][index % 3]
            p = doc.new_page(width=width, height=height)
            p.insert_text((35, 42), f'Chapter {index + 1}', fontsize=16, fontname='hebo')
            p.insert_text((35, 76), 'Source paragraph', fontsize=11)
            p.insert_text((35, height-30), f'Page {index + 1}', fontsize=8)
            if variant == 'table':
                p.draw_rect((35, 130, width-35, 280), fill=(.95, .96, 1))
                p.draw_line((35, 180), (width-35, 180))
                p.draw_line((width/2, 180), (width/2, 280))
                p.insert_text((45, 160), 'Merged header', fontsize=12, fontname='hebo')
                p.insert_text((45, 210), 'First cell', fontsize=10)
                p.insert_text((45, 226), 'A continuation inside the cell', fontsize=10)
                p.insert_text((width/2+10, 210), 'Other cell', fontsize=10)
            elif variant == 'columns':
                for x in (35, width/2+12):
                    p.insert_text((x, 160), 'Column heading', fontsize=12, fontname='hebo')
                    p.insert_text((x, 185), 'First paragraph stays here.', fontsize=10)
                    p.insert_text((x, 218), '1. Numbered item', fontsize=10)
                    p.insert_text((x+12, 238), '(a) Nested item', fontsize=10)
                    p.insert_text((x+12, 256), '- Subordinate item', fontsize=10)
                p.draw_line((width/2, 145), (width/2, 280))
                p.insert_text((35, height-55), '1. Footnote remains on its page.', fontsize=8, fontname='tiit')
            elif variant == 'artwork':
                p.draw_rect((35, 130, 180, 225), fill=(.9, .95, .9))
                p.insert_text((45, 160), 'Text box', fontsize=11, fontname='tiit', color=(.2,.3,.4))
                for n, bar_height in enumerate((30, 60, 40)):
                    p.draw_rect((225+n*30, 240-bar_height, 245+n*30, 240), fill=(.2,.4,.7))
                p.draw_line((215, 242), (330, 242))
                p.insert_text((215, 265), 'Figure 1. Results', fontsize=9)
                pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0,0,25,25), False)
                pix.clear_with(100)
                p.insert_image((40, 300, 95, 355), pixmap=pix)
            elif variant == 'styles':
                for n, font in enumerate(('tiit', 'tibo', 'cour', 'cobi', 'heit')):
                    p.insert_text((35, 150+n*28), f'Styled line {n}', fontsize=10+n, fontname=font, color=(.2,.3,.6))
            if crop:
                p.set_cropbox(fitz.Rect(10, 10, width-10, height-10))
            p.set_rotation(rotation)
        doc.set_toc([[1, f'Chapter {n+1}', n+1] for n in range(pages)])
        doc.set_page_labels([{'startpage': 0, 'prefix': 'S-', 'style': 'D', 'firstpagenum': 1}])
        doc.set_metadata({'title': 'Independent layout fixture', 'author': 'Regression suite'})
        doc.save(path)
    return path


def analyze(path):
    with fitz.open(path) as doc:
        layout = {'pages': [analyze_page(p, 'en', use_ocr=False) for p in doc]}
    return refine(layout, path)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('crop', [False, True])
def test_rotated_cropped_native_geometry_and_untouched_artwork(tmp_path, rotation, crop):
    source = fixture_pdf(tmp_path/'input.pdf', rotation=rotation, crop=crop, variant='table')
    digest = hashlib.sha256(source.read_bytes()).digest()
    layout = analyze(source)
    r = next(r for r in layout['pages'][0]['regions'] if r['text'] == 'Source paragraph')
    report = render(source, tmp_path/'output.pdf', layout,
                    RenderRequest(mode='fit', replacements={r['id']: 'Translated paragraph with extra words'}, min_font_size=8))
    result = next(s for s in report['regions'] if s['id'] == r['id'])
    assert result['status'] in ('FIT', 'ADJUSTED'), result
    assert result['placement_checks']['within_bounds'] and result['text_check']
    assert report['validation']['geometry_ok'] and report['validation']['structure_ok'], report
    assert not report['validation']['pages'][0]['issues'], report['validation']
    assert r['display_bbox'] == pytest.approx(display_rect(layout['pages'][0], r['bbox']))
    assert hashlib.sha256(source.read_bytes()).digest() == digest


@pytest.mark.parametrize('variant', ['body', 'table', 'columns', 'artwork', 'styles'])
def test_diverse_structures_and_mixed_page_sizes(tmp_path, variant):
    source = fixture_pdf(tmp_path/'input.pdf', pages=3, variant=variant)
    layout = analyze(source)
    replacements = {r['id']: 'Short' if p['number']%2 else 'A longer translated paragraph with ordinary word wrapping.'
                    for p in layout['pages'] for r in p['regions'] if r['text'] == 'Source paragraph'}
    report = render(source, tmp_path/'output.pdf', layout, RenderRequest(mode='fit', replacements=replacements))
    assert report['validation']['geometry_ok'] and report['validation']['structure_ok'], report
    assert not any(p['issues'] for p in report['validation']['pages']), report['validation']
    assert all(r['text_check'] for r in report['regions'])
    assert report['validation']['translated'] == 3


def test_many_pages_keep_order_headers_footers_and_bookmarks(tmp_path):
    source = fixture_pdf(tmp_path/'many.pdf', pages=24)
    layout = analyze(source)
    replacements = {p['regions'][0]['id']: f'Translated chapter {i+1}' for i,p in enumerate(layout['pages'])}
    report = render(source, tmp_path/'out.pdf', layout, RenderRequest(mode='fit', replacements=replacements))
    assert report['validation']['geometry_ok'] and report['validation']['structure_ok']
    with fitz.open(tmp_path/'out.pdf') as result:
        assert len(result) == 24
        for i, p in enumerate(result):
            assert f'Translated chapter {i+1}' in p.get_text()
            assert f'Page {i+1}' in p.get_text()


def test_rotated_scan_uses_unrotated_ocr_crops_and_preserves_rotation(tmp_path):
    from engine.image_ocr import raster_page
    source = tmp_path/'scan.pdf'
    with fitz.open() as text, fitz.open() as pdf:
        page = text.new_page(width=320, height=200)
        page.insert_text((35, 75), 'Original scanned text', fontsize=16)
        p = pdf.new_page(width=320, height=200)
        p.insert_image(p.rect, stream=page.get_pixmap(dpi=180).tobytes('png'))
        p.set_rotation(90)
        pdf.save(source)
    image, origin = raster_page(source, 0, None, dpi=72)
    assert image.size == (320, 200) and origin == (0, 0)
    with fitz.open(source) as doc:
        model = analyze_page(doc[0], 'en')
        assert doc[0].rotation == 90
    assert model['analysis']['kind'] == 'scan'
    assert any(r['ocr'] for r in model['regions'])
    report = render(source, tmp_path/'out.pdf', {'pages': [model]}, RenderRequest(mode='fit',
        replacements={r['id']: 'Translated scan' for r in model['regions']}, min_scale=.6, min_font_size=8))
    assert report['validation']['skipped'] == 0, report
    assert report['validation']['geometry_ok'] and report['validation']['structure_ok']
    assert not report['validation']['pages'][0]['issues'], report['validation']


def test_ocr_failure_is_page_diagnostic_and_does_not_abort_native_content(tmp_path, monkeypatch):
    source = fixture_pdf(tmp_path/'mixed.pdf', pages=2, variant='artwork', rotation=270)
    def unavailable(*args):
        raise RuntimeError('OCR engine unavailable')
    monkeypatch.setattr(enhanced, 'ocr_words', unavailable)
    monkeypatch.setattr(enhanced, 'image_tokens', unavailable)
    with fitz.open(source) as doc:
        models = [analyze_page(p, 'en') for p in doc]
        assert all(p.rotation == 270 for p in doc)
    assert all(p['analysis']['diagnostics'] and p['warnings'] for p in models)
    report = render(source, tmp_path/'out.pdf', {'pages': models}, RenderRequest(mode='fit'))
    assert report['validation']['status'] == 'NEEDS_REVIEW'
    assert report['validation']['structure_ok']


def test_incomplete_scratch_placement_never_erases_source(tmp_path, monkeypatch):
    from engine import service
    source = fixture_pdf(tmp_path/'input.pdf')
    layout = inspect_pdf(source)
    r = layout['pages'][0]['regions'][0]
    def incomplete(page, *args):
        page.insert_text((35, 42), 'Only a fragment')
        return 1
    monkeypatch.setattr(service, 'fit', incomplete)
    report = render(source, tmp_path/'out.pdf', layout, RenderRequest(mode='fit', replacements={r['id']: 'Complete translation'}))
    assert report['regions'][0]['status'] == 'NEEDS_REVIEW'
    with fitz.open(source) as before, fitz.open(tmp_path/'out.pdf') as after:
        assert before[0].get_pixmap().samples == after[0].get_pixmap().samples


@pytest.mark.parametrize('manual', [False, True])
def test_small_redaction_overlap_never_deletes_neighbor(tmp_path, manual):
    source = fixture_pdf(tmp_path/'input.pdf')
    layout = inspect_pdf(source)
    first, neighbor = layout['pages'][0]['regions'][:2]
    first['source_bbox'] = list(first['bbox'])
    first['source_bbox'][3] = neighbor['spans'][0]['chars'][0]['bbox'][1]+.2
    report = render(source, tmp_path/'out.pdf', layout, RenderRequest(mode='fit',
        replacements={first['id']: 'Changed'}, manual_regions=[first['id']] if manual else []))
    assert report['regions'][0]['applied'] and report['regions'][0]['text_check']
    assert report['regions'][0]['source_deletion']=='exclusive_glyph_interiors'
    with fitz.open(tmp_path/'out.pdf') as doc:
        assert 'Source paragraph' in doc[0].get_text() and 'Changed' in doc[0].get_text()
        assert 'Chapter 1' not in doc[0].get_text()


def structural_pdf(path):
    with fitz.open() as doc:
        p = doc.new_page(width=300, height=250)
        p.insert_text((30, 40), 'Keep unchanged', fontsize=12)
        p.insert_link({'kind': fitz.LINK_URI, 'from': fitz.Rect(30, 75, 130, 90), 'uri': 'https://example.com/a'})
        p.add_text_annot((240, 30), 'Original annotation')
        widget = fitz.Widget()
        widget.field_name = 'Reference'
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_value = 'Original value'
        widget.rect = fitz.Rect(30, 140, 150, 165)
        p.add_widget(widget)
        doc.set_toc([[1, 'Original bookmark', 1]])
        doc.save(path)
    return path


@pytest.mark.parametrize('damage', ['link', 'annotation', 'widget', 'outline', 'text', 'page_size', 'page_count'])
def test_validator_catches_corruption_even_when_object_counts_match(tmp_path, damage):
    source = structural_pdf(tmp_path/'source.pdf')
    layout = inspect_pdf(source)
    results = {r['id']: dict(id=r['id'], page=0, text=r['text'], status='UNCHANGED') for r in layout['pages'][0]['regions']}
    with fitz.open(source) as doc:
        p = doc[0]
        if damage == 'link':
            link = p.get_links()[0]; link['uri'] = 'https://example.com/b'; p.update_link(link)
        elif damage == 'annotation':
            annot = next(p.annots()); annot.set_info(content='Changed annotation'); annot.update()
        elif damage == 'widget':
            widget = next(p.widgets()); widget.field_value = 'Changed value'; widget.update()
        elif damage == 'outline':
            doc.set_toc([[1, 'Wrong bookmark', 1]])
        elif damage == 'text':
            p.add_redact_annot((25,20,180,50), fill=False); p.apply_redactions(images=0, graphics=0)
        elif damage == 'page_size':
            p.set_cropbox((0,0,270,220))
        else:
            doc.new_page()
        doc.save(tmp_path/'damaged.pdf')
    result = validate(source, tmp_path/'damaged.pdf', layout, results)
    assert result['status'] == 'NEEDS_REVIEW', result
    if damage in ('link', 'annotation', 'widget', 'outline'):
        assert not result['structure_ok']
    elif damage == 'text':
        assert not results[layout['pages'][0]['regions'][0]['id']]['text_check']


def test_missing_output_page_is_reported_instead_of_index_error(tmp_path):
    source = fixture_pdf(tmp_path/'input.pdf', pages=2)
    layout = inspect_pdf(source)
    with fitz.open(source) as doc:
        doc.delete_page(1)
        doc.save(tmp_path/'out.pdf')
    results = {r['id']: dict(text=r['text'], status='UNCHANGED') for p in layout['pages'] for r in p['regions']}
    result = validate(source, tmp_path/'out.pdf', layout, results)
    assert not result['geometry_ok'] and result['pages'][1]['issues']


@pytest.mark.parametrize('header_text', ['Step', 'Phase', 'Column Z', '단계'])
def test_stacked_label_layout_does_not_depend_on_header_word(header_text):
    from test_typography import region, page
    from engine.typography import prepare
    header = region('header', header_text, [[42,105,70,116]], [40,100,80,123], 'table_cell')
    body = region('body', '18\n건조\nII', [[50,185,65,197],[52,220,62,231],[52,234,62,245],[53,270,61,282]],
                  [40,150,80,350], 'table_cell')
    prepare({'pages': [page(0,[header,body])]}, RenderRequest(mode='fit', replacements={'body': '18\nDrying\nII'}))
    assert any(part.get('rotate') == 90 for part in body['typography']['parts'])


def test_font_family_and_italic_survive_flow_without_spacing(tmp_path):
    source = tmp_path/'styles.pdf'
    with fitz.open() as doc:
        p = doc.new_page(width=400, height=250)
        p.insert_text((35,60), 'Serif italic text', fontname='tiit', fontsize=12, color=(.2,.3,.6))
        doc.save(source)
    layout = analyze(source)
    r = layout['pages'][0]['regions'][0]
    report = render(source, tmp_path/'out.pdf', layout, RenderRequest(mode='fit', preserve_spacing=False,
        replacements={r['id']: 'New italic text'}, min_scale=.7))
    assert report['regions'][0]['status'] != 'NEEDS_REVIEW'
    with fitz.open(tmp_path/'out.pdf') as doc:
        spans = [s for b in doc[0].get_text('dict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans']]
        assert all(s['flags'] & 2 and s['flags'] & 4 for s in spans)


def test_korean_to_longer_english_preserves_page_and_figure(tmp_path):
    source = tmp_path/'korean.pdf'
    with fitz.open() as doc:
        p = doc.new_page(width=420,height=595)
        p.insert_text((35,60), '문서 번역 결과를 확인합니다.', fontname='korea', fontsize=12)
        p.draw_rect((35,200,385,350), fill=(.9,.95,1))
        doc.save(source)
    layout = analyze(source)
    r = layout['pages'][0]['regions'][0]
    text = 'Review the translated document and verify that its original visual structure is preserved.'
    report = render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',replacements={r['id']:text}))
    assert report['regions'][0]['text_check'] and report['regions'][0]['status'] != 'NEEDS_REVIEW'
    assert report['validation']['structure_ok']


def test_existing_redaction_cannot_be_executed_by_manual_override(tmp_path):
    source = tmp_path/'redactions.pdf'
    with fitz.open() as doc:
        p = doc.new_page(width=400,height=250)
        p.insert_text((35,45),'Selected text')
        p.insert_text((35,145),'Unrelated retained text')
        p.add_redact_annot((30,125,200,150))
        doc.save(source)
    layout = inspect_pdf(source); r=layout['pages'][0]['regions'][0]
    report = render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
        replacements={r['id']:'Translated'},manual_regions=[r['id']]))
    assert report['regions'][0]['reason_code'] == 'existing_redaction'
    with fitz.open(tmp_path/'out.pdf') as doc:
        assert 'Unrelated retained text' in doc[0].get_text()
        assert len(list(doc[0].annots())) == 1


def test_source_ownership_guard_covers_text_missing_from_layout(tmp_path):
    source=fixture_pdf(tmp_path/'source.pdf')
    layout=inspect_pdf(source)
    first, omitted=layout['pages'][0]['regions'][:2]
    first['source_bbox'] = list(first['bbox'])
    first['source_bbox'][3] = omitted['bbox'][3]
    layout['pages'][0]['regions'].remove(omitted)
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',replacements={first['id']:'New'}))
    assert report['regions'][0]['applied'] and report['regions'][0]['text_check']
    with fitz.open(tmp_path/'out.pdf') as doc:
        assert 'Source paragraph' in doc[0].get_text()


def test_preflight_detects_overflow_and_neighbor_overlap():
    from engine.validate import check_placement
    with fitz.open() as doc:
        p=doc.new_page(width=300,height=200)
        p.insert_text((40,60),'Visible text',fontsize=12)
        with pytest.raises(ValueError, match='경계'):
            check_placement(p, {'bbox':[40,45,60,75]}, 'Visible text')
        with pytest.raises(ValueError, match='겹칩니다'):
            check_placement(p, {'bbox':[30,35,200,90]}, 'Visible text', [fitz.Rect(45,48,100,64)])


def test_rotation_is_restored_after_analyzer_error(monkeypatch):
    def fail(*args):raise RuntimeError('Failed analysis')
    monkeypatch.setattr(enhanced,'_analyze_page',fail)
    with fitz.open() as doc:
        p=doc.new_page();p.set_rotation(90)
        with pytest.raises(RuntimeError):analyze_page(p,'en')
        assert p.rotation==90


def test_two_manual_placements_cannot_collide_in_shared_whitespace(tmp_path):
    source=tmp_path/'source.pdf'
    with fitz.open() as doc:
        p=doc.new_page(width=320,height=250)
        p.insert_text((35,45),'First source',fontsize=11)
        p.insert_text((35,200),'Second source',fontsize=11)
        doc.save(source)
    layout=analyze(source);regions=layout['pages'][0]['regions']
    for r in regions:
        r.update(user_geometry=True,user_bbox=[30,90,280,160],bbox=[30,90,280,160])
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
        replacements={r['id']:f'Translation {n}' for n,r in enumerate(regions)},
        manual_regions=[r['id'] for r in regions]))
    assert report['regions'][0]['status'] in ('FIT','ADJUSTED')
    assert report['regions'][1]['applied'] and report['regions'][1]['review_required']
    assert all(r['text_check'] for r in report['regions'])
    with fitz.open(tmp_path/'out.pdf') as doc:
        assert 'Translation 0' in doc[0].get_text()
        assert 'Translation 1' in doc[0].get_text()
        glyphs=[c for s in doc[0].get_texttrace() for c in s['chars'] if chr(c[0]).strip()]
        assert not any((fitz.Rect(a[3]) & fitz.Rect(b[3])).get_area()>.05
                       for i,a in enumerate(glyphs) for b in glyphs[i+1:])


def test_retained_neighbor_at_cell_boundary_is_not_counted_as_translation(tmp_path):
    source=tmp_path/'boundary.pdf'
    with fitz.open() as doc:
        p=doc.new_page(width=320,height=250)
        p.insert_text((35,150),'Selected source',fontsize=11)
        p.insert_text((180,100),'Neighbor',fontsize=11)
        doc.save(source)
    layout=analyze(source)
    r=next(r for r in layout['pages'][0]['regions'] if r['text']=='Selected source')
    r.update(user_geometry=True,user_bbox=[30,80,180.3,170],bbox=[30,80,180.3,170])
    report=render(source,tmp_path/'out.pdf',layout,RenderRequest(mode='fit',
        replacements={r['id']:'New text'},manual_regions=[r['id']]))
    result=next(x for x in report['regions'] if x['id']==r['id'])
    assert result['status'] in ('FIT','ADJUSTED') and result['text_check'], result
    with fitz.open(tmp_path/'out.pdf') as doc:
        assert 'Neighbor' in doc[0].get_text()


def test_old_validation_report_is_not_reused_for_new_checks(tmp_path):
    source=tmp_path/'source.pdf'
    with fitz.open() as doc:
        for n in range(2):doc.new_page(width=320,height=250).insert_text((35,60),f'Page {n}')
        doc.save(source)
    layout=inspect_pdf(source)
    policy=RenderRequest(mode='fit')
    previous=render(source,tmp_path/'previous.pdf',layout,policy)
    previous['validation'].pop('version')
    report=render(source,tmp_path/'new.pdf',layout,policy,previous_pdf=tmp_path/'previous.pdf',
                  previous_report=previous,changed_pages={0})
    assert not report['render_scope']['incremental']
    assert report['render_scope']['rendered_pages']==[0,1]
    from engine.validate import VERSION
    assert report['validation']['version']==VERSION
