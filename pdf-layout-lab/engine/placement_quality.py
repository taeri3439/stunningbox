"""Reserve verified whitespace before shrinking native PDF typography.

Source glyph boxes / erase masks never expand. Only placement geometry changes;
image labels, user boxes and special rotated / leader layouts keep their rules.
"""
import copy
import hashlib
from statistics import median

import fitz

from .models import normalized


def native(region):
    return (region.get('source_bbox') and region.get('kind') in ('paragraph', 'table_cell')
            and not region.get('patch') and not region.get('keep_original')
            and not region.get('spatial_unresolved') and not region.get('warnings')
            and all(s.get('font') != 'OCR' for s in region.get('spans', [])))


def graphics(page):
    boxes = [fitz.Rect(i['bbox']) for i in page.get_image_info()]
    boxes += [a.rect for a in page.annots() or []]
    boxes += [w.rect for w in page.widgets() or []]
    boxes += [l['from'] for l in page.get_links()]
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 're':
                r = item[1]
                boxes.extend([fitz.Rect(r.x0, r.y0-.3, r.x1, r.y0+.3),
                              fitz.Rect(r.x0, r.y1-.3, r.x1, r.y1+.3),
                              fitz.Rect(r.x0-.3, r.y0, r.x0+.3, r.y1),
                              fitz.Rect(r.x1-.3, r.y0, r.x1+.3, r.y1)])
            elif item[0] == 'l':
                a, b = item[1:3]
                boxes.append(fitz.Rect(min(a.x,b.x)-.3, min(a.y,b.y)-.3,
                                       max(a.x,b.x)+.3, max(a.y,b.y)+.3))
            elif item[0] in ('c', 'qu'):
                # Curved diagram artwork is a barrier too.
                boxes.append(fitz.Rect(drawing['rect']))
    return boxes


def column_edge(page, region, regions, tables):
    ink = fitz.Rect(region['source_bbox'])
    size = region['font_size']
    peers = [fitz.Rect(r['source_bbox']) for r in regions if native(r)
             and r['kind'] == 'paragraph' and r['id'] != region['id']
             and abs(r['source_bbox'][1]-ink.y0) < max(180,size*15)]
    aligned = [r.x1 for r in peers if abs(r.x0-ink.x0) < max(14,size*1.5)
               and r.width > page.rect.width*.28]
    # A table's OUTER edge supplies the text margin on pages with no wide prose.
    outer = [r.x1 for r in tables if r.width > page.rect.width*.45
             and r.x0 <= ink.x0+size*2 and r.y0 >= ink.y0-size]
    if aligned:
        edge = max(max(aligned),median(outer) if outer else 0)
    elif outer:
        edge = median(outer)
    else:
        wide = [fitz.Rect(r['source_bbox']) for r in regions if native(r)
                and r['kind']=='paragraph' and r['source_bbox'][2]-r['source_bbox'][0]>page.rect.width*.45]
        edge = median(r.x1 for r in wide) if wide else page.rect.width-max(ink.x0,page.rect.width*.07)
    # Repeated starts identify another column even when its current row is empty.
    starts = sorted(r.x0 for r in peers if r.x0 > ink.x1+10)
    for start in starts:
        if sum(abs(x-start)<max(10,size) for x in starts)>=2:
            edge = min(edge,start-max(6,size*.7))
            break
    return max(ink.x1,min(page.rect.width-8,edge))


def reserve_spaces(page, model):
    regions = model['regions']
    for r in regions:
        r.pop('space_plan',None)
        if r.get('user_geometry'):
            for key in ('bbox','display_bbox','spacing_source_bbox','spacing_source_display_bbox'):
                r[key] = list(r['user_bbox'])
        elif native(r):
            r.setdefault('space_base_bbox',list(r.get('prose_source_bbox',r['bbox'])))
            r['bbox'] = list(r['space_base_bbox'])
    # The source PDF is immutable within a document. Cache measured geometry in
    # the run so applying one region does not detect all 34 pages' tables again.
    signature=hashlib.sha256(b''.join(page.parent.xref_stream(x) for x in page.get_contents())).hexdigest()
    cache=model.get('space_geometry',{})
    if cache.get('signature')==signature and cache.get('version')==1:
        cells,tables,art=([fitz.Rect(b) for b in cache[k]] for k in ('cells','tables','art'))
    else:
        try:
            found = page.find_tables().tables
            cells = [fitz.Rect(c) for t in found for c in t.cells if c]
            tables = [fitz.Rect(t.bbox) for t in found]
        except (ValueError, RuntimeError):
            cells, tables = [], []
        art = graphics(page)
        model['space_geometry']=dict(version=1,signature=signature,cells=[list(b) for b in cells],
                                     tables=[list(b) for b in tables],art=[list(b) for b in art])
    # PDF ascender/descender boxes can share a thin strip at row boundaries.
    # Retain the existing margin partition before reserving extra whitespace.
    for r in regions:
        if not native(r) or r.get('user_geometry') or r['kind']!='paragraph':continue
        box=fitz.Rect(r['bbox'])
        for other in regions:
            if other is r or other.get('kind') in ('paragraph','protected_text'):continue
            b=fitz.Rect(other['bbox'])
            if not box.intersects(b):continue
            if b.y0<box.y0 and 0<b.y1-box.y0<r['font_size']*.4:box.y0=b.y1+.2
            elif b.y1>box.y1 and 0<box.y1-b.y0<r['font_size']*.4:box.y1=b.y0-.2
        for b in art:
            if b.height<1 and b.width>box.width*.8 and b.x0<=box.x0 and b.x1>=box.x1:
                if box.y0<b.y1<box.y0+r['font_size']*.25:box.y0=b.y1+.7
        r['bbox']=list(box)
    # Frozen reservations make expansion independent of region traversal order.
    reserved = {r['id']:fitz.Rect(r['bbox']) for r in regions}
    for r in regions:
        if not native(r) or r.get('user_geometry'):
            continue
        old = reserved[r['id']]; ink = fitz.Rect(r['source_bbox'])
        if old.y0 > page.rect.height*.93 or r.get('text_align') in ('center','right'):
            continue
        cell = min((c for c in cells if (c+(-.6,-.6,.6,.6)).contains(ink)),
                   key=lambda c:c.get_area(),default=None)
        if r['kind']=='table_cell' and cell is None:
            continue
        if cell is not None:
            if not (cell+(-.6,-.6,.6,.6)).contains(old):
                continue
            right = max(old.x1,cell.x1-.7)
            bottom = max(old.y1,cell.y1-.7)
        else:
            right = max(old.x1,column_edge(page,r,regions,tables))
            bottom = min(page.rect.height-8,old.y1+min(36,r['font_size']*3))
        barriers = [b for rid,b in reserved.items() if rid!=r['id']] + art
        for b in barriers:
            if b.x0>=old.x1-.1 and min(b.y1,old.y1)>max(b.y0,old.y0):
                right=min(right,b.x0-.7)
        candidate = fitz.Rect(old.x0,old.y0,max(old.x1,right),old.y1)
        for b in barriers:
            if b.y0>=old.y1-.1 and min(b.x1,candidate.x1)>max(b.x0,candidate.x0):
                bottom=min(bottom,b.y0-.7)
        candidate.y1=max(old.y1,bottom)
        # Artwork already touching the old box must not be crossed farther.
        if any((candidate&b).get_area()>(old&b).get_area()+.1 for b in barriers):
            candidate=old
        r['space_plan']={'boundary':'cell' if cell else 'column',
                         'before_bbox':list(old),'after_bbox':list(candidate),
                         'expanded':candidate!=old}
        for key in ('bbox','display_bbox','spacing_source_bbox','spacing_source_display_bbox'):
            r[key]=list(candidate)


def eligible(region):
    parts = region.get('typography',{}).get('parts',[])
    return (native(region) and parts and not region.get('user_geometry')
            and not any(p.get('repeat_group') or p.get('rotate') or p.get('leader')
                        or p.get('adaptive_leader') for p in parts))


def prefer_source_size(page, region, text, policy):
    """Try original size before allowing shrink; return a verified frozen plan."""
    from .typesetter import fit, render_units, text_lines
    if not eligible(region):
        return None
    parts = region['typography']['parts']
    originals = [p.get('source_font_size',p['font_size']) for p in parts]
    heading = len(parts)==1 and parts[0].get('role')=='heading' and '\n' not in parts[0]['text']
    trials=[('source_size',False,False,1),('space_and_spacing',True,False,1)]
    if heading:
        trials=[('heading_single_line',False,True,1),('heading_single_line',True,True,1),
                ('heading_single_line',True,True,.95),('heading_single_line',True,True,.9)]+trials
    attempts=[]
    # Compare the ordinary and compact bounded fits too: a successful but tiny
    # ordinary fit must not prevent the better spacing candidate from winning.
    trials += [('bounded_shrink',False,False,0),('spacing_before_shrink',True,False,0)]
    best=None
    for step,compact,nowrap,factor in trials:
        if factor and factor<1 and (factor<policy.min_scale or min(originals)*factor<policy.min_font_size):
            continue
        candidate=copy.deepcopy(region)
        cp=candidate['typography']['parts']; box=fitz.Rect(region['bbox'])
        for index,p in enumerate(cp):
            p.pop('layout_error',None)
            p.update(font_size=originals[index]*(factor or 1),fixed_size=bool(factor))
            if nowrap:p['single_line']=True
            if compact:
                p['line_height']=min(policy.line_height,1.05)
                if index==0:p['bbox'][1]=max(box.y0+.1,p['bbox'][1]-.7)
                if index==len(cp)-1:p['bbox'][3]=box.y1-.1
                p['bbox'][2]=box.x1-.2
        try:
            with fitz.open() as scratch:
                probe=scratch.new_page(width=page.rect.width,height=page.rect.height)
                scale=fit(probe,candidate,text,policy)
                if normalized(probe.get_text())!=normalized(''.join(render_units(p['text']) for p in cp)):
                    raise ValueError('조정 후 글자 누락')
                if any(not box.contains(fitz.Point(c['origin'])) for b in probe.get_text('rawdict')['blocks'] if b['type']==0
                       for line in b['lines'] for s in line['spans'] for c in s['chars'] if c['c'].strip()):
                    raise ValueError('조정 후 경계 이탈')
                count=len(text_lines(probe))
                if nowrap and count!=1:raise ValueError('제목 줄바꿈 유지')
            attempts.append({'step':step,'result':'passed'})
            result=dict(scale=scale,attempts=attempts,line_count=count,source_sizes=originals,
                        retained_ratio=factor or scale,heading=bool(heading),strategy=step)
            if factor:
                region['typography']=candidate['typography']
                return result
            if best is None or scale>best[0]:best=(scale,candidate,result)
        except ValueError:
            attempts.append({'step':step,'result':'failed'})
    if best:
        region['typography']=best[1]['typography']
        return best[2]
    # Existing bounded fitting/recovery remains available when original size
    # cannot fit. It will carry a separate readability warning if still small.
    return dict(attempts=attempts,source_sizes=originals,heading=bool(heading),strategy='bounded_shrink')
