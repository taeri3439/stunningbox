"""Read-only adapter to the existing translator and DOCX renderer.

The lab uses its own region model as a shared input. This is a renderer comparison,
not a claim to reproduce the production vision/reconciliation pipeline.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / "backend"


def setup():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(BACKEND / ".env", override=False)
    if str(BACKEND) not in sys.path:
        sys.path.append(str(BACKEND))


def configuration():
    setup()
    from pipeline.llm_provider import openai_model, resolve_llm_provider
    from pipeline.translator import TRANSLATOR_MODEL
    provider = resolve_llm_provider()
    model = (openai_model(dedicated_env="OPENAI_TRANSLATOR_MODEL", shared_env="TRANSLATOR_MODEL", default="gpt-4o")
             if provider == "openai" else TRANSLATOR_MODEL if provider else None)
    return {"configured": bool(provider), "provider": provider, "model": model}


def shared_model(layout, source_lang):
    setup()
    from models import ParagraphBlock, SemanticDocument
    blocks = []
    for page in layout["pages"]:
        for region in page["regions"]:
            span = max(region["spans"], key=lambda s: len(s["text"]))
            if region.get('keep_original'):
                continue
            blocks.append(ParagraphBlock(
                block_id=region["id"], page=page["number"], text=region["text"],
                font_size=span["size"], font=span["font"], bold=bool(span["flags"] & 16),
                italic=bool(span["flags"] & 2), color=f"{span['color']:06x}",
            ))
    first = layout["pages"][0]
    regions = first["regions"]
    left = max(12, min((r["bbox"][0] for r in regions), default=36))
    right = max(12, first["width"] - max((r["bbox"][2] for r in regions), default=first["width"] - 36))
    # Don't turn a sparse one-line sample into a narrow body column.
    left, right = min(left, first["width"] * .15), min(right, first["width"] * .15)
    return SemanticDocument(source_lang=source_lang, blocks=blocks, page_width=first["width"],
                            page_height=first["height"], margin_left=left, margin_right=right,
                            margin_top=36, margin_bottom=36)


class CheckedTranslator:
    """Keep existing prompts/chunking but reject malformed successful responses."""
    def __init__(self, delegate):
        self.delegate = delegate

    def translate(self, texts, *args, **kwargs):
        result = self.delegate.translate(texts, *args, **kwargs)
        if len(result) != len(texts) or any(not isinstance(t, str) or not t.strip() for t in result):
            raise ValueError("번역 API가 비어 있거나 개수가 다른 결과를 반환했습니다.")
        return result


def translate_shared(layout, source_lang, target_lang, cache_dir, progress=None):
    config = configuration()
    from pipeline.translator import get_translator, load_glossary, translate_document, _is_atomic
    if not config["configured"]:
        raise ValueError("기존 backend/.env 또는 pdf-layout-lab/.env에 번역 API 설정이 필요합니다. 모의 번역은 사용하지 않습니다.")
    if not layout["region_count"]:
        raise ValueError("번역할 텍스트를 찾지 못했습니다. OCR 분석 결과를 확인하세요.")
    glossary = load_glossary(source_lang, target_lang)
    key_data = {"source": layout["source_hash"], "schema": layout["schema_version"],
                "adapter": 2, "layout_hash": hashlib.sha256(json.dumps(layout, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), "source_lang": source_lang, "target_lang": target_lang,
                "config": config, "glossary": glossary,
                "translator_code": hashlib.sha256((BACKEND / "pipeline" / "translator.py").read_bytes()).hexdigest()}
    cache_key = hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cache_dir.mkdir(exist_ok=True)
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists():
        result = json.loads(cache_path.read_text(encoding="utf-8"))
        return {**result, "cache_hit": True}
    model = shared_model(layout, source_lang)
    before = {b.block_id: b.text for b in model.blocks}
    translated = translate_document(model, target_lang, source_lang=source_lang,
                                    translator=CheckedTranslator(get_translator()), on_progress=progress)
    replacements = {r['id']:r['text'] for p in layout['pages'] for r in p['regions']}
    replacements.update({b.block_id: b.text for b in translated.blocks})
    unchanged = [key for key, text in replacements.items()
                 if key in before and text == before[key] and not _is_atomic(before[key])]
    result = {"id": cache_key, "source_lang": source_lang, "target_lang": target_lang,
              "provider": config["provider"], "model": config["model"], "replacements": replacements,
              "unchanged_ids": unchanged, "cache_hit": False}
    # Existing translators may return source text after an invalid single-item response.
    # Expose unchanged items for review rather than presenting them as verified translation.
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(cache_path)
    return result


def create_docx(layout, texts, source_lang, target_lang, output):
    setup()
    from renderers.docx_renderer import render_docx
    model = shared_model(layout, source_lang)
    model.target_lang = target_lang
    for block in model.blocks:
        block.text = texts[block.block_id]
    render_docx(model, str(output), str(output.parent))


def convert_docx(download_url, cache_key, output):
    """Use the same OnlyOffice conversion API and JWT helpers as production."""
    setup()
    import httpx
    import onlyoffice
    body = {"async": False, "filetype": "docx", "outputtype": "pdf", "key": cache_key,
            "url": download_url, "title": "comparison.docx"}
    token = onlyoffice.sign(body)
    if token:
        body["token"] = token
    try:
        with httpx.Client(timeout=120) as client:
            response = client.post(onlyoffice.DS_INTERNAL_URL.rstrip("/") + "/ConvertService.ashx",
                                   json=body, headers={"Accept": "application/json"})
            response.raise_for_status()
            result = response.json()
            if result.get("error") is not None:
                raise ValueError(f"OnlyOffice 변환 오류 코드 {result['error']}. 서비스의 JWT 및 실험실 DOCX 접근 주소를 확인하세요.")
            if not result.get("endConvert") or not result.get("fileUrl"):
                raise ValueError("OnlyOffice에서 완료된 PDF를 반환하지 않았습니다.")
            pdf = client.get(onlyoffice.ds_fetch_url(result["fileUrl"]))
            pdf.raise_for_status()
            output.write_bytes(pdf.content)
    except httpx.HTTPError as exc:
        # Never expose request bodies, signed URLs or credentials in API errors.
        raise RuntimeError("OnlyOffice 연결 또는 PDF 변환에 실패했습니다. 서비스 실행 상태와 LAB_PUBLIC_URL을 확인하세요. 번역과 DOCX는 저장되어 있습니다.") from exc


def public_url():
    setup()
    return os.environ.get("LAB_PUBLIC_URL", "http://host.docker.internal:8011").rstrip("/")
