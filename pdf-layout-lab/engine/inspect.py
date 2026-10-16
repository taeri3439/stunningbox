"""Physical extraction and conservative block regions, without an LLM or OCR."""
import hashlib
from pathlib import Path

import fitz


def inspect_pdf(path: Path) -> dict:
    with fitz.open(path) as doc:
        if not doc.is_pdf or doc.needs_pass:
            raise ValueError("암호가 없는 PDF 파일을 사용해 주세요.")
        if not 1 <= len(doc) <= 60:
            raise ValueError("실험실에서는 1~60페이지 PDF를 지원합니다.")
        pages = []
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        for number, page in enumerate(doc):
            if max(page.rect.width, page.rect.height) > 2500:
                raise ValueError("페이지 한 변이 2,500pt 이하인 PDF를 사용해 주세요.")
            regions = []
            redactions = any(a.type[0] == fitz.PDF_ANNOT_REDACT for a in page.annots() or [])
            for block_index, block in enumerate(page.get_text("rawdict")["blocks"]):
                if block["type"] != 0:
                    continue
                spans, lines, unsupported = [], [], []
                for line in block["lines"]:
                    if abs(line["dir"][0] - 1) > 0.001 or abs(line["dir"][1]) > 0.001:
                        unsupported.append("회전·세로 텍스트는 아직 재삽입하지 않습니다.")
                    line_text = []
                    for span in line["spans"]:
                        text = "".join(c["c"] for c in span["chars"])
                        spans.append({
                            "text": text, "bbox": list(span["bbox"]),
                            "origin": list(span["origin"]), "font": span["font"],
                            "size": span["size"], "color": span["color"],
                            "flags": span["flags"], "alpha": span.get("alpha", 255),
                            "chars": [{"c": c["c"], "origin": list(c["origin"]),
                                       "bbox": list(c["bbox"])} for c in span["chars"]],
                        })
                        line_text.append(text)
                    lines.append("".join(line_text))
                text = "\n".join(lines)
                if not text.strip():
                    continue
                if redactions:
                    unsupported.append("기존 삭제 주석이 있는 페이지는 수정하지 않습니다.")
                if any(s["alpha"] != 255 for s in spans):
                    unsupported.append("투명 텍스트는 아직 지원하지 않습니다.")
                box = fitz.Rect(block["bbox"])
                # v1 deliberately never expands a region into unmeasured whitespace.
                display = box * page.rotation_matrix
                regions.append({
                    "id": f"p{number + 1}-b{block_index}", "page": number,
                    "text": text, "bbox": list(box), "display_bbox": list(display),
                    "kind": "text_block", "spans": spans,
                    "font_size": max((s["size"] for s in spans), default=11),
                    "warnings": sorted(set(unsupported)),
                })
            pages.append({
                "number": number, "width": page.rect.width, "height": page.rect.height,
                "rotation": page.rotation, "mediabox": list(page.mediabox),
                "cropbox": list(page.cropbox), "regions": regions,
                "warnings": [] if regions else ["텍스트를 찾지 못했습니다. 스캔·윤곽선 글자일 수 있으며 OCR은 아직 지원하지 않습니다."],
            })
        return {"source_hash": digest, "schema_version": 1, "pages": pages,
                "region_count": sum(len(p["regions"]) for p in pages)}
