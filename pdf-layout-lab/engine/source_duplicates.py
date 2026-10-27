"""Identify two extraction records for the same native/OCR source text.

Require matching text and source geometry, never just table boxes.
Keep both records/translations for inspection; render one physical replacement.
"""
from difflib import SequenceMatcher

import fitz

from .models import normalized


def aliases(model):
    result = {}
    native = [r for r in model['regions'] if not r.get('ocr') and not r.get('keep_original')
              and any(s.get('font') != 'OCR' for s in r.get('spans', []))]
    for r in model['regions']:
        if not r.get('ocr') or r.get('keep_original') or not r.get('text'):
            continue
        text = normalized(r['text'])
        if len(text) < 8:
            continue
        box = fitz.Rect(r.get('source_bbox',r['bbox']))
        matches = []
        for other in native:
            other_box = fitz.Rect(other.get('source_bbox',other['bbox']))
            area = min(box.get_area(),other_box.get_area())
            same_ink = area>0 and (box & other_box).get_area()/area >= .75
            # Older OCR review sometimes transcribed the WHOLE sentence into
            # the leftover number/punctuation record beside native body text.
            # A short original OCR fragment plus the same row/cell and matching
            # transcript establishes one source; adjacent repeated cells do not.
            a,b = fitz.Rect(r['bbox']),fitz.Rect(other['bbox'])
            placement_area = min(a.get_area(),b.get_area())
            fragment = normalized(''.join(s.get('text','') for s in r.get('spans',[])))
            prefix_fragment = (r.get('kind')==other.get('kind')=='table_cell'
                and len(fragment)<len(text)*.35 and placement_area>0
                and (a&b).get_area()/placement_area>=.8
                and 0<=other_box.x0-box.x1<=max(r.get('font_size',0),other.get('font_size',0))*2
                and min(box.y1,other_box.y1)-max(box.y0,other_box.y0)>=box.height*.5)
            if not same_ink and not prefix_fragment:
                continue
            ratio = SequenceMatcher(None,text,normalized(other['text']),autojunk=False).ratio()
            if ratio >= (.75 if prefix_fragment else .86):
                matches.append((ratio,other['id']))
        if matches:
            result[r['id']] = max(matches)[1]
    return result


def edit_groups(layout, ids):
    """An alias and its representative refer to one physical source edit."""
    all_aliases = {k:v for page in layout['pages'] for k,v in aliases(page).items()}
    result = {}
    for region_id in ids:
        owner = all_aliases.get(region_id,region_id)
        result[region_id] = {owner} | {k for k,v in all_aliases.items() if v==owner}
    return result
