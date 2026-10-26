"""Recover evidence missing from PDF font flags, without a model call."""
from collections import defaultdict
from statistics import median

import fitz
import numpy as np
import re


def font_weights(page):
    # Some PDF exporters bake synthetic bold into anonymously named glyphs and
    # still mark them Regular. Compare the SAME characters against the dominant
    # normal font, rather than guessing from font names or heading words.
    samples = defaultdict(lambda: defaultdict(list))
    spans = [s for b in page.get_text('rawdict')['blocks'] if b['type'] == 0
             for line in b['lines'] for s in line['spans']]
    fonts = {s['font'] for s in spans}
    if len(fonts) < 2 or not any(re.fullmatch(r'CairoFont-\d+-[01]',name) for name in fonts):
        return {}
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), colorspace=fitz.csGRAY, alpha=False)
    pixels = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    for span in spans:
        if span['flags'] & 16:
            continue
        for char in span['chars']:
            if not char['c'].isalpha():
                continue
            x0, y0, x1, y1 = [round(v * 3) for v in char['bbox']]
            crop = pixels[max(0,y0):min(pix.height,y1), max(0,x0):min(pix.width,x1)]
            if crop.size:
                samples[span['font']][char['c']].append(float((crop < 150).mean()))
    if not samples:
        return {}
    reference = max(samples, key=lambda f: sum(map(len, samples[f].values())))
    base = samples[reference]
    result = {}
    for font, chars in samples.items():
        ratios = [median(values)/median(base[c]) for c,values in chars.items()
                  if c in base and median(base[c]) > .02]
        # High threshold and several shared glyphs avoid confusing a different
        # family (serif/sans) with synthetic bold.
        if len(ratios) >= 3 and median(ratios) > 1.55:
            result[font] = 16
    # Cairo exports Latin and CJK subsets of one face as -0 and -1. Punctuation
    # has too few shared alphabetic glyphs to establish weight independently.
    families = {re.sub(r'-[01]$','',font) for font in result if re.fullmatch(r'CairoFont-\d+-[01]',font)}
    for font in fonts:
        if re.sub(r'-[01]$','',font) in families:
            result[font] = 16
    return result


def restore_weights(page, regions):
    weights = font_weights(page)
    for region in regions:
        for span in region.get('spans', []):
            span['flags'] |= weights.get(span['font'], 0)
    return weights


def prepare_page(page, model, flow=True):
    """Upgrade saved layouts as well as newly analyzed pages."""
    regions = model['regions']
    if flow and model.get('source_style_version') != 1:
        restore_weights(page, regions)
        model['source_style_version'] = 1
    images = [fitz.Rect(i['bbox']) for i in page.get_image_info()]
    from .typography import physical_rows, union
    from .models import normalized
    for region in regions:
        spans = region.get('spans', [])
        native_count=sum(len(s.get('text','')) for s in spans if s.get('font')!='OCR')
        bold_count=sum(len(s.get('text','')) for s in spans if s.get('font')!='OCR' and s.get('flags',0)&16)
        if native_count and bold_count>native_count*.65:
            # User-drawn boxes use the region style rather than per-line spans.
            region['dominant_flags']=region.get('dominant_flags',0)|16
        native = [s for s in spans if s['font'] != 'OCR']
        raster = [s for s in spans if s['font'] == 'OCR']
        if native and raster:
            rows = physical_rows(dict(spans=native))
            native_text = '\n'.join(''.join(s['text'] for s in sorted(row['spans'],key=lambda s:s['bbox'][0])) for row in rows)
            if normalized(native_text) == normalized(region['text']):
                # Old OCR review could retain only the caption but leave diagram
                # label boxes in the erase mask. Limit editing to that caption.
                region['spans'] = native
                ink = union(s['bbox'] for s in native)
                region['source_bbox'] = list(ink)
                region['bbox'] = list(ink + (-.4,-.8,.4,.8))
                region['display_bbox'] = list(region['bbox'])
                region['mask_boxes'] = [s['bbox'] for s in native]
                region['source_mask_boxes'] = list(region['mask_boxes'])
                region.pop('spacing_source_bbox', None)
                region.pop('spacing_source_display_bbox', None)
        elif raster and not native and region.get('kind') == 'table_cell':
            box = fitz.Rect(region['source_bbox'])
            # Previously collapsed infographic transcripts have no trustworthy
            # mapping from translated lines to spatial labels. Never erase them.
            if any(im.width > 120 and im.height > 60 and im.get_area() < page.rect.get_area()*.75
                   and (box & im).get_area() > im.get_area()*.65 for im in images):
                region['spatial_unresolved'] = True
    if not flow:
        return
    from .placement_quality import reserve_spaces
    reserve_spaces(page, model)
