"""Bounded local recovery after a capacity failure. Never rewrite translations.

Source ink/masks and translation units are immutable here. Only placement and
typography may change, and only after a fresh scratch render passes checks.
"""
import copy

import fitz

from .models import normalized
from .typesetter import fit, render_units


CAPACITY_REASONS = ('공간', '넣을 수', '들어가지', '여백', '글자 크기')


def obstacles(page, page_info, region):
    from .placement_quality import graphics
    return [fitz.Rect(r['bbox']) for r in page_info['regions'] if r['id'] != region['id']] + graphics(page)


def available_box(page, page_info, region):
    old = fitz.Rect(region['bbox'])
    # Raster labels need image-specific empty-ink detection; tables already own
    # their full cell. User-drawn boxes are an explicit placement constraint.
    if region.get('patch') or region.get('user_geometry'):
        return old
    if region['kind']=='table_cell':
        ink=fitz.Rect(region['source_bbox'])
        cells=[fitz.Rect(c) for t in page.find_tables().tables for c in t.cells if c]
        cell=min((c for c in cells if (c+(-.5,-.5,.5,.5)).contains(ink)),key=lambda c:c.get_area(),default=None)
        if cell is None:return old
        candidate=(cell+(.35,.35,-.35,-.35)) | old
        # Do not reclaim a neighboring list item's whitespace inside a cell.
        for other in page_info['regions']:
            if other['id']==region['id']:continue
            b=fitz.Rect(other['bbox'])
            if not candidate.intersects(b):continue
            if b.y1<=old.y0+.1:candidate.y0=max(candidate.y0,b.y1)
            elif b.y0>=old.y1-.1:candidate.y1=min(candidate.y1,b.y0)
            elif b.x1<=old.x0+.1:candidate.x0=max(candidate.x0,b.x1)
            elif b.x0>=old.x1-.1:candidate.x1=min(candidate.x1,b.x0)
            else:return old
        if not (cell+(-.5,-.5,.5,.5)).contains(candidate):return old
        return candidate
    if region['kind']!='paragraph':return old
    peers = [fitz.Rect(r['source_bbox']) for r in page_info['regions']
             if r.get('kind') == 'paragraph' and not r.get('patch') and r.get('source_bbox')
             and abs(r['source_bbox'][0]-old.x0) < max(18,region['font_size']*2)]
    edge = max([old.x1]+[b.x1 for b in peers])
    size = region['font_size']
    right = min(page.rect.width-8, edge, old.x1+max(48,old.width*.35))
    bottom = min(page.rect.height-8, old.y1+min(36,size*3))
    barriers = obstacles(page,page_info,region)
    # Two sequential axes: reserve width first, then the entire expanded width
    # when looking below, so a diagonal neighbor can never be covered.
    for b in barriers:
        if b.x0 >= old.x1-.1 and min(b.y1,old.y1)>max(b.y0,old.y0):
            right = min(right,b.x0-1)
    candidate = fitz.Rect(old.x0,old.y0,max(old.x1,right),old.y1)
    for b in barriers:
        if b.y0 >= old.y1-.1 and min(b.x1,candidate.x1)>max(b.x0,candidate.x0):
            bottom = min(bottom,b.y0-1)
    candidate.y1 = max(old.y1,bottom)
    # Reject newly crossed artwork even if it begins inside the old box.
    for b in barriers:
        if (candidate & b).get_area() > (old & b).get_area()+.1:
            return old
    return candidate


def recover(page, page_info, region, text, policy, reason):
    audit = dict(status='unresolved', initial_reason=str(reason), attempts=[])
    if not any(word in str(reason) for word in CAPACITY_REASONS):
        return None, audit
    parts = region.get('typography',{}).get('parts',[])
    if (not policy.preserve_spacing or not parts or region.get('keep_original')
            or region.get('spatial_unresolved') or region.get('kind') == 'protected_text'
            or any(p.get('repeat_group') or p.get('rotate') or p.get('adaptive_leader') for p in parts)):
        audit['stop_reason'] = '보호 영역 또는 별도 구조의 배치 규칙을 유지했습니다.'
        return None, audit
    old_box = fitz.Rect(region['bbox'])
    grown = available_box(page,page_info,region) if len(parts)==1 else old_box
    steps = [('padding',old_box,policy.line_height),
             ('line_spacing',old_box,min(policy.line_height,1.05)),
             ('word_breaks',old_box,min(policy.line_height,1.05))]
    if grown != old_box:
        steps.append(('available_space',grown,min(policy.line_height,1.05)))
    if region['kind']=='table_cell' and not any(p.get('prose') for p in parts) and policy.line_height>1:
        steps.append(('compact_cell',grown,1.0))
    for name,box,line_height in steps:
        candidate = copy.deepcopy(region)
        candidate['bbox'] = list(box)
        candidate['display_bbox'] = list(box)
        candidate['typography'].pop('spacing_relaxed',None)
        trial_parts = candidate['typography']['parts']
        for index,part in enumerate(trial_parts):
            part.pop('layout_error',None)
            part.pop('fixed_size',None)
            # Preserve the user's newlines, list blocks, indents and emphasis.
            # Move only the outer top/bottom padding of an intact text block.
            part['bbox'][1] = max(box.y0+.1, part['bbox'][1]-(part['bbox'][1]-box.y0)*.65) if index==0 else part['bbox'][1]
            if name in ('available_space','compact_cell') and region['kind']=='table_cell':
                part['bbox'][0]=box.x0+.15
                if index==0:part['bbox'][1]=box.y0+.15
            if index==len(trial_parts)-1: part['bbox'][3]=box.y1-.1
            part['bbox'][2] = max(part['bbox'][2],box.x1-.2)
            part['line_height'] = line_height
            if name in ('word_breaks','available_space','compact_cell') and not part.get('bold_prefix'):
                part['wrap_long_words']=True
            part['readable_floor'] = max(policy.min_font_size,part.get('readable_floor',0),part['font_size']*(.7 if part.get('prose') else .6))
        entry = dict(step=name,bbox=list(box),line_height=line_height)
        audit['attempts'].append(entry)
        if any((box & fitz.Rect(r['bbox'])).get_area()>.5 for r in page_info['regions'] if r['id'] != region['id']):
            entry['result']='neighbor_collision'
            continue
        try:
            with fitz.open() as scratch:
                probe=scratch.new_page(width=page.rect.width,height=page.rect.height)
                scale=fit(probe,candidate,text,policy)
                expected=''.join(render_units(p['text']) for p in trial_parts)
                if normalized(probe.get_text()) != normalized(expected):
                    raise ValueError('조정 후 글자 누락 또는 순서 불일치')
                chars=[c for b in probe.get_text('rawdict')['blocks'] if b['type']==0
                       for line in b['lines'] for s in line['spans'] for c in s['chars'] if c['c'].strip()]
                if any(not box.contains(fitz.Point(c['origin'])) for c in chars):
                    raise ValueError('조정 후 배치 경계 이탈')
            entry['result']='passed'
            audit.update(status='adjusted',before_bbox=list(old_box),after_bbox=list(box),
                         font_size=region['font_size']*scale)
            # Only commit an accepted placement; failed attempts leave no state.
            region.update(bbox=candidate['bbox'],display_bbox=candidate['display_bbox'],typography=candidate['typography'])
            return scale,audit
        except ValueError as exc:
            entry.update(result='failed',reason=str(exc))
    audit['stop_reason']='여백·행 간격·허용 공간을 조정해도 읽기 크기와 경계를 함께 지킬 수 없습니다.'
    return None,audit
