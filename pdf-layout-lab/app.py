"""Local comparison lab with a read-only bridge to existing translation/rendering."""
import hashlib
import json
import re
import secrets
import shutil
import threading
import time
import uuid
import sys
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

import fitz
from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from engine.inspect import inspect_pdf
from engine.models import CompareRequest, RenderRequest
from engine.region_edit import RegionEditRequest, edit_layout
from engine.service import render
from engine import legacy
from engine import translation_units
from engine.enhanced import prepare
from engine.ocr_review import correct_sources, correct_image_sources
from engine.docx_output import create_docx as create_layout_docx
from engine.smart_export import options as export_options
import native_api
import dashboard_api

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
LOCK = threading.Lock()  # MuPDF operations are serialized in this single-worker lab.
JOB_LOCK = threading.Lock()  # Progress must stay readable while MuPDF is busy.
native_api.LOCK = LOCK
JOBS = {}
ACTIVE = {}
DOWNLOAD_TOKENS = {}
EDITOR_SESSIONS = {}
EDITOR_KEYS = {}
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="layout-comparison")
app = FastAPI(title="PDF Layout Lab")
app.include_router(native_api.router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "host.docker.internal", "testserver"])
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def folder(document_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", document_id):
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    path = DATA / document_id
    if not (path / "layout.json").exists():
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    return path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def manifest(path):
    layout = read_json(path / "layout.json")
    state = read_json(path / "state.json")
    run_layout = path / 'runs' / (state.get('latest_run') or '_') / 'layout.json'
    if run_layout.exists():
        layout = read_json(run_layout)
    # Read-only compatibility projection for old runs; disk history is untouched.
    translation_units.ensure(layout)
    # Character-level data stays on disk; the browser only needs region geometry.
    for page in layout["pages"]:
        for region in page["regions"]:
            region["fonts"] = sorted({s["font"] for s in region.pop("spans")})
            for fragment in region.get('source_fragments', []):
                fragment.pop('spans', None)
    for region in layout['source_regions']:
        region.pop('spans',None)
        region.pop('mask_boxes',None)
    active = ACTIVE.get(path.name)
    exports = export_options(path / 'runs' / (state.get('latest_run') or '_'), state.get('report') or {})
    return {"id": path.name, **state, "layout": layout, "exports": exports, "active_job": active if active in JOBS else None}


def require_idle(document_id):
    if document_id in ACTIVE:
        raise HTTPException(409, "이 문서의 번역·비교 작업이 진행 중입니다.")


def source_file(path, kind, run=None):
    if kind == "baseline" and not read_json(path / "state.json").get("baseline"):
        raise HTTPException(404, "현재 실행에 연결된 기존 PDF가 없습니다.")
    if kind == "new":
        if not run or not re.fullmatch(r"[a-f0-9]{32}", run):
            raise HTTPException(404, "생성된 실행 결과가 없습니다.")
        file = path / "runs" / run / "output.pdf"
    else:
        file = path / f"{kind}.pdf"
    if not file.exists():
        raise HTTPException(404, "이 비교 파일은 아직 없습니다.")
    return file


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get('/editor')
def editor_page():
    return FileResponse(ROOT / 'static' / 'editor.html')


def lab_run(document_id, run):
    path = folder(document_id)
    if not re.fullmatch(r'[a-f0-9]{32}', run):
        raise HTTPException(404, '실행 결과가 없습니다.')
    result = path / 'runs' / run
    if not (result / 'layout.docx').exists():
        raise HTTPException(404, '새 DOCX를 먼저 생성하세요.')
    return path, result


def editor_session(token):
    session = EDITOR_SESSIONS.get(token)
    if not session or session['expires'] < time.monotonic():
        raise HTTPException(404, '편집 세션이 만료되었습니다. 편집기를 다시 여세요.')
    return session


@app.get('/api/documents/{document_id}/editor-config')
def lab_editor_config(document_id: str, run: str):
    legacy.setup()
    import onlyoffice
    if not onlyoffice.JWT_SECRET:
        raise HTTPException(503, 'OnlyOffice JWT 설정이 필요합니다. lab/.env의 ONLYOFFICE_JWT_SECRET을 문서 서버와 맞추세요.')
    with LOCK:
        path, result = lab_run(document_id, run)
        require_idle(document_id)
        token = secrets.token_urlsafe(32)
        key = onlyoffice.document_key(str(result / 'layout.docx'))
        expires = time.monotonic() + 86400
        for old in [k for k, v in EDITOR_SESSIONS.items() if v['expires'] < time.monotonic()]:
            EDITOR_SESSIONS.pop(old, None)
            DOWNLOAD_TOKENS.pop(old, None)
        EDITOR_SESSIONS[token] = {'document_id': document_id, 'run': run, 'key': key,
                                  'expires': expires, 'revision': 0}
        EDITOR_KEYS[(document_id, run)] = key
        DOWNLOAD_TOKENS[token] = (result / 'layout.docx', expires)
        config = {'documentType': 'word', 'document': {'fileType': 'docx', 'key': key,
                  'title': 'Layout Lab · ' + read_json(path / 'state.json')['name'] + '.docx',
                  'url': f'{legacy.public_url()}/api/conversion-input/{token}'},
                  'editorConfig': {'mode': 'edit', 'lang': 'ko',
                  'callbackUrl': f'{legacy.public_url()}/api/editor-callback/{token}',
                  'customization': {'forcesave': True, 'anonymous': {'request': False, 'label': 'Lab'}}}}
        config['token'] = onlyoffice.sign(config)
        docx_info = read_json(result / 'report.json').get('docx_output', {})
        return {'document_server_url': onlyoffice.PUBLIC_URL, 'config': config, 'session': token,
                'editing_scope':docx_info.get('scope'),
                'editable_original_regions':docx_info.get('editable_original_regions', 0)}


@app.post('/api/editor-callback/{token}')
async def lab_editor_callback(token: str, request: Request):
    legacy.setup()
    import onlyoffice
    import httpx
    try:
        body = onlyoffice.verify_callback(request.headers, await request.json())
    except Exception:
        raise HTTPException(403, '문서 서버 서명이 올바르지 않습니다.')
    with LOCK:
        session = dict(editor_session(token))
        if body.get('key') != session['key']:
            raise HTTPException(403, '편집 문서 키가 일치하지 않습니다.')
        if EDITOR_KEYS.get((session['document_id'], session['run'])) != session['key']:
            return {'error': 1}  # A newer editor opened this run; reject stale saves.
        _, result = lab_run(session['document_id'], session['run'])
    if body.get('status') not in (2, 6):
        return {'error': 0}
    if not isinstance(body.get('url'), str):
        return {'error': 1}
    # Resolve saved-file URLs against the configured DS, never an arbitrary host.
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(onlyoffice.ds_fetch_url(body['url']))
            response.raise_for_status()
        content = response.content
        if len(content) > 100 * 1024 * 1024:
            return {'error': 1}
        with ZipFile(BytesIO(content)) as archive:
            if 'word/document.xml' not in archive.namelist():
                return {'error': 1}
        with LOCK:
            if EDITOR_KEYS.get((session['document_id'], session['run'])) != session['key']:
                return {'error': 1}
            target = result / 'layout.docx'
            # Retain the generated version for an auditable comparison.
            if not (result / 'layout.generated.docx').exists():
                shutil.copyfile(target, result / 'layout.generated.docx')
            temporary = result / f'saved-{uuid.uuid4().hex}.tmp'
            temporary.write_bytes(content)
            temporary.replace(target)
            editor_session(token)['revision'] += 1
            report = read_json(result / 'report.json')
            report['docx_output'].update(edited=True, preview_status='stale')
            write_json(result / 'report.json', report)
            path = folder(session['document_id'])
            state = read_json(path / 'state.json')
            if state.get('latest_run') == session['run']:
                state['report'] = report
                write_json(path / 'state.json', state)
        return {'error': 0}
    except (httpx.HTTPError, BadZipFile, OSError):
        return {'error': 1}


@app.get('/api/editor-sessions/{token}')
def lab_editor_status(token: str):
    with LOCK:
        return {'revision': editor_session(token)['revision']}


@app.post('/api/editor-sessions/{token}/save')
def lab_editor_save(token: str):
    legacy.setup()
    import onlyoffice
    with LOCK:
        session = dict(editor_session(token))
        _, result = lab_run(session['document_id'], session['run'])
    try:
        status = onlyoffice.forcesave(str(result / 'layout.docx'), key=session['key'])
        return {'error': status.get('error', -1), 'revision': session['revision']}
    except Exception:
        raise HTTPException(503, '편집 내용을 저장하지 못했습니다. 문서 서버 연결을 확인하세요.')


@app.post('/api/documents/{document_id}/preview')
def refresh_docx_preview(document_id: str, run: str):
    with LOCK:
        path, result = lab_run(document_id, run)
        require_idle(document_id)
        ACTIVE[document_id] = 'preview'
        snapshot = result / f'preview-{uuid.uuid4().hex}.docx'
        shutil.copyfile(result / 'layout.docx', snapshot)
        snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    try:
        convert_run_docx(snapshot, snapshot.with_suffix('.pdf'))
        with LOCK:
            if hashlib.sha256((result / 'layout.docx').read_bytes()).hexdigest() != snapshot_hash:
                raise HTTPException(409, '미리보기 생성 중 문서가 변경되었습니다. 다시 저장해 주세요.')
            with fitz.open(snapshot.with_suffix('.pdf')) as preview:
                count = len(preview)
            snapshot.with_suffix('.pdf').replace(result / 'output.pdf')
            report = read_json(result / 'report.json')
            report['pdf_source'] = 'edited_docx'
            report['docx_output'].update(preview_status='ready', page_count=count, version=uuid.uuid4().hex)
            write_json(result / 'report.json', report)
            state = read_json(path / 'state.json')
            if state.get('latest_run') == run:
                state['report'] = report
                write_json(path / 'state.json', state)
            return {'ok': True, 'page_count': count}
    finally:
        snapshot.unlink(missing_ok=True)
        snapshot.with_suffix('.pdf').unlink(missing_ok=True)
        with LOCK:
            ACTIVE.pop(document_id, None)


@app.get("/api/documents")
def documents():
    with LOCK:
        result = []
        for path in sorted(DATA.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.is_dir() and (path / "state.json").exists() and (path / "layout.json").exists():
                state = read_json(path / "state.json")
                result.append({"id": path.name, "name": state["name"]})
        return result + native_api.list_documents()


@app.get("/api/config")
def config():
    try:
        return legacy.configuration()
    except (ImportError, ModuleNotFoundError):
        return {"configured": False, "provider": None, "model": None,
                "error": "번역 의존성이 없습니다. start.ps1 또는 기존 backend 가상환경으로 실행하세요."}


def update_job(job_id, **values):
    with JOB_LOCK:
        job = JOBS[job_id]
        if job.get('cancel_requested') and values.get('status') not in {'cancelled', 'failed'}:
            raise ComparisonCancelled()
        now = time.monotonic()
        previous = job.get('stage')
        stage = values.get('stage', previous)
        if stage != previous:
            if job.get('steps'):
                job['steps'][-1]['seconds'] = round(now - job['_stage_started'], 1)
                job['steps'][-1]['status'] = values['status'] if values.get('status') in {'failed', 'cancelled'} else 'finished'
            job.pop('done', None)
            job.pop('total', None)
            job['_stage_started'] = now
            if stage not in {'done', 'error', 'cancelled'}:
                job.setdefault('steps', []).append({'stage': stage, 'seconds': 0, 'status': 'running'})
        job.update(values)
        if values.get('status') in {'completed', 'partial', 'failed', 'cancelled'}:
            job['_finished'] = now


def job_snapshot(job_id):
    with JOB_LOCK:
        job = JOBS[job_id]
        now = job.get('_finished', time.monotonic())
        snapshot = {key: value for key, value in job.items() if not key.startswith('_')}
        snapshot['steps'] = [dict(step) for step in job.get('steps', [])]
        snapshot['elapsed_seconds'] = round(now - job.get('_started', now), 1)
        snapshot['stage_seconds'] = round(now - job.get('_stage_started', now), 1)
        if snapshot['steps'] and snapshot['steps'][-1]['status'] == 'running':
            snapshot['steps'][-1]['seconds'] = snapshot['stage_seconds']
        return snapshot


def document_cache(path):
    """Reuse content-identical uploads; migration only copies existing results."""
    digest=hashlib.sha256((path/'source.pdf').read_bytes()).hexdigest()
    cache=DATA/'_cache'/digest
    cache.mkdir(parents=True,exist_ok=True)
    for name in ['analysis','image-transcripts','ocr-transcripts','translations']:
        target=cache/name
        target.mkdir(exist_ok=True)
        previous=path/name
        if previous.exists():
            for file in previous.glob('*.json'):
                if not (target/file.name).exists():shutil.copyfile(file,target/file.name)
    return cache


def convert_run_docx(docx_path, output):
    token = secrets.token_urlsafe(32)
    with LOCK:
        DOWNLOAD_TOKENS[token] = (docx_path, time.monotonic() + 300)
    try:
        legacy.convert_docx(f"{legacy.public_url()}/api/conversion-input/{token}",
                            f"lab-{uuid.uuid4().hex}", output)
    finally:
        with LOCK:
            DOWNLOAD_TOKENS.pop(token, None)


def make_layout_output(run_dir, report, convert=True, on_preview=None):
    """The original renderer is read-only; the lab owns this separate DOCX path."""
    report['pdf_source'] = 'layout'
    with LOCK:
        shutil.copyfile(run_dir / 'output.pdf', run_dir / 'layout-intermediate.pdf')
        try:
            layout_file = run_dir / 'layout.json'
            result = create_layout_docx(run_dir / 'layout-intermediate.pdf', run_dir / 'layout.docx',
                                        read_json(layout_file) if layout_file.exists() else None, report)
        except Exception as exc:
            (run_dir / 'layout.docx').unlink(missing_ok=True)
            report['docx_output'] = {'ready': False, 'preview_status': 'unavailable',
                'warnings': [f'추가 DOCX 생성 실패 ({type(exc).__name__}). 기본 PDF 결과는 보존했습니다.'],
                'scope': '기본 PDF를 다운로드할 수 있습니다. DOCX Export는 생성되지 않았습니다.'}
            return
    report['docx_output'] = result
    if not convert:
        return
    if on_preview:
        on_preview()
    try:
        convert_run_docx(run_dir / 'layout.docx', run_dir / 'docx-preview.pdf')
        with LOCK, fitz.open(run_dir / 'docx-preview.pdf') as preview:
            if not preview.is_pdf or not len(preview):
                raise ValueError('DOCX 미리보기가 올바른 PDF가 아닙니다.')
            result.update(preview_status='ready', page_count=len(preview))
            # The earlier pixel checks apply to fitted PDF, not Word pagination.
            result['geometry_ok'] = len(preview) == len(result['pages'])
            if not result['geometry_ok']:
                result['warnings'].append('DOCX 변환 후 페이지 수가 달라졌습니다. 페이지 배치를 검토하세요.')
        # Keep the directly typeset PDF canonical. A Word round-trip can change
        # line spacing, glyphs and watermark artwork even when page count matches.
    except Exception:
        result.update(preview_status='unavailable',
                      preview_error='DOCX는 저장되었습니다. OnlyOffice 미리보기 변환에 실패하여 중간 PDF를 표시합니다.')
    check_output_text(run_dir / 'output.pdf', report)


def check_output_text(output, report, *, locked=False):
    from contextlib import nullcontext
    from engine.models import normalized
    with (nullcontext() if locked else LOCK), fitz.open(output) as pdf:
        pages = [normalized(page.get_text()) for page in pdf]
    for region in report['regions']:
        if region['status'] in {'FIT', 'ADJUSTED'}:
            number = region['page']
            region['pdf_text_found'] = number < len(pages) and normalized(region.get('logical_text', region['text'])) in pages[number]


def compare_worker(document_id, job_id, request):
    path = folder(document_id)
    run_dir = path / "runs" / job_id
    run_dir.mkdir(parents=True, exist_ok=True)
    download_token = None
    try:
        cache = document_cache(path)
        update_job(job_id, status='running', stage='analyze', message='표 셀·이미지·도형 글자 분석 중')
        layout = prepare(path / 'source.pdf', cache / 'analysis', request.source_lang,
                         lambda done,total: update_job(job_id, message=f'표·OCR 분석 {done+1}/{total} 페이지',done=done,total=total), LOCK)
        update_job(job_id, stage='ocr', message='이미지·도형 원문 확인 중 (저장된 OCR은 재사용)')
        layout = correct_image_sources(layout,path / 'source.pdf',cache / 'image-transcripts',
                         lambda d,n:update_job(job_id,stage='ocr',message=f'이미지 원문 읽기 {d}/{n}영역',done=d,total=n),LOCK,
                         prepare_progress=lambda p,n,d,k:update_job(job_id,stage='ocr_prepare',
                             message=f'OCR 이미지 준비 {p}/{n}페이지 · {d}/{k}영역',done=d,total=k))
        layout = correct_sources(layout,path / 'source.pdf',cache / 'ocr-transcripts',
                         lambda p,n,i,k: update_job(job_id,stage='ocr',message=f'이미지·도형 원문 보완 {p+1}/{n} 페이지 · {i}/{k}영역',done=i,total=k),LOCK)
        from engine.native_refinement import refine
        refine(layout,path / 'source.pdf')
        translation_units.ensure(layout)
        write_json(run_dir / 'layout.json', layout)
        update_job(job_id, status="running", stage="translate", message="기존 API 번역 로직 실행 중 (캐시가 있으면 재사용)")

        def progress(stage, done, total):
            if stage == 'translate_cache':
                update_job(job_id, message=f'저장 번역 {done}/{total}개 재사용 · 나머지 API 요청 준비', cached_segments=done, segment_count=total)
            elif stage == 'translate_retry':
                update_job(job_id, message=f'응답 형식 오류 복구 중 · {total}개 재요청 · 누적 API 호출 {done}회')
            else:
                update_job(job_id, message=f"번역 API 처리 {done}/{total} 묶음 · 응답 대기 포함", done=done, total=total)

        translation = legacy.translate_shared(layout, request.source_lang, request.target_lang,
                                               cache / "translations", progress)
        update_job(job_id, cache_hit=translation['cache_hit'], translation_stats=translation.get('recovery',{}))
        replacements = translation["replacements"]
        incomplete = set(translation.get('incomplete_ids', []))
        for page in layout['pages']:
            for region in page['regions']:
                if region['id'] in incomplete:
                    region.setdefault('warnings', []).append('번역 API의 응답 ID 누락·중복 또는 빈 번역문을 재요청해도 복구하지 못해 원문을 유지했습니다. 교체 텍스트 또는 DOCX에서 직접 수정할 수 있습니다.')
        policy = RenderRequest(mode="fit", replacements=replacements, min_scale=request.min_scale,
                               min_font_size=request.min_font_size, line_height=request.line_height,
                               preserve_spacing=request.preserve_spacing, harmonize_repeats=request.harmonize_repeats)
        update_job(job_id, stage="new_pdf", message="같은 번역문으로 새 PDF 조판·검증 중")
        with LOCK:
            report = render(path / "source.pdf", run_dir / "output.pdf", layout, policy,
                            progress=lambda d,n:update_job(job_id,stage='new_pdf',
                                message=f'PDF 배치·공간 부족 자동 조정 {d}/{n}페이지',done=d,total=n))
        # Keep the actual rendered text auditable without changing translation caches.
        translation = {**translation, 'replacements':report['policy']['replacements'],
                       'layout_style':report.get('layout_style')}
        text_hash = hashlib.sha256(json.dumps(translation['replacements'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        write_json(run_dir / 'translation.json', translation)
        write_json(run_dir / 'layout.json', layout)
        update_job(job_id, stage="new_docx", message="새 레이아웃을 편집 가능한 DOCX로 생성 중")
        make_layout_output(run_dir, report, on_preview=lambda: update_job(job_id,
            stage='new_preview', message='OnlyOffice에서 새 DOCX를 미리보기 PDF로 변환 중'),
            convert=request.include_baseline)
        if not request.include_baseline:
            check_output_text(run_dir / 'output.pdf', report)
        report.update(run_id=job_id, translation_id=translation["id"], text_hash=text_hash)
        if translation['unchanged_ids']:
            report['validation']['warnings'].append(f"{len(translation['unchanged_ids'])}개 번역문이 원문과 같습니다. 고유명사 또는 번역 누락 여부를 확인하세요.")
            report['validation']['status']='NEEDS_REVIEW'
        legacy_pdf = None
        legacy_error = None
        docx_ready = False
        project_result = None
        if request.include_baseline:
            update_job(job_id, stage="docx", message="기존 프로젝트 결과 확인·전체 파이프라인 실행 중")
            try:
                def project_progress(stage, done, total):
                    labels = {'analyze': '구조 분석', 'extract': '추출', 'translate': '번역'}
                    update_job(job_id, stage='docx', message=f"기존 프로젝트 · {labels.get(stage, stage)} {done}/{total}", done=done, total=total)
                project_result = legacy.project_output(path / 'source.pdf', request.source_lang, request.target_lang,
                    cache / 'project-pipeline', run_dir / 'comparison.docx', project_progress)
                docx_ready = True
                update_job(job_id, stage="legacy_pdf", message="OnlyOffice에서 기존 경로 PDF 변환 중")
                download_token = secrets.token_urlsafe(32)
                with LOCK:
                    DOWNLOAD_TOKENS[download_token] = (run_dir / "comparison.docx", time.monotonic() + 300)
                # Do NOT hold LOCK here: OnlyOffice fetches the DOCX from our HTTP server.
                legacy.convert_docx(f"{legacy.public_url()}/api/conversion-input/{download_token}",
                                    f"lab-{job_id}", run_dir / "legacy.pdf")
                with LOCK, fitz.open(run_dir / "legacy.pdf") as pdf:
                    if not pdf.is_pdf or pdf.needs_pass or len(pdf) == 0:
                        raise ValueError("변환 결과가 읽을 수 있는 PDF가 아닙니다.")
                    legacy_pdf = {"name": "기존 프로젝트 · 전체 파이프라인", "page_count": len(pdf),
                                  "version": job_id, **project_result}
            except ComparisonCancelled:
                raise
            except Exception as exc:
                legacy_error = (str(exc) if isinstance(exc, (RuntimeError, ValueError))
                                else f"기존 출력 생성 실패 ({type(exc).__name__}). DOCX 생성/변환 환경을 확인하세요.")
        comparison = {"run_id": job_id, "translation_id": translation["id"], "text_hash": text_hash,
                      "source_lang": request.source_lang, "target_lang": request.target_lang,
                      "provider": translation["provider"], "model": translation["model"],
                      "cache_hit": translation["cache_hit"], "unchanged_ids": translation["unchanged_ids"],
                      "incomplete_ids": translation.get('incomplete_ids', []),
                      "translation_recovery": translation.get('recovery', {}),
                      "retained_source_regions": report["validation"]["skipped"],
                      "legacy_status": "ready" if legacy_pdf else "unavailable" if request.include_baseline else "skipped",
                      "include_baseline": request.include_baseline,
                      "legacy_error": legacy_error, "docx_ready": docx_ready,
                      "new_docx_ready": report['docx_output']['ready'], "new_preview_status": report['docx_output']['preview_status'],
                      "project_result": project_result,
                      "scope": project_result['scope'] if project_result else ('기존 프로젝트 파이프라인 결과를 생성하지 못했습니다.' if request.include_baseline else '새 레이아웃만 생성했습니다. 기존 프로젝트 처리와 DOCX 미리보기 변환은 생략했습니다.')}
        report["comparison"] = comparison
        update_job(job_id, stage='finalize', message='검증 보고서와 비교 결과 저장 중')
        report['processing'] = job_snapshot(job_id)
        write_json(run_dir / "report.json", report)
        with LOCK:
            if legacy_pdf:
                shutil.copyfile(run_dir / "legacy.pdf", path / "baseline.pdf")
            state = read_json(path / "state.json")
            state.update(latest_run=job_id, report=report, baseline=legacy_pdf, comparison=comparison)
            write_json(path / "state.json", state)
            good = (report['validation']['status'] == 'CHECKS_PASSED'
                    and (not request.include_baseline or report['docx_output']['preview_status'] == 'ready')
                    and not report['docx_output']['warnings'])
            update_job(job_id, status="completed" if (legacy_pdf or not request.include_baseline) and good else "partial", stage="done",
                                message=f"번역 배치 {report['validation'].get('translated', report['validation']['processed'])}개 · 원문 유지 {report['validation'].get('unchanged', 0)}개 · 미반영 {report['validation']['skipped']}개. " +
                                ('자동 검사 통과. ' if good else '미처리/검증 이슈가 있어 검토가 필요합니다. ') +
                                ('기존 출력 비교 가능.' if legacy_pdf else '기존 PDF 변환 미완료.' if request.include_baseline else '새 레이아웃 생성 완료.'))
            report['processing'] = job_snapshot(job_id)
            write_json(run_dir / 'report.json', report)
            state['report'] = report
            write_json(path / 'state.json', state)
    except ComparisonCancelled:
        update_job(job_id, status='cancelled', stage='cancelled', message='작업을 중지했습니다. 이전 결과와 저장된 캐시는 유지됩니다.')
    except Exception as exc:
        # SDK errors can include request metadata. Persist only a safe type/category.
        from engine.failures import api_failure, file_failure
        diagnostic = api_failure(exc) or file_failure(exc)
        # Record the failing code location without SDK messages, document text or paths.
        import logging
        import traceback
        locations = [f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}'
                     for frame in traceback.extract_tb(exc.__traceback__)[-6:]]
        logging.getLogger(__name__).error('Job %s failed (%s); locations=%s',
                                        job_id, type(exc).__name__, ' > '.join(locations))
        if type(exc).__name__ == 'XMLSyntaxError':
            message = '문서 XML 생성·읽기에 실패했습니다. API 키 문제가 아닙니다. 저장된 번역은 재사용됩니다.'
        else:
            message = str(exc) if isinstance(exc, ValueError) else f"작업 실패 ({type(exc).__name__}). 처리 단계의 실행 환경을 확인하세요. 저장된 번역은 재사용됩니다."
        failed_stage = job_snapshot(job_id).get('stage')
        update_job(job_id, status="failed", stage="error", failed_stage=failed_stage,
                   **(diagnostic or {'message': message}))
    finally:
        with LOCK:
            ACTIVE.pop(document_id, None)
            if download_token:
                DOWNLOAD_TOKENS.pop(download_token, None)


@app.post("/api/documents/{document_id}/compare", status_code=202)
def start_comparison(document_id: str, request: CompareRequest):
    if request.source_lang == request.target_lang:
        raise HTTPException(400, "원문 언어와 번역 언어를 다르게 선택하세요.")
    with LOCK:
        path = folder(document_id)
        require_idle(document_id)
        settings = config()
        if not settings["configured"]:
            raise HTTPException(400, settings.get("error") or "번역 API 설정이 없습니다. backend/.env를 확인하세요.")
        job_id = uuid.uuid4().hex
        now = time.monotonic()
        with JOB_LOCK:
            JOBS[job_id] = {"id": job_id, "document_id": document_id, "status": "queued", "stage": "queued",
                            'include_baseline': request.include_baseline,
                            "message": "번역·비교 작업 대기 중", '_started': now, '_stage_started': now,
                            'steps': [{'stage': 'queued', 'seconds': 0, 'status': 'running'}]}
        ACTIVE[document_id] = job_id
        EXECUTOR.submit(compare_worker, document_id, job_id, request)
        return job_snapshot(job_id)


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with JOB_LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, "작업을 찾을 수 없습니다. 문서를 다시 선택하세요.")
    return job_snapshot(job_id)


class ComparisonCancelled(Exception):
    """Cooperative cancellation at a progress boundary; never kill an API request."""


@app.post('/api/jobs/{job_id}/cancel')
def cancel_comparison(job_id: str):
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, '작업을 찾을 수 없습니다.')
        if job['status'] in {'queued', 'running'}:
            # Final publication is short and atomic from the user's perspective.
            if job['stage'] == 'finalize':
                raise HTTPException(409, '결과 저장 중입니다. 완료 후 결과를 확인하세요.')
            job.update(cancel_requested=True, message='중지 요청됨 · 현재 처리 묶음이 끝나면 중지합니다.')
    return job_snapshot(job_id)


@app.get("/api/conversion-input/{token}")
def conversion_input(token: str):
    # Unpredictable five-minute capability URL, only while this conversion is active.
    with LOCK:
        entry = DOWNLOAD_TOKENS.get(token)
        if entry is None or entry[1] < time.monotonic():
            raise HTTPException(404, "변환 입력이 만료되었습니다.")
        path = entry[0]
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


async def pdf_bytes(file):
    data = await file.read(25 * 1024 * 1024 + 1)
    await file.close()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "25MB 이하 PDF를 사용해 주세요.")
    if b"%PDF-" not in data[:1024]:
        raise HTTPException(400, "유효한 PDF 파일을 선택해 주세요.")
    return data


@app.post("/api/documents")
async def upload(file: UploadFile = File(...)):
    if Path(file.filename or '').suffix.lower() in {'.doc', '.docx', '.hwp', '.hwpx', '.pptx'}:
        return await native_api.upload(file)
    data = await pdf_bytes(file)
    with LOCK:
        path = DATA / uuid.uuid4().hex
        path.mkdir()
        try:
            (path / "source.pdf").write_bytes(data)
            layout = inspect_pdf(path / "source.pdf")
            write_json(path / "layout.json", layout)
            write_json(path / "state.json", {"name": file.filename or "source.pdf", "baseline": None,
                                             "latest_run": None, "report": None})
            return manifest(path)
        except Exception as exc:
            # Only remove the newly created UUID directory within this lab's data root.
            shutil.rmtree(path)
            raise HTTPException(400, f"PDF 분석 실패: {exc}") from exc


@app.get("/api/documents/{document_id}")
def document(document_id: str):
    if re.fullmatch(r'[a-f0-9]{32}', document_id) and (native_api.DATA / document_id / 'state.json').exists():
        return native_api.document(document_id)
    with LOCK:
        return manifest(folder(document_id))


@app.post("/api/documents/{document_id}/baseline")
async def baseline(document_id: str, file: UploadFile = File(...)):
    path = folder(document_id)
    data = await pdf_bytes(file)
    with LOCK:
        require_idle(document_id)
        temp = path / "baseline-upload.pdf"
        try:
            temp.write_bytes(data)
            model = inspect_pdf(temp)
            temp.replace(path / "baseline.pdf")
            state = read_json(path / "state.json")
            state["baseline"] = {"name": file.filename, "page_count": len(model["pages"]),
                                 "version": uuid.uuid4().hex, "origin": "manual"}
            state["comparison"] = None
            write_json(path / "state.json", state)
            return manifest(path)
        except Exception as exc:
            temp.unlink(missing_ok=True)
            raise HTTPException(400, f"비교 PDF 분석 실패: {exc}") from exc


@app.post("/api/documents/{document_id}/render")
def generate(document_id: str, request: RenderRequest):
    started = time.monotonic()
    with LOCK:
        path = folder(document_id)
        require_idle(document_id)
        state = read_json(path / 'state.json')
        apply_ids = list(dict.fromkeys(([request.apply_region_id] if request.apply_region_id else []) + request.apply_region_ids))
        parent_run = state.get('latest_run')
        previous_layout = path / 'runs' / (parent_run or '_') / 'layout.json'
        layout = read_json(previous_layout if previous_layout.exists() else path / 'layout.json')
        if not apply_ids:
            prior_report = path / 'runs' / (parent_run or '_') / 'report.json'
            prior_text = read_json(prior_report).get('policy', {}).get('replacements', {}) if prior_report.exists() else {}
            for page in layout['pages']:
                for region in page['regions']:
                    text = request.replacements.get(region['id'])
                    if text is not None and text != prior_text.get(region['id']):
                        # A user edit carries intentional line breaks, even when
                        # its number of lines happens to match the source wraps.
                        region['user_linebreak_text'] = text
        if apply_ids:
            if not request.base_run or request.base_run != parent_run:
                raise HTTPException(409, '결과가 변경되었습니다. 문서를 다시 불러온 후 적용하세요.')
            previous = read_json(path / 'runs' / parent_run / 'report.json')
            if previous.get('docx_output', {}).get('edited'):
                raise HTTPException(409, 'DOCX 수동 편집 결과입니다. 추가 DOCX 편집 화면에서 수정해 주세요.')
            regions = {r['id']: r for p in layout['pages'] for r in p['regions']}
            for apply_id in apply_ids:
                if apply_id not in regions or regions[apply_id].get('kind') == 'protected_text':
                    raise HTTPException(422, '문서에 없는 영역이나 보호 영역은 적용할 수 없습니다.')
                text = request.replacements.get(apply_id, '')
                if not text.strip() or len(text) > 15000:
                    raise HTTPException(422, '각 영역의 교체 텍스트를 1~15,000자로 입력하세요.')
            saved = RenderRequest.model_validate(previous['policy'])
            request = saved.model_copy(update={
                'mode': 'fit', 'apply_region_id': None, 'apply_region_ids': [], 'base_run': None,
                'harmonize_repeats': False,
                'replacements': {**saved.replacements, **{key: request.replacements[key] for key in apply_ids}},
                'manual_regions': sorted(set(saved.manual_regions) | set(apply_ids)),
            })
        run_id = uuid.uuid4().hex
        run_dir = path / "runs" / run_id
        run_dir.mkdir(parents=True)
        try:
            state = read_json(path / 'state.json')
            changed_pages = {i for i, p in enumerate(layout['pages']) if any(r['id'] in apply_ids for r in p['regions'])}
            options = {'previous_pdf': path / 'runs' / parent_run / 'output.pdf',
                       'previous_report': previous, 'changed_pages': changed_pages} if apply_ids else {}
            report = render(path / "source.pdf", run_dir / "output.pdf", layout, request, **options)
            write_json(run_dir / 'layout.json',layout)
            report['pdf_source'] = 'layout'
            shutil.copyfile(run_dir / 'output.pdf', run_dir / 'layout-intermediate.pdf')
            report['docx_output'] = ({'ready': False, 'deferred': True, 'warnings': [],
                'preview_status': 'pending', 'scope': 'PDF 적용을 완료했습니다. DOCX 편집·내보내기가 필요하면 편집본을 생성하세요.'}
                if apply_ids else create_layout_docx(run_dir / 'output.pdf', run_dir / 'layout.docx', layout, report))
            check_output_text(run_dir / 'output.pdf', report, locked=True)
            if apply_ids:
                report['manual_edit'] = {'region_id': apply_ids[0] if len(apply_ids) == 1 else None,
                                         'region_ids': apply_ids, 'parent_run': parent_run}
            report['render_seconds'] = round(time.monotonic() - started, 2)
            report["run_id"] = run_id
            write_json(run_dir / "report.json", report)
            state = read_json(path / "state.json")
            state.update(latest_run=run_id, report=report)
            if not apply_ids:
                state["comparison"] = None
            write_json(path / "state.json", state)
            return manifest(path)
        except Exception as exc:
            shutil.rmtree(run_dir)
            raise HTTPException(400, f"재생성 실패: {exc}") from exc


@app.post('/api/documents/{document_id}/regions/edit')
def edit_regions(document_id: str, request: RegionEditRequest):
    started=time.monotonic()
    with LOCK:
        path=folder(document_id); require_idle(document_id)
        state=read_json(path/'state.json'); parent=state.get('latest_run')
        if request.base_run!=parent or not parent:
            raise HTTPException(409,'결과가 변경되었습니다. 문서를 다시 불러오세요.')
        previous=read_json(path/'runs'/parent/'report.json')
        if previous.get('docx_output',{}).get('edited'):
            raise HTTPException(409,'DOCX 편집 결과는 DOCX 편집 화면에서 수정하세요.')
        try:
            layout,region,number,originals=edit_layout(read_json(path/'runs'/parent/'layout.json'),request)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        ACTIVE[document_id]='region-edit'
    run_dir=None
    try:
        old_policy=RenderRequest.model_validate(previous['policy'])
        translation=None
        if request.action=='translate_apply':
            # Deliberate user merge: translate the confirmed sentence as a unit.
            import copy
            target=copy.deepcopy(region)
            if len(originals)>1:
                target['text']=' '.join(target['text'].splitlines())
            partial=translation_units.subset(layout,[region['id']])
            partial['pages'][0]['regions']=[target]
            translation=legacy.translate_shared(partial,request.source_lang,request.target_lang,
                                                document_cache(path)/'translations',force=True)
            if translation.get('incomplete_ids'):
                raise ValueError('선택 영역의 번역 응답을 복구하지 못했습니다. 기존 결과는 유지됩니다.')
            text=translation['replacements'][region['id']]
            translation_units.accept_translation(layout,translation)
        else:
            text=request.replacement if request.replacement is not None else '\n'.join(
                old_policy.replacements.get(r['id'],r['text']) for r in originals)
        if not text.strip(): raise ValueError('교체 텍스트를 입력하세요.')
        removed=set(request.region_ids)
        policy=old_policy.model_copy(update={'mode':'fit','harmonize_repeats':False,
            'replacements':{**{k:v for k,v in old_policy.replacements.items() if k not in removed},region['id']:text},
            'manual_regions':sorted((set(old_policy.manual_regions)-removed)|{region['id']})})
        with LOCK:
            if read_json(path/'state.json').get('latest_run')!=parent:
                raise HTTPException(409,'결과가 변경되어 편집을 중단했습니다.')
            run_id=uuid.uuid4().hex; run_dir=path/'runs'/run_id; run_dir.mkdir()
            report=render(path/'source.pdf',run_dir/'output.pdf',layout,policy,
                          previous_pdf=path/'runs'/parent/'output.pdf',previous_report=previous,changed_pages={number})
            write_json(run_dir/'layout.json',layout)
            shutil.copyfile(run_dir/'output.pdf',run_dir/'layout-intermediate.pdf')
            report.update(run_id=run_id,pdf_source='layout',render_seconds=round(time.monotonic()-started,2),
                          docx_output={'ready':False,'deferred':True,'warnings':[],'preview_status':'pending'},
                          region_edit={'parent_run':parent,'region_id':region['id'],'merged_from':request.region_ids,
                                       'translation_recovery':translation.get('recovery') if translation else None})
            check_output_text(run_dir/'output.pdf',report,locked=True)
            write_json(run_dir/'report.json',report)
            state.update(latest_run=run_id,report=report)
            write_json(path/'state.json',state)
            return manifest(path)
    except HTTPException:
        raise
    except Exception as exc:
        if run_dir and run_dir.exists(): shutil.rmtree(run_dir)
        raise HTTPException(400,f'영역 편집 실패: {type(exc).__name__}: {exc}') from exc
    finally:
        with LOCK: ACTIVE.pop(document_id,None)


class UnitEditRequest(BaseModel):
    base_run: str
    action: Literal['merge','split']
    region_ids: list[str] = Field(min_length=1,max_length=100)
    split_after: str | None = None


@app.post('/api/documents/{document_id}/units/edit')
def edit_translation_units(document_id: str, request: UnitEditRequest):
    # Group edits never invoke translation or distribute fragments of old English.
    # The edited units return to source text until explicitly translated/applied.
    with LOCK:
        path=folder(document_id); require_idle(document_id)
        state=read_json(path/'state.json'); parent=state.get('latest_run')
        if parent!=request.base_run or not parent: raise HTTPException(409,'결과가 변경되었습니다. 다시 불러오세요.')
        previous=read_json(path/'runs'/parent/'report.json')
        if previous.get('docx_output',{}).get('edited'): raise HTTPException(409,'DOCX 편집 화면에서 수정하세요.')
        layout=read_json(path/'runs'/parent/'layout.json'); translation_units.ensure(layout)
        try:
            if request.action=='split':
                if len(request.region_ids)!=1: raise ValueError('하나의 번역 묶음을 선택하세요.')
                added,number,original=translation_units.split_region(layout,request.region_ids[0],request.split_after)
                removed=[original['id']]
            else:
                selected=[r for p in layout['pages'] for r in p['regions'] if r['id'] in request.region_ids]
                if len(selected)<2: raise ValueError('같은 열의 두 번역 묶음 이상을 선택하세요.')
                if any(r['kind']!='paragraph' or r.get('patch') or r.get('structure') for r in selected):
                    raise ValueError('1차 기능에서는 같은 열의 일반 본문만 묶을 수 있습니다. 표 셀·이미지는 따로 유지하세요.')
                ordered=sorted(selected,key=lambda r:r['source_bbox'][1])
                for a,b in zip(ordered,ordered[1:]):
                    x,y=fitz.Rect(a['source_bbox']),fitz.Rect(b['source_bbox'])
                    if (min(x.x1,y.x1)-max(x.x0,y.x0)<min(x.width,y.width)*.6 or
                        y.y0<x.y0+a['font_size']*.5 or y.y0-x.y1>max(24,a['font_size']*2)):
                        raise ValueError('서로 다른 열이나 떨어진 본문은 하나의 번역 묶음으로 합칠 수 없습니다.')
                layout,region,number,originals=edit_layout(layout,RegionEditRequest(base_run=parent,region_ids=request.region_ids))
                region['logical_paragraph']=True
                for unit in layout['translation_units']:
                    if unit['id']==region['translation_unit_id']:
                        unit.update(flow=True,status='pending',translated_text=None,reason='번역 묶음 변경 · 다시 번역하거나 교체문을 입력하세요.')
                added=[region]; removed=[r['id'] for r in originals]
            old_policy=RenderRequest.model_validate(previous['policy'])
            policy=old_policy.model_copy(update={'mode':'fit','harmonize_repeats':False,
                'manual_regions':[i for i in old_policy.manual_regions if i not in removed],
                'replacements':{**{k:v for k,v in old_policy.replacements.items() if k not in removed},**{r['id']:r['text'] for r in added}}})
            run_id=uuid.uuid4().hex; run_dir=path/'runs'/run_id; run_dir.mkdir()
            report=render(path/'source.pdf',run_dir/'output.pdf',layout,policy,
                previous_pdf=path/'runs'/parent/'output.pdf',previous_report=previous,changed_pages={number})
            check_output_text(run_dir/'output.pdf',report,locked=True)
            write_json(run_dir/'layout.json',layout)
            shutil.copyfile(run_dir/'output.pdf',run_dir/'layout-intermediate.pdf')
            report.update(run_id=run_id,pdf_source='layout',unit_edit={'action':request.action,'parent_run':parent,'region_ids':[r['id'] for r in added]},
                          docx_output={'ready':False,'deferred':True,'warnings':[],'preview_status':'pending'})
            write_json(run_dir/'report.json',report)
            state.update(latest_run=run_id,report=report);write_json(path/'state.json',state)
            return manifest(path)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@app.post('/api/documents/{document_id}/editable-docx')
def rebuild_editable_docx(document_id: str, run: str):
    """Upgrade a generated DOCX in a new run, preserving PDF and prior edits."""
    with LOCK:
        path = folder(document_id)
        if not re.fullmatch(r'[a-f0-9]{32}', run):
            raise HTTPException(404, '실행 결과가 없습니다.')
        previous = source_file(path, 'new', run).parent
        require_idle(document_id)
        state = read_json(path / 'state.json')
        if state.get('latest_run') != run:
            raise HTTPException(409, '결과가 변경되었습니다. 문서를 다시 불러오세요.')
        report = read_json(previous / 'report.json')
        if report.get('docx_output', {}).get('edited'):
            raise HTTPException(409, '이미 편집한 DOCX는 덮어쓰지 않습니다. 기존 편집본을 이용하세요.')
        run_id = uuid.uuid4().hex
        result = path / 'runs' / run_id
        result.mkdir()
        try:
            for name in ('output.pdf', 'layout.json', 'translation.json'):
                if (previous / name).exists():
                    shutil.copyfile(previous / name, result / name)
            layout = read_json(result / 'layout.json' if (result / 'layout.json').exists() else path / 'layout.json')
            write_json(result / 'layout.json', layout)
            shutil.copyfile(result / 'output.pdf', result / 'layout-intermediate.pdf')
            report['docx_output'] = create_layout_docx(result / 'output.pdf', result / 'layout.docx', layout, report)
            report.update(run_id=run_id, docx_parent_run=run)
            write_json(result / 'report.json', report)
            state.update(latest_run=run_id, report=report, comparison=None)
            write_json(path / 'state.json', state)
            return manifest(path)
        except Exception as exc:
            shutil.rmtree(result)
            raise HTTPException(400, f'DOCX 편집본 생성 실패: {type(exc).__name__}') from exc


@app.get("/api/documents/{document_id}/pages/{kind}/{page_number}")
def page_image(document_id: str, kind: Literal["source", "new", "baseline"],
               page_number: int, run: str | None = None):
    with LOCK:
        file = source_file(folder(document_id), kind, run)
        with fitz.open(file) as doc:
            if not 0 <= page_number < len(doc):
                raise HTTPException(404, "대응하는 페이지가 없습니다.")
            pix = doc[page_number].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            return Response(pix.tobytes("png"), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/documents/{document_id}/download/{kind}")
def download(document_id: str, kind: Literal["source", "new", "baseline", "report", "docx", "new-docx", "translation"], run: str | None = None):
    path = folder(document_id)
    if kind in {"report", "docx", "new-docx", "translation"}:
        pdf = source_file(path, "new", run)
        name = {"report": "report.json", "docx": "comparison.docx", "new-docx": "layout.docx", "translation": "translation.json"}[kind]
        target = pdf.with_name(name)
        if not target.exists():
            raise HTTPException(404, "이 실행에는 요청한 결과가 없습니다.")
        return FileResponse(target, filename=name)
    file = source_file(path, kind, run)
    return FileResponse(file, filename=f"{kind}-{'review-' if kind == 'new' else ''}output.pdf", media_type="application/pdf")


@app.get('/api/documents/{document_id}/export/{format}')
def export_document(document_id: str, format: str, run: str):
    with LOCK:
        path = folder(document_id)
        pdf = source_file(path, 'new', run)
        report = read_json(pdf.with_name('report.json'))
        policy = export_options(pdf.parent, report)
        choice = next((item for item in policy['options'] if item['format'] == format), None)
        if choice is None:
            raise HTTPException(422, '이 문서에서 지원하지 않거나 편집 가능한 구조가 없는 출력 형식입니다.')
        if not choice['available']:
            raise HTTPException(409, choice['description'])
        target = pdf if format == 'pdf' else pdf.with_name('layout.' + format)
        name = Path(read_json(path / 'state.json')['name']).stem + '-layout.' + format
        return FileResponse(target, filename=name)


@app.post('/api/documents/{document_id}/smart-exports')
def prepare_smart_exports(document_id: str):
    from engine.pptx_output import eligible, create_pptx
    with LOCK:
        path = folder(document_id)
        require_idle(document_id)
        state = read_json(path / 'state.json')
        pdf = source_file(path, 'new', state.get('latest_run'))
        report = read_json(pdf.with_name('report.json'))
        if report.get('docx_output', {}).get('edited') and report['docx_output'].get('preview_status') != 'ready':
            raise HTTPException(409, '편집 후 PDF 미리보기를 먼저 갱신하세요.')
        if not eligible(pdf):
            raise HTTPException(422, '슬라이드 Export는 동일 크기의 가로형 페이지에만 제공됩니다. 현재 문서는 PDF·DOCX를 사용하세요.')
        ACTIVE[document_id] = 'smart-export'
        try:
            create_pptx(pdf, pdf.with_name('candidate.pptx'))
        except Exception:
            ACTIVE.pop(document_id, None)
            raise
        source_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    try:
        native_api.convert(pdf.with_name('candidate.pptx'), pdf.with_name('slide-preview.pdf'), 'pdf')
        with LOCK:
            with fitz.open(pdf) as before, fitz.open(pdf.with_name('slide-preview.pdf')) as after:
                if len(before) != len(after):
                    raise ValueError('슬라이드 페이지 수가 일치하지 않습니다.')
                for a,b in zip(before,after):
                    if abs(a.rect.width-b.rect.width)>1 or abs(a.rect.height-b.rect.height)>1:
                        raise ValueError('슬라이드 크기가 일치하지 않습니다.')
                    from engine.models import normalized
                    from collections import Counter
                    if sum((Counter(normalized(a.get_text())) - Counter(normalized(b.get_text()))).values()) > 5:
                        raise ValueError('변환된 슬라이드의 텍스트 누락 가능성이 있어 Export를 제외했습니다.')
            if source_hash != hashlib.sha256(pdf.read_bytes()).hexdigest():
                raise ValueError('원본 결과가 변경되었습니다. 다시 분석하세요.')
            report = read_json(pdf.with_name('report.json'))
            docx = report.get('docx_output') or {}
            if docx.get('edited') and docx.get('preview_status') != 'ready':
                raise ValueError('분석 중 DOCX가 편집되었습니다. 미리보기를 갱신한 후 다시 분석하세요.')
            pdf.with_name('candidate.pptx').replace(pdf.with_name('layout.pptx'))
            report['slide_output'] = {'verified': True, 'source_hash': source_hash}
            write_json(pdf.with_name('report.json'), report)
            state = read_json(path / 'state.json')
            state['report'] = report
            write_json(path / 'state.json', state)
            return manifest(path)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        with LOCK:
            ACTIVE.pop(document_id, None)
            pdf.with_name('candidate.pptx').unlink(missing_ok=True)


app.include_router(dashboard_api.router_for(sys.modules[__name__]))
