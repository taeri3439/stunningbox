import fitz
import numpy as np
from engine.enhanced import analyze_page
from engine.service import render
from engine.models import RenderRequest


def test_rotated_overlay_does_not_poison_native_cell():
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=200)
        page.draw_rect(fitz.Rect(20, 20, 280, 120))
        page.insert_text((40, 70), 'Body', fontsize=12)
        point = fitz.Point(80, 80)
        page.insert_text(point, 'COPY', color=(1, 0, 0), fontsize=20,
                         morph=(point, fitz.Matrix(30)))
        regions = analyze_page(page, 'en', use_ocr=False)['regions']
        body = next(r for r in regions if 'Body' in r['text'])
        protected = [r for r in regions if r['kind'] == 'protected_text']
        assert not body['warnings']
        assert 'COPY' not in body['text']
        assert protected and all(r['keep_original'] and r['overlay_safe'] for r in protected)


def test_raster_body_can_translate_across_red_watermark_without_erasing_it(tmp_path):
    source = tmp_path / 'watermark.pdf'
    with fitz.open() as image_doc:
        page = image_doc.new_page(width=300, height=200)
        page.insert_text((45, 75), 'Original', fontsize=12)
        png = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes('png')
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=200)
        page.insert_image(page.rect, stream=png)
        point = fitz.Point(90, 90)
        page.insert_text(point, 'COPY', color=(1, 0, 0), fontsize=20,
                         morph=(point, fitz.Matrix(30)))
        layout_page = analyze_page(page, 'en', use_ocr=False)
        body = {'id': 'body', 'text': 'Original', 'bbox': [40, 40, 240, 100],
                'source_bbox': [40, 40, 240, 100], 'display_bbox': [40, 40, 240, 100],
                'kind': 'paragraph', 'font_size': 12, 'dominant_color': 0, 'dominant_flags': 0,
                'patch': True, 'ocr': True, 'warnings': [], 'mask_boxes': [[40, 40, 240, 100]],
                'spans': [{'font': 'OCR', 'text': 'Original', 'chars': [], 'flags': 0, 'color': 0, 'size': 12}]}
        layout_page['regions'].append(body)
        doc.save(source)
    report = render(source, tmp_path / 'out.pdf', {'pages': [layout_page], 'region_count': len(layout_page['regions'])},
                    RenderRequest(mode='fit', replacements={'body': 'Translated'}, min_font_size=3, min_scale=.4))
    outcome = next(r for r in report['regions'] if r['id'] == 'body')
    assert outcome['status'] in {'FIT', 'ADJUSTED'}, outcome
    assert outcome['text_check'], 'Retained watermark text must not count as duplicated body text'
    def red_pixels(path):
        with fitz.open(path) as doc:
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            rgb = np.frombuffer(pix.samples, np.uint8).reshape(-1, 3).astype(int)
            return ((rgb[:, 0] - rgb[:, 1] > 80) & (rgb[:, 0] - rgb[:, 2] > 80)).sum()
    assert red_pixels(tmp_path / 'out.pdf') >= red_pixels(source) * .9
    with fitz.open(tmp_path / 'out.pdf') as doc:
        assert 'Translated' in doc[0].get_text()
        assert 'COPY' in doc[0].get_text()
