from pathlib import Path

import fitz

from .models import RenderRequest
from .typesetter import FontResolver, fit, replay
from .validate import validate
from .enhanced import cleaned_patch


def render(source_path: Path, output_path: Path, layout: dict, policy: RenderRequest) -> dict:
    ids = {r["id"] for p in layout["pages"] for r in p["regions"]}
    if set(policy.replacements) - ids:
        raise ValueError("현재 문서에 없는 영역 ID가 포함되어 있습니다.")
    if any(not t.strip() or len(t) > 15000 for t in policy.replacements.values()):
        raise ValueError("교체 텍스트는 비어 있지 않은 15,000자 이하 문자열이어야 합니다.")
    outcomes = {}
    with fitz.open(source_path) as source, fitz.open(source_path) as output:
        fonts = FontResolver(source)
        for pno, page_info in enumerate(layout["pages"]):
            page, original = output[pno], source[pno]
            approved = []
            patches = {}
            for region in page_info["regions"]:
                text = region["text"] if policy.mode == "replay" else policy.replacements.get(region["id"], region["text"])
                outcome = {"id": region["id"], "page": pno, "text": text,
                           "status": "FIT", "scale": 1.0, "reason": ""}
                outcomes[region["id"]] = outcome
                if region.get('warnings'):
                    outcome.update(status='NEEDS_REVIEW',reason=' '.join(region['warnings']))
                    continue
                if region['text']=='[OCR]' and not region.get('keep_original'):
                    outcome.update(status='NEEDS_REVIEW',reason='이미지 원문 인식이 완료되지 않았습니다.')
                    continue
                if policy.mode == 'fit' and (region.get('keep_original') or text == region['text']):
                    outcome.update(status='UNCHANGED',reason='번역 변경이 없는 원문/기호를 보존했습니다.',text=region['text'])
                    continue
                try:
                    if policy.mode == 'replay' and region.get('ocr'):
                        raise ValueError('OCR 영역은 원문 문자 재삽입 대신 FIT 번역 모드를 사용하세요.')
                    if region["warnings"]:
                        raise ValueError(" ".join(region["warnings"]))
                    target = fitz.Rect(region.get('source_bbox', region["bbox"]))
                    bounds = original.rect * original.derotation_matrix
                    if not bounds.contains(target):
                        raise ValueError("페이지 경계를 벗어난 텍스트 영역입니다.")
                    # Conservative guards: don't remove adjacent block text or annotations.
                    for other in page_info["regions"]:
                        if other["id"] == region["id"]:
                            continue
                        if 'source_bbox' in region and (fitz.Rect(region['bbox']) & fitz.Rect(other['bbox'])).get_area() > .5:
                            raise ValueError('배치 영역이 이웃 영역과 겹칩니다. 겹침을 피하기 위해 원문을 유지했습니다.')
                        if any((target & fitz.Rect(c["bbox"])).get_area() > fitz.Rect(c['bbox']).get_area()*.15
                               for s in other["spans"] for c in s["chars"] if c["c"].strip()):
                            raise ValueError("다른 텍스트 영역과 겹칩니다. 영역 분리가 필요합니다.")
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
                            outcome["scale"] = fit(test_page, region, text, policy)
                            outcome["status"] = "ADJUSTED" if outcome["scale"] < 0.999 else "FIT"
                            outcome["reason"] = "셀/문단 안에서 대체 폰트로 조판했습니다. 혼합 서식은 대표 서식으로 통일됩니다."
                            outcome['font_size'] = region['font_size'] * outcome['scale']
                            outcome['ocr'] = bool(region.get('ocr'))
                            if outcome['font_size'] < 5:
                                outcome['readability_warning']='5pt 미만의 작은 글자입니다. 확대해서 확인하세요.'
                    if policy.mode == 'fit' and region.get('patch'):
                        patches[region['id']]=cleaned_patch(original,region)
                    approved.append(region)
                except Exception as exc:
                    outcome.update(status="NEEDS_REVIEW", reason=str(exc))
            # Delete every accepted region first, then insert; never redact fresh text.
            for region in approved:
                page.add_redact_annot(fitz.Rect(region.get('source_bbox',region["bbox"])), fill=False, cross_out=False)
            if approved:
                page.apply_redactions(images=0, graphics=0, text=0)
            for region in approved:
                if policy.mode == "replay":
                    replay(page, region, original, fonts)
                else:
                    if region.get('patch'):
                        page.insert_image(fitz.Rect(region['bbox']), stream=patches[region['id']],keep_proportion=False)
                    fit(page, region, outcomes[region["id"]]["text"], policy)
        output.save(output_path, garbage=4, deflate=True)
    validation = validate(source_path, output_path, layout, outcomes)
    return {"mode": policy.mode, "policy": policy.model_dump(),
            "regions": list(outcomes.values()), "validation": validation}
