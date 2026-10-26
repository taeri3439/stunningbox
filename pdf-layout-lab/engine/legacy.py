"""Adapters for lab region translation and the actual project's PDF pipeline.

The comparison baseline uses a saved project DOCX or the complete project pipeline.
Production storage is read-only; newly generated artifacts stay in the lab.
"""
import hashlib
import json
import os
import sys
import shutil
import re
import logging
import uuid
from pathlib import Path
from .file_paths import io_path

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
    """Validate aligned results, checkpoint successes, and isolate bad responses."""
    def __init__(self, delegate, cache_dir=None, progress=None, refresh=False, paragraph_texts=()):
        self.delegate = delegate
        self.cache_dir = io_path(cache_dir) if cache_dir is not None else None
        self.progress = progress
        self.refresh = refresh
        self.paragraph_texts = set(paragraph_texts)
        self.review_texts = set()
        self.diagnostics = {'invalid_responses': 0, 'retried_segments': 0, 'cached_segments': 0,
                            'api_calls': 0, 'api_seconds': 0, 'pending_segments': 0}

    def _saved(self, text):
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha256(text.encode()).hexdigest() + '.json')

    def _recover(self, texts, args, kwargs, depth=0):
        """Never zip a count-mismatched response to inputs: the alignment is unknown."""
        if depth == 0:
            self._recovery_budget = 6
        else:
            if self._recovery_budget <= 0:
                return [None]*len(texts)
            self._recovery_budget -= 1
        try:
            import time
            started=time.monotonic()
            self.diagnostics['api_calls'] += 1
            got = self.delegate.translate(texts, *args, **kwargs)
        except json.JSONDecodeError:
            got = None  # Truncated tool arguments; smaller requests can recover.
        finally:
            self.diagnostics['api_seconds'] += round(time.monotonic()-started,2)
        aligned = isinstance(got, (list, tuple)) and len(got) == len(texts)
        values = [t.strip() if isinstance(t, str) and t.strip() else None for t in got] if aligned else [None] * len(texts)
        bad = [i for i, value in enumerate(values) if value is None]
        # Persist valid aligned items even if the subsequent recovery loses its connection.
        for source, value in zip(texts, values):
            saved = self._saved(source)
            if value is not None and saved is not None:
                saved.parent.mkdir(parents=True, exist_ok=True)
                temporary = saved.with_name(saved.name+'.'+uuid.uuid4().hex+'.tmp')
                temporary.write_text(json.dumps({'source': source, 'text': value}, ensure_ascii=False), encoding='utf-8')
                temporary.replace(saved)
        if bad:
            self.diagnostics['invalid_responses'] += 1
            if self.progress:
                self.progress('translate_retry', self.diagnostics['api_calls'], len(bad))
            logging.getLogger(__name__).warning('Translation response rejected: expected=%d received=%s invalid=%d depth=%d',
                len(texts), len(got) if isinstance(got, (list, tuple)) else 'invalid_type', len(bad), depth)
            if depth < 4:
                # Retry only invalid aligned entries; split the entire batch if alignment is lost.
                size = max(1, len(bad) // 2)
                for offset in range(0, len(bad), size):
                    indexes = bad[offset:offset + size]
                    self.diagnostics['retried_segments'] += len(indexes)
                    recovered = self._recover([texts[i] for i in indexes], args, kwargs, depth + 1)
                    for i, value in zip(indexes, recovered):
                        values[i] = value
        return values

    def translate(self, texts, *args, **kwargs):
        # Keep line boundaries, indentation, list markers and checkbox states out
        # of the model's output. Only their associated text is translated.
        plans, payload = [], []
        for text in texts:
            if text in self.paragraph_texts:
                # Preserve the complete source context, including words split
                # by physical PDF wraps. Geometry stays in the layout model.
                plans.append([len(payload)])
                payload.append(text)
                continue
            plan, line_start = [], True
            for part in re.split(r'(\r\n|\r|\n|[☐☑☒□■✓✔])', text):
                if not part:
                    continue
                if part in ('\n', '\r', '\r\n') or re.fullmatch(r'[☐☑☒□■✓✔]', part):
                    plan.append(part); line_start = part in ('\n', '\r', '\r\n')
                    continue
                prefix = re.match(r'[ \t]*', part)[0]
                body = part[len(prefix):]
                if line_start:
                    marker = re.match(r'(?:\(?\d{1,3}[.)]|[①-⑳•●▪-])\s+', body)
                    if marker:
                        prefix += marker[0]; body = body[len(marker[0]):]
                suffix = body[len(body.rstrip()):]
                body = body.strip()
                plan.append(prefix)
                if body and any(ch.isalpha() for ch in body):
                    plan.append(len(payload)); payload.append(body)
                else:
                    plan.append(body)  # Numeric values, dotted leaders and symbols are structural.
                plan.append(suffix)
                line_start = False
            plans.append(plan)
        result = {}
        pending = []
        kwargs = dict(kwargs)
        on_done = kwargs.pop('on_done', None)
        for text in dict.fromkeys(payload):
            saved = self._saved(text)
            if not self.refresh and saved is not None and saved.exists():
                try:
                    entry = json.loads(saved.read_text('utf-8'))
                    if entry.get('source') == text and isinstance(entry.get('text'), str) and entry['text'].strip():
                        result[text] = entry['text'].strip()
                        self.diagnostics['cached_segments'] += 1
                        continue
                except (OSError, ValueError, AttributeError):
                    pass
            pending.append(text)
        self.diagnostics['pending_segments'] = len(pending)
        if self.progress:
            self.progress('translate_cache', self.diagnostics['cached_segments'], len(dict.fromkeys(payload)))
        # Bound both item count and text size. One long cell must not inflate an 80-item request.
        batches, batch, chars = [], [], 0
        for text in pending:
            if batch and (len(batch) >= 40 or chars + len(text) > 6000):
                batches.append(batch); batch, chars = [], 0
            batch.append(text); chars += len(text)
        if batch:
            batches.append(batch)
        if self.progress:
            self.progress('translate', 0, max(1, len(batches)))
        for number, batch in enumerate(batches, 1):
            result.update(zip(batch, self._recover(batch, args, kwargs)))
            if self.progress:
                self.progress('translate', number, len(batches))
        if not batches and self.progress:
            self.progress('translate', 1, 1)
        outputs = []
        for text, plan in zip(texts, plans):
            if any(result[payload[item]] is None for item in plan if isinstance(item, int)):
                self.review_texts.add(text)
                outputs.append(text)  # Keep the whole region coherent and visibly untranslated.
            else:
                outputs.append(''.join(result[payload[item]] if isinstance(item, int) else item for item in plan))
        if on_done:
            for _ in range(max(1, (len(texts) + 79) // 80)):
                on_done()
        return outputs


def translate_shared(layout, source_lang, target_lang, cache_dir, progress=None, force=False):
    if layout.get('unit_schema'):
        setup()
        from .unit_translation import translate
        return translate(layout,source_lang,target_lang,cache_dir,progress,force)
    cache_dir = io_path(cache_dir)
    config = configuration()
    from pipeline.translator import get_translator, load_glossary, translate_document, _is_atomic, OpenAITranslator
    if not config["configured"]:
        raise ValueError("기존 backend/.env 또는 pdf-layout-lab/.env에 번역 API 설정이 필요합니다. 모의 번역은 사용하지 않습니다.")
    if not layout["region_count"]:
        raise ValueError("번역할 텍스트를 찾지 못했습니다. OCR 분석 결과를 확인하세요.")
    glossary = load_glossary(source_lang, target_lang)
    key_data = {"source": layout["source_hash"], "schema": layout["schema_version"],
                "adapter": 5, "layout_hash": hashlib.sha256(json.dumps(layout, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), "source_lang": source_lang, "target_lang": target_lang,
                "config": config, "glossary": glossary,
                "translator_code": hashlib.sha256((BACKEND / "pipeline" / "translator.py").read_bytes()).hexdigest()}
    cache_key = hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists() and not force:
        result = json.loads(cache_path.read_text(encoding="utf-8"))
        return {**result, "cache_hit": True}
    model = shared_model(layout, source_lang)
    before = {b.block_id: b.text for b in model.blocks}
    # Geometry, region IDs and analysis versions must not invalidate identical
    # source sentences. Languages, model, glossary and translator still do.
    segment_identity={k:key_data[k] for k in ('source_lang','target_lang','config','glossary','translator_code')}
    segment_identity['adapter']=5
    segment_key=hashlib.sha256(json.dumps(segment_identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    delegate=get_translator()
    owned_client=None
    if config['provider']=='openai' and type(delegate) is OpenAITranslator:
        from pipeline.translator import OpenAITranslator, _collapse_leaders
        from pipeline.llm_provider import openai_tool_call
        from openai import OpenAI
        class LabTranslator(OpenAITranslator):
            # Let CheckedTranslator own recovery; nested binary retries in both
            # adapters could multiply API calls and silently return source text.
            def _translate_chunk(self,chunk,system,tool):
                parsed,_=openai_tool_call(model=self.model,system=system,
                    user_content='Translate these segments (JSON array), returning an array of the same length and order:\n'+json.dumps([_collapse_leaders(c) for c in chunk],ensure_ascii=False),
                    tool_name=tool['name'],tool_description=tool['description'],parameters=tool['parameters'],
                    max_tokens=8000,client=self._client)
                return (parsed or {}).get('translations',[])
        owned_client=OpenAI(timeout=60,max_retries=1)
        delegate=LabTranslator(client=owned_client,model=config['model'])
    if hasattr(getattr(delegate,'client',None),'with_options'):
        delegate.client=delegate.client.with_options(timeout=60,max_retries=1)
    paragraph_ids = {r['id'] for p in layout['pages'] for r in p['regions']
                     if r.get('logical_paragraph') or r.get('user_geometry') and not r.get('structure')}
    checked = CheckedTranslator(delegate, cache_dir / 'segments' / segment_key, progress, refresh=force,
                                paragraph_texts=[text for key,text in before.items() if key in paragraph_ids])
    try:
        translated = translate_document(model, target_lang, source_lang=source_lang,
                                        translator=checked,
                                        style_context='\nEach input segment is an independent logical unit. '
                                        'Within a paragraph, PDF line breaks are soft visual wraps and can split a word. '
                                        'Read all lines together and translate the complete paragraph without splitting '
                                        'the translation by source lines. Preserve list markers and all facts; never '
                                        'combine separate input segments.')
    finally:
        if owned_client is not None: owned_client.close()
    replacements = {r['id']:r['text'] for p in layout['pages'] for r in p['regions']}
    replacements.update({b.block_id: b.text for b in translated.blocks})
    for key in paragraph_ids:
        if key in before and before[key] not in checked.review_texts and replacements[key] != before[key]:
            replacements[key] = re.sub(r'\s*\r?\n\s*', ' ', replacements[key]).strip()
    unchanged = [key for key, text in replacements.items()
                 if key in before and text == before[key] and not _is_atomic(before[key])]
    result = {"id": cache_key, "source_lang": source_lang, "target_lang": target_lang,
              "provider": config["provider"], "model": config["model"], "replacements": replacements,
              "unchanged_ids": unchanged, "cache_hit": False,
              "incomplete_ids": [key for key, text in before.items() if text in checked.review_texts],
              "recovery": checked.diagnostics}
    result['paragraph_units'] = sorted(paragraph_ids)
    # Existing translators may return source text after an invalid single-item response.
    # Expose unchanged items for review rather than presenting them as verified translation.
    if not result['incomplete_ids']:
        temporary = cache_path.with_name(cache_path.name+'.'+uuid.uuid4().hex+'.tmp')
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


def project_output(source, source_lang, target_lang, cache_dir, output, progress=None):
    """Use the real PDF pipeline, or snapshot an existing project's saved DOCX.

    Production storage is read-only. Fresh analysis, assets and translations live in
    the lab cache, never in the project's documents database or storage directory.
    """
    setup()
    from pipeline import process
    from renderers.docx_renderer import render_docx
    digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    storage = Path(os.environ.get('STORAGE_DIR', str(BACKEND / 'storage')))
    if not storage.is_absolute():
        storage = BACKEND / storage
    candidates = []
    for path in storage.iterdir() if storage.exists() else []:
        original, model, docx = path / 'source.pdf', path / 'source.json', path / f'output.{target_lang}.docx'
        if not all(p.is_file() for p in (original, model, docx)):
            continue
        try:
            if json.loads(model.read_text('utf-8')).get('source_lang') != source_lang:
                continue
            if hashlib.sha256(original.read_bytes()).hexdigest() == digest:
                candidates.append(docx)
        except (OSError, ValueError):
            continue
    if candidates:
        selected = max(candidates, key=lambda p: p.stat().st_mtime_ns)
        shutil.copyfile(selected, output)
        return {'origin': 'project_snapshot', 'document_id': selected.parent.name,
                'docx_sha256': hashlib.sha256(Path(output).read_bytes()).hexdigest(),
                'scope': '동일 PDF·언어의 기존 프로젝트 저장 DOCX 사본입니다. 기존 프로젝트의 수동 편집도 포함될 수 있습니다.'}

    code_hash = hashlib.sha256()
    for directory in ('pipeline', 'renderers'):
        for file in sorted((BACKEND / directory).glob('*.py')):
            code_hash.update(file.name.encode()); code_hash.update(file.read_bytes())
    code_hash.update((BACKEND / 'models.py').read_bytes())
    for file in sorted((BACKEND / 'glossary').glob('*.json')):
        code_hash.update(file.name.encode()); code_hash.update(file.read_bytes())
    signature = [digest, source_lang, target_lang, configuration(), code_hash.hexdigest(),
                 {key: os.environ.get(key, '') for key in ('ANALYZER_MODE', 'ANALYZER_MODEL', 'OPENAI_ANALYZER_MODEL')}]
    identity = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    root = Path(cache_dir)
    path = root / identity
    path.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, path / 'source.pdf')
    if not (path / 'source.json').exists():
        process.build_source_model(str(path / 'source.pdf'), identity, str(root),
                                   source_lang=source_lang, on_progress=progress)
    process.get_translated_model(str(root), identity, target_lang, on_progress=progress)
    docx = Path(process.output_docx_path(str(root), identity, target_lang))
    if not docx.exists():
        model = process.get_export_model(str(root), identity, target_lang)
        render_docx(model, str(docx), process.asset_dir(str(root), identity))
    shutil.copyfile(docx, output)
    return {'origin': 'project_pipeline', 'docx_sha256': hashlib.sha256(Path(output).read_bytes()).hexdigest(),
            'scope': '기존 프로젝트의 PDF 분석·번역·이미지 처리·DOCX 생성 파이프라인입니다. lab과 독립 번역하므로 번역문은 다를 수 있습니다.'}


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
