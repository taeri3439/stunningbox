"""Recover source spacing and repeated header styles without another OCR/model call.

Only exact, spatially corresponding source text shares a style/translation. OCR
disagreements remain separate. Geometry is measured from the original spans.
"""
import copy
import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from statistics import median

import fitz

VERSION = 4


def lines(text):
    return [line for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n') if line.strip()]


def union(boxes):
    result = fitz.Rect()
    for box in boxes:
        result |= fitz.Rect(box)
    return result


def physical_rows(region):
    spans = region['spans']
    # OCR can include a tiny second detection wholly inside a larger word.
    spans = [s for s in spans if not any(
        fitz.Rect(other['bbox']).contains(fitz.Rect(s['bbox'])) and
        fitz.Rect(other['bbox']).height > fitz.Rect(s['bbox']).height * 1.7
        for other in spans if other is not s)]
    rows = []
    for span in sorted(spans, key=lambda s: (s['bbox'][1], s['bbox'][0])):
        box = fitz.Rect(span['bbox'])
        match = next((row for row in reversed(rows)
                      if min(row['box'].y1, box.y1) - max(row['box'].y0, box.y0)
                      >= min(row['box'].height, box.height) * .35), None)
        if match is None:
            rows.append({'box':box, 'spans':[span]})
        else:
            match['box'] |= box
            match['spans'].append(span)
    return rows


def aligned_groups(region, source_lines):
    rows = physical_rows(region)
    if len(rows) == len(source_lines):
        return [[row] for row in rows]
    if len(source_lines) <= 1 or not rows:
        return None
    threshold = median(row['box'].height for row in rows) * .7
    groups = [[rows[0]]]
    for row in rows[1:]:
        if row['box'].y0 - groups[-1][-1]['box'].y1 > threshold:
            groups.append([row])
        else:
            groups[-1].append(row)
    return groups if len(groups) == len(source_lines) else None


def alignment(ink, container, short):
    if not short:
        return 'left'
    left, right = ink.x0 - container.x0, container.x1 - ink.x1
    if min(left, right) > 1 and abs(left - right) < max(4, container.width * .12):
        return 'center'
    if right < max(3, container.width * .08) and left > right + 8:
        return 'right'
    return 'left'


def vertical_process_parts(region, replacement, page):
    """Recognize a stacked CJK process label, not arbitrary tall body text.

    A Step header supplies column context. Separate OCR fragments use only
    verified vacant space between their existing neighbours, never guessed cells.
    """
    box = fitz.Rect(region['bbox'])
    ink = fitz.Rect(region['source_bbox'])
    headers = [r for r in page['regions'] if r['text'].strip().lower() == 'step']
    if not any(h['bbox'][1] < box.y0 and abs((h['bbox'][0]+h['bbox'][2]-ink.x0-ink.x1)/2) < 25
               for h in headers):
        return None
    if box.width > 45 or ink.height < ink.width*1.5:
        return None
    source = unicodedata.normalize('NFKC', region['text']).strip()
    match = re.fullmatch(r'(?:(\d{1,3})\s*)?([가-힣\s]{2,30}?)(?:\s*([IVX]+|\d{1,2}))?', source)
    if not match or len(re.sub(r'\s', '', match[2])) < 2:
        return None
    number, name, stage = match.groups()
    translated = unicodedata.normalize('NFKC', replacement).strip()
    if number:
        start = re.match(re.escape(number)+r'(?=\s|[A-Za-z])\s*', translated)
        if not start:
            return None
        translated = translated[start.end():]
    if stage:
        end = re.search(r'\s+'+re.escape(stage)+r'$', translated)
        if not end:
            return None
        translated = translated[:end.start()]
    if not re.fullmatch(r'[A-Za-z][A-Za-z\s()/,&+\-]{1,100}', translated):
        return None
    size = min(10, max(8, region['font_size']))
    if region['kind'] != 'table_cell':
        # OCR sometimes splits the process number, label and Roman numeral.
        # Leave neighbouring numbers untouched and expand only into empty gaps.
        peers = [fitz.Rect(r['bbox']) for r in page['regions'] if r['id'] != region['id']
                 and not r.get('overlay_safe') and min(r['bbox'][2], ink.x1) > max(r['bbox'][0], ink.x0)]
        above = [r.y1+2 for r in peers if r.y1 <= box.y0]
        below = [r.y0-2 for r in peers if r.y0 >= box.y1]
        box.y0 = max(max(above, default=box.y0), box.y0-size*4)
        box.y1 = min(min(below, default=box.y1), box.y1+size*4)
        if number or stage:
            return None  # A multi-part OCR fragment is not a confirmed table cell.
    # Use the cell's full width for a two-line rotated phrase, trimming only
    # vacant padding that intrudes into a neighbouring region on a skewed scan.
    for other in page['regions']:
        if other['id'] == region['id'] or other.get('overlay_safe'):
            continue
        ob = fitz.Rect(other['bbox'])
        if min(box.y1,ob.y1) <= max(box.y0,ob.y0):
            continue
        if ob.x0 >= ink.x1:
            box.x1 = min(box.x1, ob.x0-1)
        elif ob.x1 <= ink.x0:
            box.x0 = max(box.x0, ob.x1+1)
    if box.width < size*1.15 or box.height < size*3:
        return None
    region['bbox'] = list(box)
    region['display_bbox'] = list(box)
    base = dict(font_size=size, align='center', flags=region.get('dominant_flags',0),
                color=region.get('dominant_color',0), anchored=True, single_line=True,
                center_y=False, process_label=True, readable_floor=8)
    parts = []
    pad = max(3, min(8, box.height*.04)) if region['kind'] == 'table_cell' else .3
    top, bottom = box.y0+pad, box.y1-pad
    band = size*1.2+1
    if number:
        rect = [box.x0+.3, top, box.x1-.3, top+band]
        parts.append(dict(base, source_text=number, text=number, bbox=rect, ink_bbox=rect, rotate=0))
        top += band+5
    if stage:
        bottom -= band+5
    # Original structural line breaks may separate syllables. Preserve new
    # user-entered line breaks; otherwise use one rotated English phrase.
    text = ' '.join(translated.split()) if len(lines(replacement)) == len(lines(region['text'])) else translated
    rect = [box.x0+.3, top, box.x1-.3, bottom]
    parts.append(dict(base, source_text=name.strip(), text=text, bbox=rect,
                      ink_bbox=rect, rotate=90, single_line=False))
    if stage:
        rect = [box.x0+.3, bottom+5, box.x1-.3, bottom+5+band]
        parts.append(dict(base, source_text=stage, text=stage, bbox=rect, ink_bbox=rect, rotate=0))
    return parts


def plan_region(region, replacement, page, manual=False):
    if region.get('user_geometry'):
        return [dict(source_text=region['text'],text=replacement,ink_bbox=region['source_bbox'],
                     bbox=list(region['user_bbox']),font_size=region['font_size'],
                     align=region.get('text_align','left'),flags=region.get('dominant_flags',0),
                     color=region.get('dominant_color',0),anchored=False,single_line=False,center_y=False)]
    if not region.get('spans') or any('bbox' not in s for s in region['spans']):
        return []  # Older layouts without measured span geometry use normal FIT.
    ink=fitz.Rect(region['source_bbox']); box=fitz.Rect(region['bbox'])
    if (not manual and '\n' in region['text'] and re.fullmatch(r'[가-힣\s]{2,20}',region['text'])
            and ink.height>ink.width*3 and box.height>box.width*3
            and re.search(r'[A-Za-z]',replacement)):
        # A vertical Korean label's glyph width estimates its size. OCR often
        # reports the height of the entire vertical word as a single font size.
        size=min(region['font_size'],ink.width*.95)
        text=' '.join(replacement.split())
        return [dict(source_text=region['text'],text=text,ink_bbox=list(ink),
                     bbox=list(box+(.3,.4,-.3,-.4)),font_size=size,align='center',
                     flags=region.get('dominant_flags',0),color=region.get('dominant_color',0),
                     anchored=True,single_line=False,center_y=False,rotate=90,vertical_label=True)]
    vertical = vertical_process_parts(region, replacement, page)
    if vertical:
        return vertical
    source_lines, target_lines = lines(region['text']), lines(replacement)
    groups = aligned_groups(region, source_lines)
    box = fitz.Rect(region['bbox'])
    # Mapping requires both transcripts to agree on logical line count. Extra
    # user-entered newlines remain in the normal flowing-text path, never lost.
    breaks = lambda text: text.replace('\r\n','\n').replace('\r','\n').count('\n')
    anchored = (groups is not None and len(source_lines) == len(target_lines)
                and breaks(region['text']) == breaks(replacement)
                and not (region.get('logical_paragraph') and not manual)
                and not (manual and region['kind'] == 'paragraph' and not region.get('patch')))
    if not anchored:
        groups = [physical_rows(region)]
        source_lines, target_lines = [region['text']], [replacement]
    parts = []
    for index, (source_text, text, rows) in enumerate(zip(source_lines, target_lines, groups)):
        if not rows:
            continue
        ink = union(row['box'] for row in rows)
        short = len(rows) == 1 and '\n' not in text and '\r' not in text
        spans = [s for row in rows for s in row['spans']]
        size = median(s['size'] for s in spans)
        if not any(s['font'] != 'OCR' for s in spans):
            size = median(fitz.Rect(s['bbox']).height for s in spans) * .85
        bottom = min(box.y1 - .3, groups[index+1][0]['box'].y0 - 2) if index+1 < len(groups) else box.y1 - .3
        top = max(box.y0 + .4, ink.y0 - .3)
        align = alignment(ink, box, short) if region['kind'] == 'table_cell' else region.get('text_align','left')
        left = box.x0 + .5 if align != 'left' else max(box.x0 + .3, ink.x0)
        right = box.x1 - .5
        if region['kind'] != 'table_cell' and anchored and not region.get('image_label') and region.get('patch'):
            left, right = max(box.x0+.3,ink.x0), min(box.x1-.3,ink.x1+.5)
        flags = region.get('dominant_flags',0)
        if spans:
            styled = max(spans, key=lambda s: len(s.get('text','')))
            bold_chars = sum(len(s.get('text','')) for s in spans if s.get('flags',0) & 16)
            total_chars = sum(len(s.get('text','')) for s in spans)
            flags = styled.get('flags', flags)
            if bold_chars > total_chars * .65:
                flags |= 16
        part = {'source_text':source_text, 'text':text, 'ink_bbox':list(ink),
                'bbox':[left, top, max(left+1,right), max(top+1,bottom)],
                'font_size':size, 'align':align, 'flags':flags,
                'color':region.get('dominant_color',0), 'anchored':anchored,
                'single_line':False, 'center_y':False}
        part['source_font_size'] = size
        part['role'] = ('heading' if region['kind']=='paragraph' and not region.get('patch')
                        and short and flags & 16 and len(source_text)<120
                        and not source_text.rstrip().endswith(('.', '다.', '함.')) else 'body')
        ordered_spans = sorted(spans, key=lambda s:(round(s['bbox'][1],0),s['bbox'][0]))
        bold_source = ''.join(s.get('text','') for s in ordered_spans if s.get('flags',0)&16)
        # Sentence-level emphasis can be transferred without pretending source
        # character offsets align with another language's word order.
        if not manual and bold_chars and bold_chars < total_chars*.95 and bold_source.rstrip().endswith(('.', '。')):
            sentence = re.match(r'^.*?[.!?](?=\s|$)', text)
            if sentence:
                part['bold_prefix'] = len(sentence[0])
                part['flags'] &= ~16
        # Dotted leaders signal a label/value row, not a paragraph. Draw a new
        # leader between the independently aligned label and value columns.
        leader = re.fullmatch(r'\s*(.+?)(?:\.{2,}|…+)\s*(\S.*?)\s*', text)
        if short and leader and len(spans) >= 2 and re.search(r'\d', leader[2]):
            ordered = sorted(spans,key=lambda s:s['bbox'][0])
            cuts = [(b['bbox'][0]-a['bbox'][2], b['bbox'][0]) for a,b in zip(ordered,ordered[1:])]
            gap, value_left = max(cuts,default=(0,0))
            if gap > max(15,size*2):
                part.update(leader=[leader[1],leader[2]], value_left=value_left, single_line=True,
                            align='left', center_y=True)
                part['bbox'][0] = max(box.x0+.3, ink.x0)
                part['bbox'][2] = min(box.x1-.5, ink.x1)
                # Remove the old dotted leader too; horizontal grid strokes are
                # independently preserved by cleaned_patch's line mask.
                region['mask_boxes'].append(list(ink))
        parts.append(part)
    if (not manual and region['kind'] == 'paragraph' and not region.get('patch')
            and anchored and box.width > 180 and not any(p.get('leader') for p in parts)):
        # Physical wraps are not separate typography units. Reflow continuation
        # lines together, keeping list starts / heading boundaries independent.
        merged = []
        for part in parts:
            starts = re.match(r'^\s*(?:[◦•●▪-]\s|\(?\d+[.)]\s)', part['source_text'])
            heading_end = merged and merged[-1]['flags'] & 16 and not part['flags'] & 16
            if merged and not starts and not heading_end:
                previous = merged[-1]
                previous['text'] += ' ' + part['text'].strip()
                previous['source_text'] += '\n' + part['source_text']
                previous['bbox'][3] = part['bbox'][3]
                previous['ink_bbox'] = list(fitz.Rect(previous['ink_bbox']) | fitz.Rect(part['ink_bbox']))
            else:
                merged.append(part)
        for part in merged:
            part['bbox'][2] = box.x1 - .5
            part['prose'] = True
            part['anchored'] = False
        parts = merged
    if region['kind'] == 'paragraph' and not region.get('patch') and box.width > 180:
        for part in parts:
            part['prose'] = True
            part['bbox'][2] = box.x1 - .5
    if (region.get('image_label') and not manual and anchored and len(parts)>1
            and max(p['font_size'] for p in parts)/min(p['font_size'] for p in parts)<1.3):
        combined = dict(parts[0])
        combined.update(text=' '.join(p['text'].strip() for p in parts),
                        source_text='\n'.join(p['source_text'] for p in parts),
                        bbox=list(union(p['bbox'] for p in parts)),
                        ink_bbox=list(union(p['ink_bbox'] for p in parts)),
                        font_size=median(p['font_size'] for p in parts),anchored=False)
        parts = [combined]
    return parts


def restore_source_geometry(layout):
    """Saved placement metadata must not alter a later run's source geometry."""
    for page in layout['pages']:
        for region in page['regions']:
            if 'spacing_source_bbox' in region:
                region['bbox'] = list(region['spacing_source_bbox'])
                region['display_bbox'] = list(region['spacing_source_display_bbox'])
            if 'source_mask_boxes' in region:
                region['mask_boxes'] = copy.deepcopy(region['source_mask_boxes'])
            region.pop('typography', None)


def prepare(layout, policy):
    """Enrich the caller's in-memory run layout; no changes to source/cache files."""
    restore_source_geometry(layout)
    replacements = dict(policy.replacements)
    groups = defaultdict(list)
    for page in layout['pages']:
        for region in page['regions']:
            if 'source_bbox' not in region or region.get('keep_original') or region.get('warnings'):
                continue
            # Idempotent across saved-run regeneration and manual edits.
            region.setdefault('source_mask_boxes',copy.deepcopy(region.get('mask_boxes',[])))
            region['mask_boxes'] = copy.deepcopy(region['source_mask_boxes'])
            region.setdefault('spacing_source_bbox', list(region['bbox']))
            region.setdefault('spacing_source_display_bbox', list(region['display_bbox']))
            text = replacements.get(region['id'],region['text'])
            literal = region['id'] in policy.manual_regions or region.get('user_linebreak_text') == text
            parts = plan_region(region,text,page,literal)
            region['typography'] = {'version':VERSION,'parts':parts}
            for part in parts:
                ink=fitz.Rect(part['ink_bbox'])
                if (region['id'] in policy.manual_regions or ink.y1 > page['height']*.19
                        or len(part['source_text']) > 150 or len(lines(part['source_text'])) != 1
                        or sum(c.isalpha() for c in part['source_text']) < 3):
                    continue
                key=re.sub(r'\s+','',unicodedata.normalize('NFKC',part['source_text']))
                # Location buckets would split nearly identical scans at a
                # boundary. Clusters instead allow small measured scan shifts.
                candidates=groups[key]
                entry=next((g for g in candidates if abs(g[0][2]-ink.y0/page['height'])<.025
                            and abs(g[0][3]-(ink.x0+ink.x1)/2/page['width'])<.1),None)
                row=(region,part,ink.y0/page['height'],(ink.x0+ink.x1)/2/page['width'],page)
                if entry is None:candidates.append([row])
                else:entry.append(row)
    normalized_count=0
    repeated=[]
    for candidates in groups.values():
        for members in candidates:
            if len({row[4]['number'] for row in members}) < 3:
                continue
            size=median(row[1]['font_size'] for row in members)
            translated=[row[1]['text'].strip() for row in members if row[1]['text'].strip()!=row[1]['source_text'].strip()]
            common_text=Counter(translated).most_common(1)[0][0] if translated else None
            flags=Counter(row[1]['flags'] for row in members).most_common(1)[0][0]
            color=Counter(row[1]['color'] for row in members).most_common(1)[0][0]
            left=median(row[1]['ink_bbox'][0]/row[4]['width'] for row in members)
            group_id=hashlib.sha256((members[0][1]['source_text']+str(members[0][2])).encode()).hexdigest()[:12]
            for region,part,_,_,page in members:
                if policy.harmonize_repeats and common_text and part['text'].strip()!=part['source_text'].strip() and part['text'].strip()!=common_text:
                    part['text']=common_text; normalized_count+=1
                part.update(repeat_group=group_id,font_size=size,flags=flags,color=color,
                            align='center',single_line=True,center_y=True)
                # A broad OCR box sometimes includes the logo to the left of a
                # repeated header. Clip only this outlier against peer text bounds.
                ink=fitz.Rect(part['ink_bbox'])
                if left*page['width']-ink.x0 > ink.height*.7:
                    edge=left*page['width']-1
                    part['bbox'][0]=max(part['bbox'][0],edge)
                    part['ink_bbox'][0]=edge
                    trimmed=[]
                    for mask in region['mask_boxes']:
                        mask=list(mask)
                        if min(mask[3],ink.y1)>max(mask[1],ink.y0):mask[0]=max(mask[0],edge)
                        if mask[2]>mask[0]:trimmed.append(mask)
                    region['mask_boxes']=trimmed
            repeated.append(group_id)
    for page in layout['pages']:
        for region in page['regions']:
            parts=region.get('typography',{}).get('parts',[])
            if not parts:continue
            original=replacements.get(region['id'],region['text'])
            # Preserve CRLF, explicit blank lines and leading spaces verbatim.
            iterator=iter(parts)
            if len(parts)==len(lines(original)):
                normalized=[]
                for piece in re.split(r'(\r\n|\r|\n)',original):
                    normalized.append(next(iterator)['text'] if piece.strip() and piece not in ('\r','\n','\r\n') else piece)
                replacements[region['id']]=''.join(normalized)
    return policy.model_copy(update={'replacements':replacements}), {
        'version':VERSION,'repeat_groups':len(repeated),'normalized_translations':normalized_count,
        'anchored_regions':sum(any(p['anchored'] for p in r.get('typography',{}).get('parts',[]))
                               for page in layout['pages'] for r in page['regions'])}
