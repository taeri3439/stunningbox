"""User-confirmed layout edits. Source erasure masks never follow resize boxes."""
import copy
import math
import uuid
import fitz
from pydantic import BaseModel, Field
from typing import Literal


class RegionEditRequest(BaseModel):
    base_run: str
    region_ids: list[str] = Field(min_length=1, max_length=100)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    source_text: str | None = Field(default=None, max_length=15000)
    replacement: str | None = Field(default=None, max_length=15000)
    action: Literal['apply', 'translate_apply'] = 'apply'
    source_lang: Literal['ko','en','ja','zh','es','fr','de','vi'] = 'ko'
    target_lang: Literal['ko','en','ja','zh','es','fr','de','vi'] = 'en'


def edit_layout(layout, request):
    layout=copy.deepcopy(layout)
    from .translation_units import ensure, replace_regions
    ensure(layout)
    for p in layout['pages']:
        for r in p['regions']:
            r.setdefault('source_bbox',list(r['bbox']))
    ids=set(request.region_ids)
    found=[(i,r) for i,p in enumerate(layout['pages']) for r in p['regions'] if r['id'] in ids]
    if len(found)!=len(ids) or len({i for i,r in found})!=1:
        raise ValueError('같은 페이지의 영역만 합칠 수 있습니다.')
    number=found[0][0]; page=layout['pages'][number]
    regions=sorted((r for _,r in found),key=lambda r:(round(r['source_bbox'][1]/4),r['source_bbox'][0]))
    if any(r.get('kind')=='protected_text' or r.get('structure')=='checkbox_mark' or r.get('spatial_unresolved') for r in regions):
        raise ValueError('보호 영역·체크 표시·위치 미확정 영역은 합치거나 조절할 수 없습니다.')
    if len({bool(r.get('patch')) for r in regions})>1:
        raise ValueError('이미지 글자와 일반 글자는 따로 편집하세요.')
    region=copy.deepcopy(regions[0])
    source=fitz.Rect(regions[0]['source_bbox']); box=fitz.Rect(regions[0]['bbox'])
    for r in regions[1:]:
        source |= fitz.Rect(r['source_bbox']); box |= fitz.Rect(r['bbox'])
    if request.bbox is not None:
        if not all(math.isfinite(v) for v in request.bbox):
            raise ValueError('박스 좌표는 유한한 숫자여야 합니다.')
        box=fitz.Rect(request.bbox)
    if box.width<5 or box.height<5 or not fitz.Rect(0,0,page['width'],page['height']).contains(box):
        raise ValueError('박스는 페이지 안에 있어야 하고 폭·높이는 5pt 이상이어야 합니다.')
    # A merged rectangle must not absorb an unselected neighboring paragraph.
    for other in page['regions']:
        if other['id'] in ids or other.get('kind')=='protected_text': continue
        ink=fitz.Rect(other['source_bbox'])
        if (box & ink).get_area()>max(2,ink.get_area()*.08):
            raise ValueError(f"{other['id']}의 원문과 겹칩니다. 해당 영역도 선택하거나 박스를 줄이세요.")
    text=request.source_text if request.source_text is not None else '\n'.join(r['text'] for r in regions)
    if not text.strip(): raise ValueError('번역할 원문을 입력하세요.')
    region.update(id=region['id'] if len(regions)==1 else f'p{number+1}-user-{uuid.uuid4().hex[:10]}',
        text=text,source_bbox=list(source),bbox=list(box),display_bbox=list(box),
        spans=[s for r in regions for s in r['spans']],
        mask_boxes=[b for r in regions for b in r.get('source_mask_boxes',r.get('mask_boxes',[r['source_bbox']]))],
        user_bbox=list(box),user_geometry=True,merged_from=[r['id'] for r in regions])
    if len(regions)>1:
        region['kind']='paragraph'
        region.pop('structure',None)
    for key in ('typography','spacing_source_bbox','spacing_source_display_bbox','source_mask_boxes','prose_source_bbox','space_base_bbox','space_plan','user_linebreak_text'):
        region.pop(key,None)
    page['regions']=[r for r in page['regions'] if r['id'] not in ids]+[region]
    page['regions'].sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
    layout['region_count']=sum(len(p['regions']) for p in layout['pages'])
    replace_regions(layout,regions,[region],page.get('number',number))
    return layout,region,number,regions
