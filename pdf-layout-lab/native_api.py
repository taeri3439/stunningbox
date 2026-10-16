"""Lab-only original-format processing and verified export artifacts."""
import json
import secrets
import shutil
import threading
import time
import uuid
import re
from collections import Counter
from zipfile import ZipFile
from pathlib import Path

import fitz
import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from engine import legacy
from engine import native_document as native

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'native-data'
TOKENS = {}
LOCK = threading.Lock()
ACTIVE = set()
router = APIRouter()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def folder(identifier):
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise HTTPException(404, '문서가 없습니다.')
    path = DATA / identifier
    if not (path / 'state.json').exists():
        raise HTTPException(404, '문서가 없습니다.')
    return path


def list_documents():
    if not DATA.exists():
        return []
    return [{'id': p.name, 'name': read(p / 'state.json')['name'], 'native': True}
            for p in DATA.iterdir() if p.is_dir() and (p / 'state.json').exists()]


def convert(source, output, target):
    legacy.setup()
    import onlyoffice
    token = secrets.token_urlsafe(32)
    with LOCK:
        TOKENS[token] = (source, time.monotonic() + 600)
    body = {'async': False, 'filetype': source.suffix[1:], 'outputtype': target,
            'key': 'native-' + uuid.uuid4().hex, 'title': source.name,
            'url': f'{legacy.public_url()}/api/native-input/{token}'}
    signed = onlyoffice.sign(body)
    if signed:
        body['token'] = signed
    try:
        with httpx.Client(timeout=180) as client:
            response = client.post(onlyoffice.DS_INTERNAL_URL.rstrip('/') + '/ConvertService.ashx', json=body,
                                   headers={'Accept': 'application/json'})
            response.raise_for_status()
            result = response.json()
            if result.get('error') is not None or not result.get('endConvert') or not result.get('fileUrl'):
                raise ValueError('설치된 변환기가 이 파일의 변환을 완료하지 못했습니다.')
            response = client.get(onlyoffice.ds_fetch_url(result['fileUrl']))
            response.raise_for_status()
            if len(response.content) > 100 * 1024 * 1024:
                raise ValueError('변환 결과가 너무 큽니다.')
            output.write_bytes(response.content)
        if target == 'pdf':
            with LOCK, fitz.open(output) as pdf:
                if not pdf.is_pdf or pdf.needs_pass or not 1 <= len(pdf) <= 60:
                    raise ValueError('PDF 결과는 암호 없는 1~60페이지여야 합니다.')
        else:
            native.validate(output, target)
    except Exception:
        output.unlink(missing_ok=True)
        raise ValueError('변환 또는 결과 검증에 실패했습니다. 해당 Export는 표시하지 않습니다.') from None
    finally:
        with LOCK:
            TOKENS.pop(token, None)


@router.get('/api/native-input/{token}')
def conversion_input(token: str):
    with LOCK:
        entry = TOKENS.get(token)
        if not entry or entry[1] < time.monotonic():
            raise HTTPException(404, '변환 입력이 만료되었습니다.')
    return FileResponse(entry[0])


async def upload(file: UploadFile):
    kind = Path(file.filename or '').suffix.lower().lstrip('.')
    content = await file.read(25 * 1024 * 1024 + 1)
    await file.close()
    if kind not in native.FORMATS or len(content) > 25 * 1024 * 1024:
        raise HTTPException(400, '25MB 이하 DOC·DOCX·HWP·HWPX·PPTX를 사용하세요.')
    path = DATA / uuid.uuid4().hex
    path.mkdir(parents=True)
    source = path / ('source.' + kind)
    source.write_bytes(content)
    try:
        native.validate(source, kind)
        warnings = []
        try:
            await run_in_threadpool(convert, source, path / 'source.pdf', 'pdf')
        except ValueError as exc:
            warnings.append(str(exc))
        state = {'id': path.name, 'native': True, 'name': Path(file.filename).name,
                 'source_format': kind, 'can_translate': kind in native.EDITABLE,
                 'source_preview': (path / 'source.pdf').exists(), 'latest_run': None,
                 'warnings': warnings, 'result': None}
        if not state['can_translate']:
            state['warnings'].append('현재 엔진은 이 형식으로 번역 저장할 수 없습니다. 원본 보존 및 호환 형식 변환만 가능합니다.')
        if kind == 'hwpx':
            state['warnings'].append('HWPX 번역은 실험 기능입니다. 실제 한글 앱에서도 줄바꿈·표·글상자를 확인하세요.')
        write(path / 'state.json', state)
        return state
    except Exception as exc:
        shutil.rmtree(path)
        raise HTTPException(400, str(exc)) from exc


@router.get('/native')
def native_page():
    return FileResponse(ROOT / 'static' / 'native.html')


@router.get('/api/native/{identifier}')
def document(identifier: str):
    with LOCK:
        state = read(folder(identifier) / 'state.json')
        return {**state, 'busy': identifier in ACTIVE}


class ProcessRequest(BaseModel):
    translate: bool = False
    source_lang: str = 'ko'
    target_lang: str = 'en'


@router.post('/api/native/{identifier}/process')
def process(identifier: str, request: ProcessRequest):
    path = folder(identifier)
    with LOCK:
        if identifier in ACTIVE:
            raise HTTPException(409, '이 문서를 처리하고 있습니다.')
        state = read(path / 'state.json')
        if request.translate and not state['can_translate']:
            raise HTTPException(422, '원본 형식으로 번역 저장하는 엔진이 없습니다.')
        if request.translate and (request.source_lang == request.target_lang or
                                  request.source_lang not in {'ko','en','ja','zh','es','fr','de','vi'} or
                                  request.target_lang not in {'ko','en','ja','zh','es','fr','de','vi'}):
            raise HTTPException(422, '지원하는 서로 다른 언어를 선택하세요.')
        ACTIVE.add(identifier)
    run = uuid.uuid4().hex
    result_dir = path / 'runs' / run
    result_dir.mkdir(parents=True)
    kind = state['source_format']
    source, output = path / ('source.' + kind), result_dir / ('output.' + kind)
    try:
        report = {'mode': 'translated' if request.translate else 'original', 'warnings': [], 'exports': [],
                  'source_lang': request.source_lang if request.translate else None,
                  'target_lang': request.target_lang if request.translate else None}
        if request.translate:
            if not legacy.configuration()['configured']:
                raise ValueError('번역 API 설정이 없습니다.')
            from pipeline.translator import get_translator, load_glossary
            translator = legacy.CheckedTranslator(get_translator())
            glossary = load_glossary(request.source_lang, request.target_lang)
            report.update(native.translate_package(source, output, kind,
                lambda texts: translator.translate(texts, request.source_lang, request.target_lang, glossary)))
            native.validate(output, kind)
        else:
            shutil.copyfile(source, output)
        report['exports'].append({'format': kind, 'label': kind.upper() + ' · 원본 형식',
                                  'description': '원본 파일과 동일한 바이트입니다.' if not request.translate else '원본 서식·개체를 유지한 번역 문서입니다. 배치 검토가 필요합니다.'})
        for target in native.candidates(kind):
            try:
                if target == 'pdf' and not request.translate and state['source_preview']:
                    shutil.copyfile(path / 'source.pdf', result_dir / 'output.pdf')
                else:
                    convert(output, result_dir / ('output.' + target), target)
                report['exports'].append({'format': target, 'label': target.upper(),
                                          'description': '변환·파일 구조 검증 완료. 서식은 미리보기에서 확인하세요.'})
            except ValueError:
                report['warnings'].append(target.upper() + ' 변환을 검증하지 못해 선택지에서 제외했습니다.')
        report['preview'] = (result_dir / 'output.pdf').exists()
        if report['preview'] and state['source_preview']:
            with LOCK, fitz.open(path / 'source.pdf') as before, fitz.open(result_dir / 'output.pdf') as after:
                report['source_pages'], report['output_pages'] = len(before), len(after)
                same_geometry = len(before) == len(after) and all(a.rect == b.rect for a,b in zip(before,after))
                report['same_page_geometry'] = same_geometry
                if not same_geometry:
                    report['warnings'].append('원본과 페이지 수 또는 크기가 달라졌습니다. 배치를 검토하세요.')
        if report['preview'] and request.translate:
            from engine.models import normalized
            with ZipFile(output) as package:
                expected = ''.join(node.text for name in native.parts(package.namelist(), kind)
                                   for node in native.text_nodes(native.parse(package.read(name)), kind))
            with LOCK, fitz.open(result_dir / 'output.pdf') as pdf:
                visible = ''.join(page.get_text() for page in pdf)
                outside = sum(not page.rect.contains(fitz.Rect(block[:4])) for page in pdf
                              for block in page.get_text('blocks') if block[6] == 0)
            missing = sum((Counter(normalized(expected)) - Counter(normalized(visible))).values())
            report['text_check'] = {'unmatched_characters': missing, 'outside_blocks': outside}
            if missing:
                report['warnings'].append(f'미리보기에서 번역 텍스트 {missing}자를 대응하지 못했습니다. 글꼴·숨김·넘침을 확인하세요.')
            if outside:
                report['warnings'].append(f'페이지 밖으로 벗어난 텍스트 블록이 {outside}개 있습니다.')
        if request.translate and not report['preview']:
            report['warnings'].append('렌더링 검증을 완료하지 못했습니다. 원본 형식 결과는 검토용이며 배치 보존을 확인할 수 없습니다.')
        report.update(run=run, default_format=kind, review_required=request.translate or not report['preview'])
        write(result_dir / 'report.json', report)
        state.update(latest_run=run, result=report)
        with LOCK:
            write(path / 'state.json', state)
        return state
    except Exception as exc:
        shutil.rmtree(result_dir)
        raise HTTPException(400, '처리를 완료하지 못했습니다: ' + str(exc)) from exc
    finally:
        with LOCK:
            ACTIVE.discard(identifier)


def run_folder(path, run):
    if not re.fullmatch(r'[a-f0-9]{32}', run):
        raise HTTPException(404, '실행 결과가 없습니다.')
    result = path / 'runs' / run
    if not (result / 'report.json').exists():
        raise HTTPException(404, '실행 결과가 없습니다.')
    return result


@router.get('/api/native/{identifier}/export/{kind}')
def export(identifier: str, kind: str, run: str):
    path = folder(identifier)
    result = run_folder(path, run)
    report = read(result / 'report.json')
    if kind not in {item['format'] for item in report['exports']}:
        raise HTTPException(422, '검증되지 않은 Export입니다.')
    state = read(path / 'state.json')
    suffix = '-translated' if report['mode'] == 'translated' else '-preserved'
    return FileResponse(result / ('output.' + kind), filename=Path(state['name']).stem + suffix + '.' + kind)


@router.get('/api/native/{identifier}/preview/{which}')
def preview(identifier: str, which: str, run: str = ''):
    path = folder(identifier)
    if which not in {'source', 'output'}:
        raise HTTPException(404)
    target = path / 'source.pdf' if which == 'source' else run_folder(path, run) / 'output.pdf'
    if not target.exists():
        raise HTTPException(404, '미리보기를 생성하지 못했습니다.')
    return FileResponse(target, media_type='application/pdf')
