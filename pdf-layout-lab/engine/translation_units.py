"""Immutable extracted areas, semantic translation units and mutable placement spaces.

pages[].regions is the compatibility projection consumed by the existing renderer.
Source regions are never resized or removed by user edits. Phase 1 uses one
continuous placement per unit, on a single page, rather than splitting translations
back into source-language line lengths.
"""
import copy
import hashlib
import json
import re
import fitz
from .geometry import display_rect

VERSION = 1


def identity(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]


def ensure(layout):
    if layout.get('unit_schema') == VERSION:
        return layout
    layout['unit_schema'] = VERSION
    layout['source_regions'] = []
    layout['translation_units'] = []
    layout['placement_spaces'] = []
    for index,page in enumerate(layout['pages']):
        number = page.get('number',index)
        for region in page['regions']:
            fragments = region.get('source_fragments') if region.get('logical_paragraph') and not region.get('patch') else None
            fragments = fragments or [dict(text=region['text'],bbox=region.get('source_bbox',region['bbox']),spans=region['spans'])]
            source_ids = []
            for fragment in fragments:
                sid = f"s{number+1}-"+identity([region['id'],fragment['bbox'],fragment['text']])
                source_ids.append(sid)
                layout['source_regions'].append(dict(id=sid,page=number,text=fragment['text'],
                    bbox=list(fragment['bbox']),spans=copy.deepcopy(fragment['spans']),
                    kind=region.get('kind','paragraph'),structure=region.get('structure'),patch=bool(region.get('patch')),
                    mask_boxes=copy.deepcopy(region.get('mask_boxes',[fragment['bbox']])) if len(fragments)==1 else [s['bbox'] for s in fragment['spans']]))
            add_unit(layout,region,source_ids,number)
    refresh_context(layout)
    return layout


def add_unit(layout, region, source_ids, page, *, status='pending', reason=''):
    uid = 'u-'+identity(source_ids)
    source_text=region['text']
    # Native table prose wraps at the cell edge. Keep real list starts while
    # joining continuation lines before translation, without changing sources.
    if (region.get('kind')=='table_cell' and not region.get('patch') and not region.get('structure')
            and not region.get('user_geometry') and '\n' in source_text
            and fitz.Rect(region['bbox']).width>70
            and not re.search(r'[☐☑☒✓✔]',source_text)):
        logical=[]
        for line in source_text.splitlines():
            if logical and not re.match(r'^\s*(?:[•◦●▪■□○-]|[①-⑳]|\(?\d+[.)])',line):
                logical[-1]+=' '+line.strip()
            else:logical.append(line)
        source_text='\n'.join(logical)
        region['translation_soft_wraps']=True
    unit = dict(id=uid,page=page,source_region_ids=list(source_ids),source_text=source_text,
                kind=region.get('kind','paragraph'),structured=bool(region.get('structure')),
                flow=bool(region.get('logical_paragraph') or region.get('user_geometry') and not region.get('structure')),
                protected=bool(region.get('keep_original') or region.get('structure')=='checkbox_mark'),
                placement_ids=['space-'+region['id']],status=status,reason=reason,
                translated_text=None,context_before='',context_after='')
    if unit['protected']: unit['status']='protected'
    layout['translation_units'].append(unit)
    layout['placement_spaces'].append(dict(id='space-'+region['id'],unit_id=uid,page=page,
        region_id=region['id'],source_region_ids=list(source_ids),bbox=list(region['bbox']),status='pending',reason=''))
    region['translation_unit_id']=uid
    region['source_region_ids']=list(source_ids)
    return unit


def refresh_context(layout):
    """Adjacent same-column prose only. Context informs translation, never placement.

    Units never cross a heading, list, table or figure boundary automatically.
    Existing conservative paragraph grouping supplies confirmed connections.
    """
    regions = {r['id']:r for p in layout['pages'] for r in p['regions']}
    spaces = {s['unit_id']:s for s in layout['placement_spaces']}
    units = layout['translation_units']
    for unit in units:
        unit['context_before']=unit['context_after']=''
        space=spaces.get(unit['id'])
        if not space: continue
        region=regions.get(space['region_id'])
        if not region or region.get('kind')!='paragraph' or region.get('patch') or unit['protected']: continue
        a=fitz.Rect(region.get('source_bbox',region['bbox']))
        for direction in ('before','after'):
            candidates=[]
            for other in units:
                if other is unit or other['page']!=unit['page'] or other['protected']: continue
                peer_space=spaces.get(other['id']); peer=regions.get(peer_space['region_id']) if peer_space else None
                if not peer: continue
                b=fitz.Rect(peer.get('source_bbox',peer['bbox']))
                gap=a.y0-b.y1 if direction=='before' else b.y0-a.y1
                if 0<=gap<max(18,region['font_size']*2) and min(a.x1,b.x1)-max(a.x0,b.x0)>min(a.width,b.width)*.6:
                    candidates.append((gap,other,peer))
            if candidates:
                _,other,peer=min(candidates,key=lambda x:x[0])
                if peer['kind']=='paragraph' and not peer.get('patch'):
                    text=other['source_text']
                    unit['context_'+direction]=text[-500:] if direction=='before' else text[:500]


def replace_regions(layout, originals, new_regions, page, *, pending=False):
    old_ids={r['id'] for r in originals}
    old_units={r.get('translation_unit_id') for r in originals}
    source_ids=list(dict.fromkeys(s for r in originals for s in r.get('source_region_ids',[])))
    prior={u['id']:u for u in layout['translation_units'] if u['id'] in old_units}
    layout['translation_units']=[u for u in layout['translation_units'] if u['id'] not in old_units]
    layout['placement_spaces']=[s for s in layout['placement_spaces'] if s['region_id'] not in old_ids]
    for region in new_regions:
        ids=region.pop('_split_source_ids',None) or source_ids
        unit=add_unit(layout,region,ids,page)
        old=prior.get(unit['id'])
        if old and old['source_text']==unit['source_text'] and not pending:
            for key in ('status','reason','translated_text'):
                unit[key]=old[key]
        if pending: unit['reason']='번역 묶음이 변경되었습니다. 이 단위만 다시 번역하거나 교체문을 입력하세요.'
    refresh_context(layout)


def subset(layout, region_ids):
    result=copy.deepcopy(layout); ids=set(region_ids)
    result['pages']=[{**p,'regions':[r for r in p['regions'] if r['id'] in ids]} for p in result['pages'] if any(r['id'] in ids for r in p['regions'])]
    result['region_count']=sum(len(p['regions']) for p in result['pages'])
    if result.get('unit_schema'):
        result['placement_spaces']=[s for s in result['placement_spaces'] if s['region_id'] in ids]
        uids={s['unit_id'] for s in result['placement_spaces']}
        result['translation_units']=[u for u in result['translation_units'] if u['id'] in uids]
        sids={sid for u in result['translation_units'] for sid in u['source_region_ids']}
        result['source_regions']=[s for s in result['source_regions'] if s['id'] in sids]
    return result


def accept_translation(layout, result):
    for unit in layout.get('translation_units',[]):
        entry=result.get('unit_results',{}).get(unit['id'])
        if entry: unit.update(entry)


def project(layout):
    """Build the renderer view from unit content and placement geometry."""
    ensure(layout)
    units={u['id']:u for u in layout['translation_units']}
    spaces={s['region_id']:s for s in layout['placement_spaces']}
    source_ids={s['id'] for s in layout['source_regions']}
    for page in layout['pages']:
        for region in page['regions']:
            space=spaces.get(region['id'])
            unit=units.get(space['unit_id']) if space else None
            if not unit or not set(unit['source_region_ids'])<=source_ids:
                raise ValueError('원본 영역·번역 단위·배치 공간 연결이 손상되었습니다.')
            region['text']=unit['source_text']
            region['bbox']=list(space['bbox']);region['display_bbox']=display_rect(page, space['bbox'])
            if region.get('user_geometry'): region['user_bbox']=list(space['bbox'])


def record_render(layout, policy, outcomes):
    """Placement outcome and translation status are independent."""
    if not layout.get('unit_schema'): return
    by_region={r['id']:r for p in layout['pages'] for r in p['regions']}
    units={u['id']:u for u in layout['translation_units']}
    for space in layout['placement_spaces']:
        region=by_region[space['region_id']]; outcome=outcomes[region['id']]
        space.update(bbox=list(region['bbox']),status=outcome['status'],reason=outcome.get('reason',''),
                     rendered_text=outcome['text'] if outcome.get('applied') else region['text'],
                     applied=outcome.get('applied',False),review_required=outcome.get('review_required',False),
                     restored=outcome.get('restored',False))
        unit=units[space['unit_id']]
        text=policy.replacements.get(region['id'],region['text'])
        edited=(unit['status'] in ('pending','failed','unchanged') or region['id'] in policy.manual_regions or region.get('user_linebreak_text')==text)
        if edited and not unit['protected'] and text!=unit['source_text'] and text!=unit.get('translated_text'):
            unit.update(translated_text=text,status='manual',reason='사용자가 수정한 교체문')
        outcome['translation_unit_id']=unit['id']
        outcome['translation_status']=unit['status']
        outcome['source_region_ids']=list(unit['source_region_ids'])


def split_region(layout, region_id, after_source_id):
    ensure(layout)
    page=next((p for p in layout['pages'] if any(r['id']==region_id for r in p['regions'])),None)
    if page is None: raise ValueError('영역을 찾을 수 없습니다.')
    original=next(r for r in page['regions'] if r['id']==region_id)
    if original.get('patch') or original.get('keep_original') or original.get('structure'):
        raise ValueError('일반 본문의 원본 줄 경계에서만 나눌 수 있습니다.')
    ids=original['source_region_ids']
    if after_source_id not in ids[:-1]: raise ValueError('마지막 줄을 제외한 분리 위치를 선택하세요.')
    cut=ids.index(after_source_id)+1
    sources={s['id']:s for s in layout['source_regions']}
    new=[]
    for group in (ids[:cut],ids[cut:]):
        fragments=[sources[s] for s in group]
        box=fitz.Rect()
        for f in fragments: box |= fitz.Rect(f['bbox'])
        region=copy.deepcopy(original)
        region.update(id='region-'+identity(group),text='\n'.join(f['text'] for f in fragments),
                      bbox=list(box+(-.5,-1.5,.5,1.5)),display_bbox=list(box+(-.5,-1.5,.5,1.5)),
                      source_bbox=list(box),spans=[s for f in fragments for s in f['spans']],
                      mask_boxes=[b for f in fragments for b in f['mask_boxes']],
                      source_fragments=copy.deepcopy(fragments),logical_paragraph=True,
                      _split_source_ids=group)
        for key in ('typography','user_geometry','user_bbox','spacing_source_bbox','spacing_source_display_bbox','source_mask_boxes','prose_source_bbox','space_base_bbox','space_plan','user_linebreak_text'):
            region.pop(key,None)
        new.append(region)
    page['regions']=[r for r in page['regions'] if r['id']!=region_id]+new
    page['regions'].sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
    from .enhanced import partition_regions
    partition_regions(page['regions'])
    replace_regions(layout,[original],new,page['number'],pending=True)
    layout['region_count']=sum(len(p['regions']) for p in layout['pages'])
    return new,page['number'],original
