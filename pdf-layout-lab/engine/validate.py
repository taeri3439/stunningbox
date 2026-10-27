"""Deterministic checks, not a claim of complete PDF semantic equivalence."""
import math
import copy
from collections import Counter

import fitz
from PIL import Image, ImageChops, ImageDraw

from .models import normalized

VERSION = 4


def characters(page):
    return [{**char, 'font': span['font'], 'size': span['size'],
             'flags': span['flags'], 'color': span['color']}
            for block in page.get_text('rawdict')['blocks'] if block['type'] == 0
            for line in block['lines'] for span in line['spans'] for char in span['chars']]


def character_key(char):
    # Superscript bit 0 is inferred from neighboring spans on the page, not a
    # font style. Scratch and final pages can infer it differently for identical
    # glyph origins/sizes; all actual font/style attributes remain compared.
    return (char['c'], *[round(v, 2) for v in char['origin']], char['font'],
            round(char['size'], 2), char['flags'] & ~1, char['color'])


def check_placement(page, region, expected, protected_boxes=(), *, review_layout=False):
    """A fit return value alone does not prove that all glyphs are inside the box."""
    if normalized(page.get_text()) != normalized(expected):
        raise ValueError('조판 사전 검사에서 번역문 누락 또는 문자 순서 불일치를 발견했습니다.')
    bounds = fitz.Rect(region['bbox']) + (-1, -1, 1, 1)
    # rawdict boxes include the font's full ascender/descender and can extend
    # well above visible ink. Trace boxes use glyph-sized geometry instead.
    glyphs = [c for span in page.get_texttrace() for c in span['chars'] if chr(c[0]).strip()]
    # Text completeness and the physical page remain hard limits. A collision
    # estimate inside the page is reviewable after the safe candidates fail.
    if any(not page.rect.contains(fitz.Rect(c[3])) for c in glyphs):
        raise ValueError('페이지 밖으로 잘리는 글자가 있어 번역문 전체를 표시할 수 없습니다.')
    within = all(bounds.contains(fitz.Rect(c[3])) for c in glyphs)
    if not within and not review_layout:
        raise ValueError('배치 공간 경계를 벗어난 글자가 있습니다. 원문을 유지합니다.')
    protected_boxes = [box for box in protected_boxes if box.intersects(bounds)]
    overlap = sum((fitz.Rect(c[3]) & box).get_area() for c in glyphs for box in protected_boxes
                  if (fitz.Rect(c[3]) & box).get_area() > .05)
    if overlap and not review_layout:
        raise ValueError('번역문이 이웃 글자·이미지·도형과 겹칩니다. 원문을 유지합니다.')
    return {'text_complete': True, 'within_bounds': within, 'clear_of_neighbors': not overlap,
            'overlap_area': round(overlap, 3)}


def canonical(value):
    """Compare semantic values, excluding object numbers changed by PDF garbage collection."""
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items() if k not in ('xref', 'id')}
    if isinstance(value, (list, tuple, fitz.Rect, fitz.Point, fitz.Quad)):
        return [canonical(v) for v in value]
    return round(value, 3) if isinstance(value, float) else value


def page_structure(page):
    return {
        'links': canonical(page.get_links()),
        'annotations': [canonical(dict(type=a.type, rect=a.rect, info=a.info,
                        flags=a.flags, colors=a.colors, opacity=a.opacity,
                        border=a.border, vertices=a.vertices)) for a in page.annots() or []],
        'widgets': [canonical(dict(name=w.field_name, type=w.field_type,
                    value=w.field_value, flags=w.field_flags, rect=w.rect,
                    choices=w.choice_values)) for w in page.widgets() or []],
    }


def document_structure(document):
    return canonical(dict(outline=document.get_toc(simple=False),
                          page_labels=document.get_page_labels(), metadata=document.metadata))


def artwork(page):
    drawings = Counter(repr(canonical({k: v for k, v in d.items() if k not in ('seqno', 'layer')}))
                       for d in page.get_drawings())
    images = Counter(repr(canonical({k: i[k] for k in ('bbox', 'transform', 'width', 'height', 'digest')}))
                     for i in page.get_image_info(hashes=True))
    return drawings, images


def image_of(page):
    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False, colorspace=fitz.csRGB)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def retained_pixels(source_page, output_page, box):
    """Resolve 72dpi edge ambiguity using only pixels fully inside the region.

    A floor/ceil crop at 72dpi includes almost a point of a neighboring row.
    Recheck at 288dpi without that extra strip; true changes inside still fail.
    """
    images=[]
    for page in (source_page,output_page):
        pix=page.get_pixmap(matrix=fitz.Matrix(4,4),clip=box,alpha=False,colorspace=fitz.csRGB)
        im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        crop=(math.ceil(box.x0*4)-pix.x,math.ceil(box.y0*4)-pix.y,
              math.floor(box.x1*4)-pix.x,math.floor(box.y1*4)-pix.y)
        if crop[2]<=crop[0] or crop[3]<=crop[1]:return False
        images.append(im.crop(crop))
    diff=ImageChops.difference(*images).convert('L').point(lambda p:255 if p>12 else 0)
    return diff.getbbox() is None


def validate(source_path, output_path, layout, outcomes, reused_pages=None):
    pages = []
    with fitz.open(source_path) as source, fitz.open(output_path) as output:
        geometry_ok = len(source) == len(output)
        document_issues = []
        if not geometry_ok:
            document_issues.append('페이지 수가 달라졌습니다.')
        structure_ok = document_structure(source) == document_structure(output)
        if not structure_ok:
            document_issues.append('목차·책갈피 목적지, 페이지 번호 체계 또는 문서 메타데이터가 달라졌습니다.')
        for pno, source_page in enumerate(source):
            if pno >= len(output):
                pages.append({'page': pno, 'changed_pixel_percent': 0,
                              'outside_changed_pixels': 0, 'issues': ['출력 페이지가 없습니다.']})
                continue
            out_page = output[pno]
            same_geometry = (all(getattr(source_page, k) == getattr(out_page, k)
                                 for k in ('rect', 'mediabox', 'cropbox', 'trimbox', 'bleedbox', 'artbox'))
                             and source_page.rotation == out_page.rotation)
            geometry_ok = geometry_ok and same_geometry
            if same_geometry and reused_pages and pno in reused_pages:
                pages.append(copy.deepcopy(reused_pages[pno]))
                continue
            before_image, after_image = image_of(source_page), image_of(out_page)
            if before_image.size != after_image.size:
                pages.append({'page': pno, 'changed_pixel_percent': 0,
                              'outside_changed_pixels': 0, 'issues': ['페이지 크기 또는 회전이 달라졌습니다.']})
                continue
            diff = ImageChops.difference(before_image, after_image).convert("L")
            changed = diff.point(lambda p: 255 if p > 12 else 0)
            outside = changed.copy()
            mask = ImageDraw.Draw(outside)
            issues = []
            source_chars, output_chars = characters(source_page), characters(out_page)
            output_keys = Counter(character_key(c) for c in output_chars if c['c'].strip())
            replaced_origins = {tuple(round(v, 3) for v in c['origin'])
                                for r in layout['pages'][pno]['regions']
                                if outcomes[r['id']]['status'] not in ('UNCHANGED', 'NEEDS_REVIEW')
                                for s in r.get('spans', []) if s.get('font') != 'OCR'
                                for c in s.get('chars', []) if c.get('origin')}
            retained_keys = {character_key(c) for c in source_chars
                             if tuple(round(v, 3) for v in c['origin']) not in replaced_origins}
            # Retained watermark glyphs can cross a translated cell. Exclude
            # only their exact source character/origin pairs from body checks.
            def signature(char):
                return (char['c'], round(char['origin'][0], 2), round(char['origin'][1], 2))
            protected_chars = {signature(c) for r in layout['pages'][pno]['regions']
                               if r.get('overlay_safe') and r.get('keep_original')
                               for s in r['spans'] for c in s['chars']}
            for region in layout["pages"][pno]["regions"]:
                result = outcomes[region["id"]]
                if result.get('duplicate_of'):
                    continue
                if result['status'] in ('UNCHANGED', 'NEEDS_REVIEW'):
                    # OCR transcripts are not a native text layer. Verify retained
                    # ink visually, while native text must retain glyphs AND style.
                    boxes = [fitz.Rect(s['bbox']) for s in region.get('spans', [])
                             if s.get('font') != 'OCR' and s.get('bbox')]
                    expected_chars = Counter(character_key(c) for c in source_chars if c['c'].strip()
                                             and any(b.contains(fitz.Point(c['origin'])) for b in boxes))
                    if expected_chars:
                        retained = not (expected_chars - output_keys)
                        result['preservation_check'] = 'native_glyphs_and_style'
                    else:
                        b = fitz.Rect(region.get('source_bbox', region['bbox'])) * source_page.rotation_matrix
                        b &= source_page.rect
                        crop = (math.floor(b.x0), math.floor(b.y0), math.ceil(b.x1), math.ceil(b.y1))
                        # Use the same antialias tolerance as the outside-region
                        # check. A resampled neighbor can change a boundary pixel
                        # by one intensity level without changing retained ink.
                        retained = changed.crop(crop).getbbox() is None if not b.is_empty else False
                        if not retained and not b.is_empty:
                            retained = retained_pixels(source_page,out_page,b)
                        result['preservation_check'] = 'source_pixels'
                    result['text_check'] = retained
                    if not retained:
                        issues.append(f"{region['id']}: 원문 유지 영역의 글자·서식 또는 이미지가 달라졌습니다.")
                    continue
                expected = result.get('logical_text', result["text"]) if result["status"] != "NEEDS_REVIEW" else region["text"]
                # Larger extraction rect tolerates font ascender differences, while visual
                # damage outside the true region remains checked independently below.
                rect = fitz.Rect(region["bbox"])
                # Font ascenders can overlap an adjacent row although its ink
                # is outside this cell. Assign by baseline origin, not bbox overlap.
                got = ''.join(c['c'] for c in output_chars
                              if rect.contains(fitz.Point(c['origin'])) and signature(c) not in protected_chars
                              and character_key(c) not in retained_keys)
                if result.get('placement_keys'):
                    planned = Counter(tuple(k) for k in result['placement_keys'])
                    # Shared whitespace is legitimate: assign by the measured
                    # inserted glyphs, not by another overlapping model box.
                    got = ''.join(k[0] for k in result['placement_keys'])
                    result['text_check'] = not (planned-output_keys) and normalized(got)==normalized(expected)
                else:
                    result["text_check"] = normalized(got) == normalized(expected)
                if not result["text_check"]:
                    issues.append(f"{region['id']}: 추출 텍스트 불일치 (누락 또는 배치 차이 확인 필요)")
                if result["status"] != "NEEDS_REVIEW":
                    # Derive from current geometry, never a stale cached display box.
                    b = (fitz.Rect(region['bbox']) * source_page.rotation_matrix) + (-2, -2, 2, 2)
                    mask.rectangle((math.floor(b.x0), math.floor(b.y0),
                                    math.ceil(b.x1), math.ceil(b.y1)), fill=0)
                    # Moving a user placement also deliberately erases its
                    # source position. Both footprints belong to this edit.
                    b=(fitz.Rect(region.get('source_bbox',region['bbox']))*source_page.rotation_matrix)+(-2,-2,2,2)
                    mask.rectangle((math.floor(b.x0),math.floor(b.y0),math.ceil(b.x1),math.ceil(b.y1)),fill=0)
            total = changed.width * changed.height
            changed_count = total - changed.histogram()[0]
            outside_count = total - outside.histogram()[0]
            if outside_count:
                issues.append("수정 영역 밖에 시각적 변화가 있습니다.")
            if not same_geometry:
                issues.append("페이지 크기 또는 회전이 달라졌습니다.")
            original_structure, final_structure = page_structure(source_page), page_structure(out_page)
            for key, label in [('links', '링크 목적지·위치'), ('annotations', '주석 속성'), ('widgets', '폼 필드·값')]:
                if original_structure[key] != final_structure[key]:
                    issues.append(label + '이 달라졌습니다.')
                    structure_ok = False
            original_art, original_images = artwork(source_page)
            final_art, final_images = artwork(out_page)
            if original_art - final_art:
                issues.append('원본 벡터 도형·선·배경 속성이 보존되지 않았습니다.')
                structure_ok = False
            if original_images - final_images:
                issues.append('원본 이미지 내용·위치·크기가 보존되지 않았습니다.')
                structure_ok = False
            pages.append({"page": pno, "changed_pixel_percent": round(changed_count / total * 100, 4),
                          "outside_changed_pixels": outside_count, "issues": issues})
        for result in outcomes.values():
            if result.get('duplicate_of'):
                owner = outcomes[result['duplicate_of']]
                result['text_check'] = owner.get('text_check',False)
                result['preservation_check'] = owner.get('preservation_check')
            result['applied'] = result['status'] not in ('UNCHANGED','NEEDS_REVIEW')
            reasons = result.setdefault('review_reasons',[])
            if result.get('text_check') is False:
                reasons.append('출력 텍스트 또는 원문 유지 검사 불일치')
            result['review_reasons'] = list(dict.fromkeys(reasons))
            result['review_required'] = bool(reasons) or result['status']=='NEEDS_REVIEW'
        applied_review = sum(r['applied'] and r['review_required'] for r in outcomes.values())
        skipped = sum(r["status"] == "NEEDS_REVIEW" for r in outcomes.values())
        unchanged = sum(r['status'] == 'UNCHANGED' for r in outcomes.values())
        warnings = [w for p in layout["pages"] for w in p["warnings"]]
        if any(r.get('ocr_verified_by') for p in layout['pages'] for r in p['regions']):
            warnings.append('이미지 OCR 원문은 자동 인식 결과입니다. 작은 글자·숫자·고유명사·서명 오독 여부를 원본과 대조해야 합니다.')
        small=sum(bool(r.get('readability_warning')) for r in outcomes.values())
        manual = sum(bool(r.get('manual_applied')) for r in outcomes.values())
        if manual:
            warnings.append(f'{manual}개 영역은 사용자 확인으로 적용했습니다. 이웃 글자·표와 겹치거나 가려진 부분이 없는지 확인하세요.')
        if small:warnings.append(f'{small}개 영역은 글자 축소 또는 제목 줄바꿈 검토가 필요합니다.')
        passed = geometry_ok and structure_ok and not skipped and not applied_review and not warnings and all(not p["issues"] for p in pages)
        return {"status": "CHECKS_PASSED" if passed else "NEEDS_REVIEW",
                "version": VERSION,
                "geometry_ok": geometry_ok, "structure_ok": structure_ok,
                "issues": document_issues, "pages": pages, "skipped": skipped,
                'applied_review': applied_review,
                "processed": len(outcomes) - skipped, "total": len(outcomes), "warnings": warnings,
                'translated':len(outcomes)-skipped-unchanged,'unchanged':unchanged,
                "scope": "72dpi 영역 외 픽셀·페이지 기하·번역문·유지 영역 원문/서식·이미지/벡터·링크 목적지·주석·폼·목차·페이지 번호 체계 검사. PDF 태그 트리, 읽기 순서와 의미상 번역 품질은 보증하지 않습니다."}
