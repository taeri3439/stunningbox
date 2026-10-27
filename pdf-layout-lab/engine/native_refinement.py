"""Repair native reading units before freezing source provenance or translation.

This also upgrades old analysis caches. No translated string or model-provided
coordinate is used to join a continuation line.
"""
import copy
import re
import unicodedata

import fitz

from .enhanced import regroup_prose, union, merge_image_paragraphs, raster
from .geometry import sync_display

VERSION = 3
SYMBOLS = {'\uf09f':'•','\uf0a7':'▪','\uf06e':'■','\uf071':'□','\uf0a1':'○'}
MARKER = re.compile(r'^\s*(?:\(?\d{1,2}[.)]|[①-⑳•●▪◦■□○-])')


def repair_symbols(region):
    special = {c for c in region['text'] if unicodedata.category(c)=='Co'}
    # These are verified Symbol/Wingdings bullets emitted by Hangul/Word. Do
    # not generalize this to unknown private-use letters, numbers or icons.
    if not special or not special.issubset(SYMBOLS): return False
    if any(line[:i].strip() for line in region['text'].splitlines() for i,c in enumerate(line) if c in special):
        return False
    region['native_text_before_symbol_repair']=region['text']
    region['text']=region['text'].translate(str.maketrans(SYMBOLS))
    region['spans']=copy.deepcopy(region['spans'])
    for span in region['spans']:
        span['text']=span['text'].translate(str.maketrans(SYMBOLS))
        for char in span.get('chars',[]):char['c']=char['c'].translate(str.maketrans(SYMBOLS))
    region['warnings']=[w for w in region.get('warnings',[]) if '특수 글꼴' not in w]
    region['symbol_repair']=dict(kind='verified_list_markers',mapping={c:SYMBOLS[c] for c in special})
    return True


def refine(layout, source):
    if layout.get('native_refinement') == VERSION: return layout
    if layout.get('unit_schema'):
        raise ValueError('원본 영역을 확정한 뒤에는 원문 분석을 다시 쓰지 않습니다. 새 분석에서 실행하세요.')
    with fitz.open(source) as pdf:
        for page_info in layout['pages']:
            page=pdf[page_info['number']]
            rotation = page.rotation
            page.set_rotation(0)  # Read-only analysis handle; source bytes are never saved.
            regions=page_info['regions']
            if any('source_bbox' not in r or 'kind' not in r for r in regions):
                continue  # Legacy text-only analysis has no geometric contract.
            images=[fitz.Rect(i['bbox']) for i in page.get_image_info()]
            cells=[fitz.Rect(c) for t in page.find_tables().tables for c in t.cells if c]
            for r in regions:
                if repair_symbols(r) and all(s['font']!='OCR' for s in r['spans']) and not any(fitz.Rect(r['source_bbox']).intersects(im) for im in images):
                    r['patch']=False
                # Discard only saved render geometry, never original ink.
                for key in ('typography','spacing_source_bbox','spacing_source_display_bbox','source_mask_boxes','prose_source_bbox','space_base_bbox','space_plan'):
                    r.pop(key,None)
            groups=[]
            for r in sorted(regions,key=lambda r:(r['source_bbox'][1],r['source_bbox'][0])):
                if r.get('structure')!='list_item' or r.get('patch') or r.get('user_geometry'):
                    continue
                box=fitz.Rect(r['source_bbox'])
                cell=min((c for c in cells if (c+(-1,-1,1,1)).contains(box)),key=lambda c:c.get_area(),default=None)
                if cell is None:continue
                match=None
                if not MARKER.match(r['text']):
                    for group in reversed(groups):
                        prev=group[-1][0]; a=fitz.Rect(prev['source_bbox'])
                        if (group[-1][1]==cell and -.4*r['font_size'] <= box.y0-a.y1 < r['font_size']*.8
                                and box.y0>a.y0 and box.x0>=a.x0-1
                                and abs(r['font_size']-prev['font_size'])<.8):
                            match=group;break
                if match is not None:match.append((r,cell))
                else:groups.append([(r,cell)])
            removed=set()
            for group in groups:
                if len(group)<2:continue
                first=group[0][0]; members=[r for r,_ in group]
                first['text']='\n'.join(r['text'] for r in members)
                first['spans']=[s for r in members for s in r['spans']]
                first['mask_boxes']=[b for r in members for b in r.get('mask_boxes',[])]
                first['source_bbox']=list(union(r['source_bbox'] for r in members))
                first['bbox']=list(union(r['bbox'] for r in members));first['display_bbox']=list(first['bbox'])
                first['logical_paragraph']=True
                first['joined_region_ids']=[r['id'] for r in members]
                removed.update(r['id'] for r in members[1:])
            regions=[r for r in regions if r['id'] not in removed]
            if images and any(r.get('image_label') and not r.get('warnings') for r in regions):
                regions=merge_image_paragraphs(regions,page,raster(page,dpi=120))
            separators=[d['rect']+(-.3,-.3,.3,.3) for d in page.get_drawings() if
                        (d['rect'].width>30 and d['rect'].height<2) or (d['rect'].height>30 and d['rect'].width<2)]
            regions=regroup_prose(regions,barriers=cells+images+separators)
            # Source ascenders may overlap at a row boundary; the two placement
            # boxes share that margin, while source ink and erasure stay intact.
            for i,r in enumerate(regions):
                a=fitz.Rect(r['bbox'])
                for other in regions[i+1:]:
                    b=fitz.Rect(other['bbox'])
                    inter=a&b
                    if inter.is_empty:continue
                    if inter.height < min(r['font_size'],other['font_size'])*.3 and abs(a.y0-b.y0)>inter.height:
                        upper,lower=(r,other) if a.y0<b.y0 else (other,r)
                        boundary=(upper['bbox'][3]+lower['bbox'][1])/2
                        upper['bbox'][3]=boundary;lower['bbox'][1]=boundary
                    elif inter.width<1.5 and abs(a.x0-b.x0)>inter.width:
                        left,right=(r,other) if a.x0<b.x0 else (other,r)
                        boundary=(left['bbox'][2]+right['bbox'][0])/2
                        left['bbox'][2]=boundary;right['bbox'][0]=boundary
                    r['display_bbox']=list(r['bbox']);other['display_bbox']=list(other['bbox'])
            seen=set()
            for r in regions:
                original=r['id']; index=2
                while r['id'] in seen:r['id']=f'{original}-n{index}';index+=1
                seen.add(r['id'])
            page_info['regions']=sorted(regions,key=lambda r:(r['bbox'][1],r['bbox'][0]))
            page.set_rotation(rotation)
            sync_display(page, page_info)
    layout['native_refinement']=VERSION
    layout['region_count']=sum(len(p['regions']) for p in layout['pages'])
    return layout
