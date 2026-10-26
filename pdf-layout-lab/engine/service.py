from pathlib import Path
import copy

import fitz

from .models import RenderRequest
from .typesetter import FontResolver, fit, replay, prepare_repeated_sizes
from . import typography
from .validate import validate
from .enhanced import cleaned_patch


def render(source_path: Path, output_path: Path, layout: dict, policy: RenderRequest,
           *, previous_pdf=None, previous_report=None, changed_pages=None, progress=None) -> dict:
    from .translation_units import project, record_render
    project(layout)
    ids = {r["id"] for p in layout["pages"] for r in p["regions"]}
    if (set(policy.replacements) | set(policy.manual_regions)) - ids:
        raise ValueError("현재 문서에 없는 영역 ID가 포함되어 있습니다.")
    if any(not t.strip() or len(t) > 15000 for t in policy.replacements.values()):
        raise ValueError("교체 텍스트는 비어 있지 않은 15,000자 이하 문자열이어야 합니다.")
    reuse = False
    old_pages = copy.deepcopy(layout['pages']) if changed_pages is not None else None
    if previous_pdf and previous_report and changed_pages and previous_report.get('mode') == 'fit':
        with fitz.open(source_path) as source, fitz.open(previous_pdf) as previous:
            # Page replacement can change interactive destinations. Keep the full path for these PDFs.
            reuse = (len(source) == len(previous) == len(layout['pages'])
                     and all({r['id'] for r in previous_report['regions'] if r['page']==n} == {r['id'] for r in layout['pages'][n]['regions']}
                             for n in range(len(layout['pages'])) if n not in changed_pages)
                     and not previous.get_toc() and not source.get_toc()
                     and all(not p.get_links() and not list(p.annots() or []) and not list(p.widgets() or [])
                             for document in (source, previous) for p in document))
    outcomes = {r['id']: copy.deepcopy(r) for r in previous_report['regions'] if r['id'] in ids} if reuse else {}
    layout_style = None
    if policy.mode == 'fit':
        from .source_styles import prepare_page
        typography.restore_source_geometry(layout)
        with fitz.open(source_path) as source:
            for number, page_info in enumerate(layout['pages']):
                page_info.setdefault('number', number)
                page_info.setdefault('width', source[number].rect.width)
                page_info.setdefault('height', source[number].rect.height)
                prepare_page(source[number], page_info, flow=policy.preserve_spacing)
        if policy.preserve_spacing:
            policy, layout_style = typography.prepare(layout, policy)
            prepare_repeated_sizes(layout, policy)
    else:
        typography.restore_source_geometry(layout)
    manual_ids = set(policy.manual_regions) if policy.mode == 'fit' else set()
    if reuse:
        for number in range(len(layout['pages'])):
            if number not in changed_pages:
                layout['pages'][number] = old_pages[number]
    with fitz.open(source_path) as source, fitz.open(previous_pdf if reuse else source_path) as output:
        fonts = FontResolver(source)
        for pno, page_info in enumerate(layout["pages"]):
            if progress:progress(pno+1,len(layout['pages']))
            if reuse:
                if pno not in changed_pages:
                    continue
                output.delete_page(pno)
                output.insert_pdf(source, from_page=pno, to_page=pno, start_at=pno)
            page, original = output[pno], source[pno]
            approved = []
            patches = {}
            for region in page_info["regions"]:
                text = region["text"] if policy.mode == "replay" else policy.replacements.get(region["id"], region["text"])
                outcome = {"id": region["id"], "page": pno, "text": text,
                           "status": "FIT", "scale": 1.0, "reason": ""}
                outcomes[region["id"]] = outcome
                if policy.mode == 'fit' and region.get('spatial_unresolved'):
                    outcome.update(status='NEEDS_REVIEW', reason='이전 분석에서 도식의 여러 칸이 하나로 합쳐졌습니다. 글자 위치를 안전하게 분리하려면 번역 비교를 다시 실행하세요. 원본 이미지의 글자는 유지했습니다.')
                    continue
                parts = region.get('typography',{}).get('parts',[]) if policy.mode == 'fit' and policy.preserve_spacing else []
                if parts:
                    outcome['layout_style'] = {'anchored':any(p['anchored'] for p in parts),
                        'repeated':any(p.get('repeat_group') for p in parts),
                        'rotated_process':any(p.get('rotate') == 90 and p.get('process_label') for p in parts),
                        'rotated_label':any(p.get('vertical_label') for p in parts),
                        'adaptive_leaders':any(p.get('adaptive_leader') for p in parts),
                        'blocks':[{'bbox':p['bbox'],'align':p['align'],'font_size':p['font_size'],
                                   'rotation':p.get('rotate',0), 'value_left':p.get('value_left'),
                                   'source_text':p['source_text']} for p in parts]}
                    if any(p.get('leader') for p in parts):
                        outcome['logical_text'] = '\n'.join(' '.join(p['leader']) if p.get('leader') else p['text'] for p in parts)
                manual = region['id'] in manual_ids
                if manual:
                    outcome['manual_requested'] = True
                    outcome['overridden_reasons'] = list(region.get('warnings', []))
                if manual and region.get('kind') == 'protected_text':
                    outcome.update(status='NEEDS_REVIEW', reason='워터마크·보호 영역은 직접 교체할 수 없습니다. 본문 영역을 선택하세요.')
                    continue
                if region.get('warnings') and not manual:
                    outcome.update(status='NEEDS_REVIEW',reason=' '.join(region['warnings']))
                    continue
                if region['text']=='[OCR]' and not region.get('keep_original') and not manual:
                    outcome.update(status='NEEDS_REVIEW',reason='이미지 원문 인식이 완료되지 않았습니다.')
                    continue
                if policy.mode == 'fit' and ((region.get('keep_original') and not manual) or text == region['text']):
                    reason = ('시각 검토에서 텍스트가 아닌 아이콘으로 확인해 원본 그림을 보존했습니다.' if region.get('reviewed_nontext')
                              else '체크 표시의 선택 상태를 원본 그대로 보존했습니다.' if region.get('structure') == 'checkbox_mark'
                              else '워터마크·보호 텍스트를 별도 보존했습니다.' if region.get('kind') == 'protected_text'
                              else '번역 변경이 없는 원문/기호를 보존했습니다.')
                    outcome.update(status='UNCHANGED',reason=reason,text=region['text'])
                    continue
                try:
                    if policy.mode == 'replay' and region.get('ocr'):
                        raise ValueError('OCR 영역은 원문 문자 재삽입 대신 FIT 번역 모드를 사용하세요.')
                    if manual and any('회전' in w or '세로' in w or '투명' in w for w in region.get('warnings', [])):
                        raise ValueError('회전·워터마크가 섞인 이전 분석 영역입니다. 번역 비교를 다시 실행해 본문을 분리한 후 적용하세요.')
                    target = fitz.Rect(region.get('source_bbox', region["bbox"]))
                    bounds = original.rect * original.derotation_matrix
                    if not bounds.contains(target):
                        raise ValueError("페이지 경계를 벗어난 텍스트 영역입니다.")
                    # Conservative guards: don't remove adjacent block text or annotations.
                    raster_only = region.get('patch') and all(s['font'] == 'OCR' for s in region['spans'])
                    overlays = []
                    for other in page_info["regions"]:
                        if other["id"] == region["id"]:
                            continue
                        if raster_only and other.get('overlay_safe'):
                            body_color = region.get('dominant_color', 0)
                            colors = [((body_color >> s) & 255) for s in (16,8,0)]
                            if max(colors)-min(colors) < 30:
                                overlays.append(other)
                                continue
                        if 'source_bbox' in region and (fitz.Rect(region['bbox']) & fitz.Rect(other['bbox'])).get_area() > .5:
                            if not manual or other.get('keep_original'):
                                raise ValueError('배치 영역이 이웃 영역과 겹칩니다. 겹침을 피하기 위해 원문을 유지했습니다.')
                            outcome['overridden_reasons'].append(f"배치 영역 겹침: {other['id']}")
                        if any((target & fitz.Rect(c["bbox"])).get_area() > fitz.Rect(c['bbox']).get_area()*.15
                               for s in other["spans"] for c in s["chars"] if c["c"].strip()):
                            if not manual or other.get('keep_original'):
                                raise ValueError("다른 텍스트 영역과 겹칩니다. 영역 분리가 필요합니다.")
                            outcome['overridden_reasons'].append(f"원문 영역 겹침: {other['id']}")
                    protected = [a.rect for a in original.annots() or []]
                    protected += [w.rect for w in original.widgets() or []]
                    protected += [l["from"] for l in original.get_links()]
                    protection_target=fitz.Rect(region['bbox']) if region.get('patch') else target
                    if any(protection_target.intersects(rect) for rect in protected):
                        raise ValueError("링크·주석·폼과 겹치는 영역은 원문을 유지합니다.")
                    with fitz.open() as scratch:
                        test_page = scratch.new_page(width=bounds.width, height=bounds.height)
                        if policy.mode == "replay":
                            replay(test_page, region, original, fonts)
                        else:
                            from .placement_quality import prefer_source_size
                            quality = prefer_source_size(original,region,text,policy) if parts else None
                            try:
                                outcome["scale"] = fit(test_page, region, text, policy)
                            except ValueError as initial_error:
                                from .placement_recovery import recover
                                scale, adjustment = recover(original,page_info,region,text,policy,initial_error)
                                outcome['adjustment'] = adjustment
                                if scale is None:
                                    raise ValueError(str(initial_error)+' '+adjustment.get('stop_reason','')) from initial_error
                                outcome['scale'] = scale
                                parts = region['typography']['parts']
                                outcome['layout_style']['blocks'] = [dict(bbox=p['bbox'],align=p['align'],
                                    font_size=p['font_size'],rotation=p.get('rotate',0),source_text=p['source_text']) for p in parts]
                            parts = region.get('typography',{}).get('parts',[]) if parts else []
                            if parts:
                                outcome['layout_style']['blocks'] = [dict(bbox=p['bbox'],align=p['align'],
                                    font_size=p['font_size'],rotation=p.get('rotate',0),source_text=p['source_text']) for p in parts]
                            outcome["status"] = "ADJUSTED" if outcome["scale"] < 0.999 else "FIT"
                            outcome["reason"] = ('원문 여백·행 위치·정렬과 반복 머리말의 서식을 반영했습니다.' if parts else
                                                 "셀/문단 안에서 대체 폰트로 조판했습니다. 혼합 서식은 대표 서식으로 통일됩니다.")
                            if region.get('typography', {}).get('spacing_relaxed'):
                                outcome['reason'] = '원문 행 위치로는 공간이 부족해 셀 안의 여백을 줄여 배치했습니다. 엔터는 유지합니다.'
                            elif any(p.get('rotate') == 90 for p in parts):
                                outcome['reason'] = '세로 항목명을 90° 회전해 글자 크기를 확보했습니다.'
                            elif any(p.get('adaptive_leader') for p in parts):
                                outcome['reason'] = '점선 길이·수량 칸을 조정하고 긴 항목명을 줄바꿈해 글자 크기를 확보했습니다.'
                            outcome['font_size'] = region['font_size'] * outcome['scale']
                            outcome['ocr'] = bool(region.get('ocr'))
                            if outcome['font_size'] < 5:
                                outcome['readability_warning']='5pt 미만의 작은 글자입니다. 확대해서 확인하세요.'
                            if outcome.get('adjustment',{}).get('status') == 'adjusted':
                                outcome.update(status='ADJUSTED',reason='공간 부족을 자동 조정한 뒤 글자 누락·경계·이웃 영역 검사를 통과했습니다.')
                            if quality:
                                source_size = min(quality['source_sizes'])
                                ratio = outcome['font_size']/source_size if source_size else 1
                                wrapped_heading = bool(quality['heading'] and quality.get('line_count',1)>1)
                                quality.update(source_font_size=source_size,rendered_font_size=outcome['font_size'],
                                    retained_ratio=ratio,space=region.get('space_plan'),
                                    wrapped_heading=wrapped_heading,
                                    review_required=ratio < (.9 if quality['heading'] else .85) or wrapped_heading)
                                outcome['typography_quality'] = quality
                                if quality['review_required']:
                                    outcome['readability_warning'] = ('경계와 읽기 크기를 지키기 위해 제목을 줄바꿈했습니다. 확인하세요.' if wrapped_heading
                                        else '빈 공간을 활용한 뒤에도 원문 대비 글자가 많이 축소됐습니다. 가독성을 확인하세요.')
                    if policy.mode == 'fit' and region.get('patch'):
                        patches[region['id']]=cleaned_patch(original,region,overlays)
                    approved.append(region)
                    if manual:
                        outcome['manual_applied'] = True
                        outcome['reason'] = '사용자가 확인한 번역문을 적용했습니다. 이웃 글자·표와의 겹침을 미리보기에서 확인하세요.'
                except Exception as exc:
                    outcome.update(status="NEEDS_REVIEW", reason=str(exc))
            # Delete every accepted region first, then insert; never redact fresh text.
            for region in approved:
                if region.get('patch') and all(s['font']=='OCR' for s in region['spans']):
                    continue  # Raster ink is masked; keep native watermark text intact.
                page.add_redact_annot(fitz.Rect(region.get('source_bbox',region["bbox"])), fill=False, cross_out=False)
            if approved:
                page.apply_redactions(images=0, graphics=0, text=0)
            for region in sorted(approved, key=lambda r: r['id'] in manual_ids):
                if policy.mode == "replay":
                    replay(page, region, original, fonts)
                else:
                    if region.get('patch'):
                        page.insert_image(fitz.Rect(region['bbox']), stream=patches[region['id']],keep_proportion=False)
                    fit(page, region, outcomes[region["id"]]["text"], policy)
        # Full stream deduplication compares artwork across every page. For a
        # page update only discard unreachable objects; retain existing compression.
        output.save(output_path, garbage=1 if reuse else 4, deflate=True)
    validation = validate(source_path, output_path, layout, outcomes,
                          reused_pages={p['page']: p for p in previous_report['validation']['pages']
                                        if p['page'] not in changed_pages} if reuse else None)
    record_render(layout,policy,outcomes)
    recovery = [r['adjustment'] for r in outcomes.values() if r.get('adjustment')]
    return {"mode": policy.mode, "policy": policy.model_dump(),
            'placement_recovery': {'attempted':len(recovery),'adjusted':sum(r['status']=='adjusted' for r in recovery)},
            "regions": list(outcomes.values()), "validation": validation, 'layout_style':layout_style,
            'render_scope': {'incremental': reuse, 'rendered_pages': sorted(changed_pages) if reuse else list(range(len(layout['pages'])))}}
