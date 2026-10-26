"""Local dashboard history and human review records, scoped to immutable PDF runs."""
from datetime import datetime, timezone
from typing import Literal
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


class ReviewRequest(BaseModel):
    run: str = Field(pattern=r'^[a-f0-9]{32}$')
    region_id: str = Field(min_length=1, max_length=150)
    decision: Literal['pending', 'approved', 'needs_fix']
    note: str = Field(default='', max_length=4000)


def timestamp(path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def router_for(web):
    router = APIRouter()

    def run_folder(document_id, run):
        path = web.folder(document_id)
        if not re.fullmatch(r'[a-f0-9]{32}', run):
            raise HTTPException(404, '실행 결과가 없습니다.')
        result = path / 'runs' / run
        if not (result / 'report.json').exists():
            raise HTTPException(404, '저장된 실행 결과가 없습니다.')
        return path, result

    @router.get('/api/history')
    def history(q: str = Query('', max_length=200), offset: int = Query(0, ge=0),
                limit: int = Query(10, ge=1, le=50),
                status: Literal['all', 'uploaded', 'ready', 'needs_review', 'running'] = 'all'):
        with web.LOCK:
            items = []
            for root, native in [(web.DATA, False), (web.native_api.DATA, True)]:
                if not root.exists():
                    continue
                for path in root.iterdir():
                    saved = path / 'state.json'
                    if not re.fullmatch(r'[a-f0-9]{32}', path.name) or not saved.exists():
                        continue
                    state = web.read_json(saved)
                    report = state.get('report') or {}
                    validation = report.get('validation') or {}
                    active = path.name in (web.native_api.ACTIVE if native else web.ACTIVE)
                    phase = ('running' if active else 'ready' if validation.get('status') == 'CHECKS_PASSED'
                             else 'needs_review' if report else 'uploaded')
                    if q.casefold() not in state['name'].casefold() or (status != 'all' and phase != status):
                        continue
                    items.append({'id': path.name, 'name': state['name'], 'native': native,
                                  'updated_at': timestamp(saved), 'status': phase,
                                  'latest_run': state.get('latest_run'),
                                  'elapsed_seconds': (report.get('processing') or {}).get('elapsed_seconds'),
                                  'source_lang': (state.get('comparison') or {}).get('source_lang'),
                                  'target_lang': (state.get('comparison') or {}).get('target_lang')})
            items.sort(key=lambda item: item['updated_at'], reverse=True)
            return {'total': len(items), 'items': items[offset:offset + limit]}

    @router.get('/api/documents/{document_id}/runs')
    def runs(document_id: str):
        with web.LOCK:
            path = web.folder(document_id)
            items = []
            for saved in (path / 'runs').glob('*/report.json'):
                if not (saved.parent / 'output.pdf').exists():
                    continue
                report = web.read_json(saved)
                items.append({'id': saved.parent.name, 'updated_at': timestamp(saved),
                              'mode': report.get('mode'),
                              'status': report.get('validation', {}).get('status'),
                              'manual_region': report.get('manual_edit', {}).get('region_id')})
            items.sort(key=lambda item: item['updated_at'], reverse=True)
            return {'items': items, 'latest_run': web.read_json(path / 'state.json').get('latest_run')}

    @router.get('/api/documents/{document_id}/reviews')
    def reviews(document_id: str, run: str):
        with web.LOCK:
            _, result = run_folder(document_id, run)
            saved = result / 'reviews.json'
            return {'run': run, 'items': web.read_json(saved) if saved.exists() else {}}

    @router.post('/api/documents/{document_id}/reviews')
    def save_review(document_id: str, request: ReviewRequest):
        with web.LOCK:
            path, result = run_folder(document_id, request.run)
            web.require_idle(document_id)
            if web.read_json(path / 'state.json').get('latest_run') != request.run:
                raise HTTPException(409, '새 결과가 생성되었습니다. 문서를 다시 불러온 후 검토하세요.')
            report = web.read_json(result / 'report.json')
            if request.region_id not in {item['id'] for item in report['regions']}:
                raise HTTPException(422, '이 실행에 없는 영역입니다.')
            saved = result / 'reviews.json'
            items = web.read_json(saved) if saved.exists() else {}
            items[request.region_id] = {'decision': request.decision, 'note': request.note,
                                       'updated_at': datetime.now(timezone.utc).isoformat()}
            temporary = saved.with_suffix('.tmp')
            web.write_json(temporary, items)
            temporary.replace(saved)
            return {'run': request.run, 'items': items}

    return router
