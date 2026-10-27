"""Resolve measured ink conflicts without treating shared whitespace as damage.

All candidates are scratch-rendered before changing the original PDF. The
translation and source strings stay immutable; only placement is adjusted.
"""
import copy
import math
from collections import defaultdict
from statistics import median

import fitz

from .typesetter import fit
from .validate import check_placement, characters, character_key


def key(point):
    return tuple(round(v, 3) for v in point)


def free_rectangles(bounds, obstacles, limit=32):
    """Bounded maximal rectangular spaces; overlapping model boxes are not ink."""
    candidates = [fitz.Rect(bounds)]
    for obstacle in obstacles:
        b = fitz.Rect(obstacle)
        if not b.intersects(bounds):
            continue
        following = []
        for a in candidates:
            if (a & b).get_area() <= .001:
                following.append(a)
                continue
            following.extend([fitz.Rect(a.x0, a.y0, min(a.x1,b.x0-.15), a.y1),
                              fitz.Rect(max(a.x0,b.x1+.15), a.y0, a.x1, a.y1),
                              fitz.Rect(a.x0, a.y0, a.x1, min(a.y1,b.y0-.15)),
                              fitz.Rect(a.x0, max(a.y0,b.y1+.15), a.x1, a.y1)])
        candidates = []
        for a in sorted(following, key=lambda r:r.get_area(), reverse=True):
            if a.is_empty or a.width < .1 or a.height < .1:
                continue
            if not any(c.contains(a) for c in candidates):
                candidates.append(a)
            if len(candidates) >= limit:
                break
    return candidates


def deletion_boxes(region, source_chars):
    """A tiny exclusive interior removes the entire owned native character.

    Ascender boxes may overlap neighboring lines. A whole-block redaction is
    unnecessary when each owned glyph has an exclusive interior. Verify every
    rectangle against all foreign characters before allowing any deletion.
    """
    target = fitz.Rect(region.get('source_bbox', region['bbox']))
    origins = {key(c['origin']) for s in region['spans'] if s['font'] != 'OCR'
               for c in s['chars'] if c.get('origin')}
    foreign = [fitz.Rect(c['bbox']) for c in source_chars
               if c['c'].strip() and key(c['origin']) not in origins]
    if not any((target & b).get_area() > .001 for b in foreign):
        return [target], False
    owned = [c for c in source_chars if c['c'].strip() and key(c['origin']) in origins]
    if not owned:
        raise ValueError('삭제할 원문 글자의 소유 관계를 확인할 수 없습니다.')
    boxes = []
    for char in owned:
        available = free_rectangles(fitz.Rect(char['bbox']), foreign)
        if not available:
            raise ValueError('겹친 원문 글자를 이웃 글자와 분리하여 삭제할 수 없습니다.')
        b = max(available, key=lambda r:r.get_area())
        x, y = (b.x0+b.x1)/2, (b.y0+b.y1)/2
        small = fitz.Rect(x-.02,y-.02,x+.02,y+.02)
        if not b.contains(small) or any(small.intersects(f) for f in foreign):
            raise ValueError('원문 글자의 안전한 삭제 공간을 확보하지 못했습니다.')
        boxes.append(small)
    return boxes, True


def probe(page, region, text, policy, blocked, *, review_layout=False):
    with fitz.open() as scratch:
        target = scratch.new_page(width=page.rect.width, height=page.rect.height)
        scale = fit(target, region, text, policy)
        checks = check_placement(target, region, text, blocked, review_layout=review_layout)
        glyphs = [fitz.Rect(c[3]) for s in target.get_texttrace()
                  for c in s['chars'] if chr(c[0]).strip()]
        keys = [character_key(c) for c in characters(target) if c['c'].strip()]
    return scale, checks, glyphs, keys


def resolve(page, region, text, policy, blocked, *, review_layout=False):
    """Reflow inside measured free space, retaining policy size limits."""
    parts = region.get('typography', {}).get('parts', [])
    special = any(p.get('rotate') or p.get('leader') or p.get('adaptive_leader') for p in parts)
    bounds = fitz.Rect(region['bbox']) & page.rect
    # A patch must continue to cover all source ink; its image extent cannot be
    # shrunk along with the text. Give the text a separate inner placement box.
    spaces = free_rectangles(bounds, blocked)
    trials = [] if special else [bounds] + spaces
    best = None
    # Preserve an already complete layout as a candidate, even when its risk
    # estimate is nonzero. Never make the translation disappear just for that.
    if review_layout:
        candidate = copy.deepcopy(region)
        try:
            scale, checks, glyphs, keys = probe(page,candidate,text,policy,blocked,review_layout=True)
            best = (candidate,scale,checks,glyphs,keys)
        except ValueError:
            pass
    for box in trials:
        if box.is_empty or box.width < 2 or box.height < 2:
            continue
        candidate = copy.deepcopy(region)
        flags = candidate.get('dominant_flags', candidate['spans'][0].get('flags', 0))
        part = dict(text=text, source_text=region['text'], anchored=False,
                    bbox=list(box+(.35,.35,-.35,-.35)),
                    ink_bbox=list(region.get('source_bbox',region['bbox'])),
                    font_size=region['font_size'], flags=flags,
                    color=region.get('dominant_color',candidate['spans'][0].get('color',0)),
                    align=region.get('text_align','left'), wrap_long_words=True,
                    line_height=min(policy.line_height,1.05))
        candidate['typography'] = {'parts':[part]}
        trial_policy = policy.model_copy(update={'preserve_spacing':True})
        try:
            scale, checks, glyphs, keys = probe(page,candidate,text,trial_policy,blocked,review_layout=review_layout)
        except ValueError:
            continue
        score = (checks.get('overlap_area',0), not checks['within_bounds'], -scale)
        if best is None or score < (best[2].get('overlap_area',0), not best[2]['within_bounds'], -best[1]):
            best = (candidate,scale,checks,glyphs,keys)
    if best:
        if best[0].get('typography'):
            region['typography'] = best[0]['typography']
        # fit() must replay this exact candidate even with spacing preservation off.
        region['resolved_placement'] = bool(best[0].get('typography'))
        return best[1:]
    return None


def clear_resolved_risks(outcome):
    checks=outcome.get('placement_checks',{})
    if outcome.get('layout_review_applied') and checks.get('within_bounds') and checks.get('clear_of_neighbors'):
        old=set(outcome.pop('layout_risk_reasons',[]))
        outcome['review_reasons']=[reason for reason in outcome.get('review_reasons',[]) if reason not in old]
        outcome.pop('layout_review_applied',None)
        outcome['adjustment']['strategy']='measured_free_space'


def harmonize_tables(page, model, approved, outcomes, policy, blockers):
    """Match comparable body/header peers, with explicit small-cell exceptions."""
    tables = model.get('space_geometry',{}).get('tables',[])
    if not tables:
        return []
    groups = defaultdict(list)
    for r in approved:
        parts = r.get('typography',{}).get('parts',[])
        if r.get('kind')=='table_cell' and len(parts)>1:
            comparable = (len({(p['flags']&30,p['color']) for p in parts})==1
                and max(p['font_size'] for p in parts)-min(p['font_size'] for p in parts)<.5
                and not any(p.get(k) for p in parts for k in ('rotate','leader','repeat_group','adaptive_leader','bold_prefix')))
            if comparable:
                # Wrapped source rows of one style must not acquire unrelated
                # font sizes merely because each translated row is a different length.
                candidate=copy.deepcopy(r)
                other_glyphs=[b for other in approved if other['id']!=r['id'] for b in blockers[other['id']]['placed']]
                outcome=outcomes[r['id']]
                resolved=resolve(page,candidate,outcome.get('logical_text',outcome['text']),policy,
                                 blockers[r['id']]['source']+other_glyphs)
                if resolved and r['font_size']*resolved[0]>=outcome['font_size']-.01:
                    scale,checks,glyphs,keys=resolved
                    r['typography']=candidate['typography'];r['resolved_placement']=True
                    outcome.update(scale=scale,font_size=r['font_size']*scale,placement_checks=checks,placement_keys=keys)
                    clear_resolved_risks(outcome)
                    blockers[r['id']]['placed']=glyphs
                    parts=r['typography']['parts']
                    outcome.setdefault('layout_style',{})['blocks']=[dict(bbox=p['bbox'],align=p['align'],
                        font_size=p['font_size'],rotation=0,source_text=p['source_text']) for p in parts]
                    outcome.setdefault('review_reasons',[]).append('같은 셀의 동일 서식 줄을 한 크기로 재배치했습니다. 줄바꿈을 확인하세요.')
        if (r.get('kind') != 'table_cell' or len(parts)!=1 or
                any(parts[0].get(k) for k in ('rotate','leader','repeat_group','adaptive_leader'))):
            continue
        ink = fitz.Rect(r.get('source_bbox',r['bbox']))
        table = next((i for i,b in enumerate(tables) if (fitz.Rect(b)+(-1,-1,1,1)).contains(ink)),None)
        if table is None:
            continue
        # Preserve source size levels and emphasis instead of flattening headings.
        style = (table, round(r['font_size']*2)/2, parts[0]['flags'] & 30, parts[0]['color'])
        groups[style].append(r)
    summaries = []
    for style,members in groups.items():
        if len(members)<2:
            continue
        sizes = sorted(outcomes[r['id']]['font_size'] for r in members)
        # One unusually long cell must not make the whole table microscopic.
        source_size=median(r['font_size'] for r in members)
        target = sizes[max(0,len(sizes)//4)] if len(sizes)>3 else min(sizes)
        # Consistency must not make otherwise readable cells tiny. Limit the
        # group-wide reduction to 15%; denser cells keep an explicit exception.
        target=max(target,source_size*max(.85,policy.min_scale),min(source_size,policy.min_font_size))
        target=min(source_size,math.ceil(target*4)/4)
        matched = []
        for r in members:
            outcome = outcomes[r['id']]
            if target < max(min(r['font_size'],policy.min_font_size),r['font_size']*policy.min_scale)-.01:
                continue
            candidate = copy.deepcopy(r)
            part = candidate['typography']['parts'][0]
            part.update(font_size=target,fixed_size=True)
            other_glyphs = [b for other in approved if other['id']!=r['id']
                            for b in blockers[other['id']]['placed']]
            try:
                scale,checks,glyphs,keys=probe(page,candidate,outcome.get('logical_text',outcome['text']),
                                         policy,blockers[r['id']]['source']+other_glyphs)
            except ValueError:
                continue
            r['typography']=candidate['typography']
            blockers[r['id']]['placed']=glyphs
            outcome.update(scale=scale,font_size=target,placement_checks=checks,
                           placement_keys=keys,
                           table_font=dict(target=target,matched=True))
            clear_resolved_risks(outcome)
            quality=outcome.get('typography_quality')
            if quality:
                ratio=target/quality['source_font_size']
                quality.update(rendered_font_size=target,retained_ratio=ratio,
                               review_required=ratio < (.9 if quality.get('heading') else .85)
                               or quality.get('wrapped_heading',False))
                if not quality['review_required'] and target>=5:
                    outcome.pop('readability_warning',None)
                elif quality['review_required']:
                    outcome.setdefault('readability_warning','표의 글자 크기를 맞추면서 원문보다 작아졌습니다. 가독성을 확인하세요.')
            if outcome.get('layout_style',{}).get('blocks'):
                outcome['layout_style']['blocks'][0]['font_size']=target
            matched.append(r['id'])
        for r in members:
            outcome=outcomes[r['id']]
            if abs(outcome['font_size']-target)>.5:
                outcome['table_font']=dict(target=target,matched=False)
                outcome.setdefault('review_reasons',[]).append('좁은 셀의 글자 크기가 같은 표의 유사 항목과 다릅니다.')
        summaries.append(dict(table=style[0],target=target,matched=matched,members=[r['id'] for r in members]))
    return summaries


def failure_category(reason):
    # Recovery's stop explanation can mention protection even when the initial
    # failure was capacity. Classify the cause, not that generic explanation.
    reason = reason.split('. ')[0]
    if any(word in reason for word in ('읽지 못','OCR','여러 칸','분리','소유','특수 글꼴')):
        return 'recognition_or_ownership'
    if any(word in reason for word in ('사진','배경','삭제','링크','주석','폼','보호','워터마크')):
        return 'source_protection'
    if '겹' in reason or '경계를 벗' in reason:
        return 'placement_collision'
    if any(word in reason for word in ('공간','크기','넣을 수','들어가지','여백')):
        return 'insufficient_space'
    if any(word in reason for word in ('겹','경계')):
        return 'placement_collision'
    return 'analysis_or_rendering'
