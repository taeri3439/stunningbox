"""Deterministic checks, not a claim of complete PDF semantic equivalence."""
import math

import fitz
from PIL import Image, ImageChops, ImageDraw

from .models import normalized


def image_of(page):
    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False, colorspace=fitz.csRGB)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def validate(source_path, output_path, layout, outcomes):
    pages = []
    with fitz.open(source_path) as source, fitz.open(output_path) as output:
        geometry_ok = len(source) == len(output)
        for pno, source_page in enumerate(source):
            out_page = output[pno]
            same_geometry = (source_page.rect == out_page.rect and
                             source_page.mediabox == out_page.mediabox and
                             source_page.cropbox == out_page.cropbox and
                             source_page.rotation == out_page.rotation)
            geometry_ok = geometry_ok and same_geometry
            diff = ImageChops.difference(image_of(source_page), image_of(out_page)).convert("L")
            changed = diff.point(lambda p: 255 if p > 12 else 0)
            outside = changed.copy()
            mask = ImageDraw.Draw(outside)
            issues = []
            output_chars = [c for block in out_page.get_text('rawdict')['blocks']
                            if block['type'] == 0 for line in block['lines']
                            for span in line['spans'] for c in span['chars']]
            for region in layout["pages"][pno]["regions"]:
                result = outcomes[region["id"]]
                if result['status'] == 'UNCHANGED':
                    result['text_check'] = True
                    continue
                expected = result["text"] if result["status"] != "NEEDS_REVIEW" else region["text"]
                # Larger extraction rect tolerates font ascender differences, while visual
                # damage outside the true region remains checked independently below.
                rect = fitz.Rect(region["bbox"])
                # Font ascenders can overlap an adjacent row although its ink
                # is outside this cell. Assign by baseline origin, not bbox overlap.
                got = ''.join(c['c'] for c in output_chars
                              if rect.contains(fitz.Point(c['origin'])))
                result["text_check"] = normalized(got) == normalized(expected)
                if not result["text_check"]:
                    issues.append(f"{region['id']}: 추출 텍스트 불일치 (누락 또는 배치 차이 확인 필요)")
                if result["status"] != "NEEDS_REVIEW":
                    b = fitz.Rect(region["display_bbox"]) + (-2, -2, 2, 2)
                    mask.rectangle((math.floor(b.x0), math.floor(b.y0),
                                    math.ceil(b.x1), math.ceil(b.y1)), fill=0)
            total = changed.width * changed.height
            changed_count = total - changed.histogram()[0]
            outside_count = total - outside.histogram()[0]
            if outside_count:
                issues.append("수정 영역 밖에 시각적 변화가 있습니다.")
            if not same_geometry:
                issues.append("페이지 크기 또는 회전이 달라졌습니다.")
            # Links / annotations are vulnerable to redaction; explicitly check count.
            if len(source_page.get_links()) != len(out_page.get_links()):
                issues.append("링크 개수가 달라졌습니다.")
            if len(list(source_page.annots() or [])) != len(list(out_page.annots() or [])):
                issues.append("주석 개수가 달라졌습니다.")
            pages.append({"page": pno, "changed_pixel_percent": round(changed_count / total * 100, 4),
                          "outside_changed_pixels": outside_count, "issues": issues})
        skipped = sum(r["status"] == "NEEDS_REVIEW" for r in outcomes.values())
        unchanged = sum(r['status'] == 'UNCHANGED' for r in outcomes.values())
        warnings = [w for p in layout["pages"] for w in p["warnings"]]
        if any(r.get('ocr_verified_by') for p in layout['pages'] for r in p['regions']):
            warnings.append('이미지 OCR 원문은 자동 인식 결과입니다. 작은 글자·숫자·고유명사·서명 오독 여부를 원본과 대조해야 합니다.')
        small=sum(bool(r.get('readability_warning')) for r in outcomes.values())
        if small:warnings.append(f'{small}개 영역은 5pt 미만입니다. 작은 도표 글자의 가독성을 확인하세요.')
        passed = geometry_ok and not skipped and not warnings and all(not p["issues"] for p in pages)
        return {"status": "CHECKS_PASSED" if passed else "NEEDS_REVIEW",
                "geometry_ok": geometry_ok, "pages": pages, "skipped": skipped,
                "processed": len(outcomes) - skipped, "total": len(outcomes), "warnings": warnings,
                'translated':len(outcomes)-skipped-unchanged,'unchanged':unchanged,
                "scope": "72dpi 영역 외 픽셀 비교·페이지 기하·영역별 추출 텍스트·링크/주석 개수 검사. 완전한 시각/접근성 보증은 아닙니다."}
