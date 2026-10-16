# Viewer Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. TDD.

**Goal:** 재번역/용어 모드의 번역본 패널을 OnlyOffice ConvertService가 변환한 PDF(편집기와 동일 엔진)의 페이지 이미지 + bbox 오버레이로 교체해 편집기와 100% 시각 일치.

**Architecture:** `ds_convert.py`(DS ConvertService 클라이언트, 신선도 캐시) → `pdf_map.py`(페이지 PNG 렌더 + 순서 기반 세그먼트/용어 bbox 매핑) → `main.py` 라우트 4종 → `TranslatedPages.tsx`가 원본 패널과 동일한 퍼센트-오버레이 방식으로 상호작용 재현. 편집 모드·원본 패널·HTML preview 엔드포인트는 무변경.

**Tech Stack:** FastAPI, httpx, PyMuPDF(fitz), python-jwt(기존 onlyoffice.py), React/Next 16, Tailwind v4.

## Global Constraints

- 절대 `git add -A` 금지. 보호 파일 무변경/무스테이징: `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, `backend/_*.py` 스크래치, `test-pdfs/`, 루트 이미지.
- 테스트는 결정적, 네트워크/실 API 호출 금지(httpx·DS 몽키패치; dev-local 통합 테스트만 예외로 skippable).
- `iter_segments`/`build_segment_index_map` 무변경. 기존 `/preview`·`/view`·`/segments` 계약 무변경.
- 백엔드 테스트: `backend/`에서 `./.venv/Scripts/python.exe -m pytest …`; 마지막에 전체 스위트(기존 15개 test_auth/test_documents_store 실패 제외).
- 프런트 검증: `frontend/`에서 `npm run build` 통과 (테스트 프레임워크 없음).
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- bbox 좌표 규약(전 태스크 공통): 페이지 분수 `{page:int(0-base), x, y, w, h}` — `extractor.segment_source_bbox`와 동일.

---

### Task 1: `ds_convert.py` — ConvertService 클라이언트 (TDD)

**Files:**
- Create: `backend/pipeline/ds_convert.py`
- Test: `backend/tests/test_ds_convert.py`

**Interfaces:**
- Produces: `ensure_pdf(storage_dir: str, doc_id: str, lang: str) -> str` (pdf 경로 반환), `pdf_path(storage_dir, doc_id, lang) -> str`, `class DsConvertError(RuntimeError)` (`.code`). Task 4가 소비.

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_ds_convert.py`):

```python
"""ConvertService client: request shape, freshness cache, error mapping."""
import json
import os

import pytest

import onlyoffice
from pipeline import ds_convert


class FakeResp:
    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _setup(tmp_path, monkeypatch, convert_payload=None, pdf_bytes=b"%PDF-fake"):
    doc_dir = tmp_path / "doc1"
    doc_dir.mkdir()
    (doc_dir / "output.en.docx").write_bytes(b"docx-bytes")
    posts, gets = [], []
    payload = convert_payload or {
        "endConvert": True, "percent": 100, "fileType": "pdf",
        "fileUrl": "http://onlyoffice-internal/cache/files/x/output.pdf?md5=a",
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append({"url": url, "body": json})
        return FakeResp(payload=payload)

    def fake_get(url, timeout=None):
        gets.append(url)
        return FakeResp(content=pdf_bytes)

    monkeypatch.setattr(ds_convert.httpx, "post", fake_post)
    monkeypatch.setattr(ds_convert.httpx, "get", fake_get)
    monkeypatch.setattr(onlyoffice, "JWT_SECRET", "testsecret")
    monkeypatch.setattr(onlyoffice, "DS_INTERNAL_URL", "http://ds-test:8080")
    monkeypatch.setattr(onlyoffice, "BACKEND_URL", "http://backend-test:9000")
    return str(tmp_path), posts, gets


def test_converts_and_writes_pdf_with_version_sidecar(tmp_path, monkeypatch):
    storage, posts, gets = _setup(tmp_path, monkeypatch)
    out = ds_convert.ensure_pdf(storage, "doc1", "en")
    assert out == ds_convert.pdf_path(storage, "doc1", "en")
    assert open(out, "rb").read() == b"%PDF-fake"
    body = posts[0]["body"]
    assert body["async"] is False and body["filetype"] == "docx" and body["outputtype"] == "pdf"
    assert body["url"] == "http://backend-test:9000/api/documents/doc1/docx?lang=en"
    assert body["key"].endswith("-pdf") and len(body["key"]) <= 128
    assert body["token"]  # signed because JWT_SECRET set
    assert posts[0]["url"] == "http://ds-test:8080/ConvertService.ashx"
    # fileUrl host rewritten to DS_INTERNAL_URL before download
    assert gets[0].startswith("http://ds-test:8080/cache/files/")
    assert open(out + ".key", encoding="utf-8").read().strip()


def test_fresh_pdf_skips_conversion(tmp_path, monkeypatch):
    storage, posts, _ = _setup(tmp_path, monkeypatch)
    ds_convert.ensure_pdf(storage, "doc1", "en")
    ds_convert.ensure_pdf(storage, "doc1", "en")
    assert len(posts) == 1  # second call served from freshness cache


def test_docx_change_reconverts(tmp_path, monkeypatch):
    storage, posts, _ = _setup(tmp_path, monkeypatch)
    ds_convert.ensure_pdf(storage, "doc1", "en")
    docx = os.path.join(storage, "doc1", "output.en.docx")
    with open(docx, "wb") as fh:
        fh.write(b"docx-bytes-v2!")  # size+mtime change -> new document_key
    ds_convert.ensure_pdf(storage, "doc1", "en")
    assert len(posts) == 2


def test_ds_error_raises(tmp_path, monkeypatch):
    storage, _, _ = _setup(tmp_path, monkeypatch, convert_payload={"error": -3})
    with pytest.raises(ds_convert.DsConvertError) as ei:
        ds_convert.ensure_pdf(storage, "doc1", "en")
    assert ei.value.code == -3


def test_missing_docx_raises(tmp_path, monkeypatch):
    storage, _, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError):
        ds_convert.ensure_pdf(storage, "nope", "en")
```

- [ ] **Step 2: Run to verify failure:** `./.venv/Scripts/python.exe -m pytest tests/test_ds_convert.py -v` → `ModuleNotFoundError: pipeline.ds_convert`.

- [ ] **Step 3: Implement** `backend/pipeline/ds_convert.py`:

```python
"""OnlyOffice ConvertService client: output.{lang}.docx -> output.{lang}.pdf.

The DS converter (x2t) is the same layout engine the embedded editor uses, so
the produced PDF paginates exactly like the editor screen — viewers built on
this PDF match the editor pixel-for-pixel. Freshness is keyed on the docx
version hash so a saved edit or re-render triggers exactly one reconversion.
"""
from __future__ import annotations

import os

import httpx

import onlyoffice


class DsConvertError(RuntimeError):
    def __init__(self, code):
        super().__init__(f"OnlyOffice conversion failed (error {code})")
        self.code = code


def pdf_path(storage_dir: str, doc_id: str, lang: str) -> str:
    return os.path.join(storage_dir, doc_id, f"output.{lang}.pdf")


def ensure_pdf(storage_dir: str, doc_id: str, lang: str) -> str:
    """Convert the translated docx to PDF via the DS ConvertService iff stale."""
    docx = os.path.join(storage_dir, doc_id, f"output.{lang}.docx")
    if not os.path.exists(docx):
        raise FileNotFoundError(docx)
    out = pdf_path(storage_dir, doc_id, lang)
    version = onlyoffice.document_key(docx)
    key_file = out + ".key"
    if os.path.exists(out):
        try:
            with open(key_file, encoding="utf-8") as fh:
                if fh.read().strip() == version:
                    return out
        except OSError:
            pass
    data = _convert(doc_id, lang, version)
    tmp = out + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, out)
    with open(key_file, "w", encoding="utf-8") as fh:
        fh.write(version)
    return out


def _convert(doc_id: str, lang: str, version: str) -> bytes:
    base = onlyoffice.BACKEND_URL.rstrip("/")
    body = {
        # Measured ~2.2s for a 21-page docx on the local DS -> synchronous is fine.
        "async": False,
        "filetype": "docx",
        "outputtype": "pdf",
        # DS caches conversion results by key: unique per docx version, and the
        # "-pdf" suffix keeps it distinct from the editor session key.
        "key": f"{version}-pdf",
        "url": f"{base}/api/documents/{doc_id}/docx?lang={lang}",
        "title": f"output.{lang}.docx",
    }
    token = onlyoffice.sign(body)
    if token:
        body["token"] = token
    url = onlyoffice.DS_INTERNAL_URL.rstrip("/") + "/ConvertService.ashx"
    r = httpx.post(url, json=body, headers={"Accept": "application/json"}, timeout=120)
    r.raise_for_status()
    data = r.json()
    if data.get("error") is not None:
        raise DsConvertError(data["error"])
    if not data.get("endConvert") or not data.get("fileUrl"):
        raise DsConvertError("incomplete")
    got = httpx.get(onlyoffice.ds_fetch_url(data["fileUrl"]), timeout=120)
    got.raise_for_status()
    return got.content
```

주의: `onlyoffice.sign`/`document_key`/`ds_fetch_url`은 module-level 상수 `JWT_SECRET`/`DS_INTERNAL_URL`/`BACKEND_URL`을 읽으므로 테스트는 `monkeypatch.setattr(onlyoffice, ...)`로 주입한다(이미 위 테스트가 그렇게 함). `_convert`는 이 상수들을 **호출 시점에** 읽어야 한다(모듈 import 시점 복사 금지).

- [ ] **Step 4: Run tests:** `./.venv/Scripts/python.exe -m pytest tests/test_ds_convert.py -v` → 5 PASS.
- [ ] **Step 5: Commit:** `git add backend/pipeline/ds_convert.py backend/tests/test_ds_convert.py` → `feat(viewer): OnlyOffice ConvertService client (docx→pdf, version-keyed cache)`

---

### Task 2: `pdf_map.py` — 페이지 PNG 렌더 + 매니페스트 (TDD)

**Files:**
- Create: `backend/pipeline/pdf_map.py`
- Test: `backend/tests/test_pdf_map.py`

**Interfaces:**
- Produces: `render_pages(pdf_path: str, out_dir: str, dpi: int = 150) -> dict` — 매니페스트 `{"version": str, "page_w": float, "page_h": float, "pages": [{"n": int, "name": str}]}` 반환, `out_dir/tpage{N}.png` + `out_dir/manifest.json` 생성. Task 4가 소비.

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_pdf_map.py`에 아래를 생성 — Task 3에서 같은 파일에 추가됨):

```python
"""pdf_map: page PNG rendering + manifest freshness."""
import json
import os

import fitz  # PyMuPDF

from pipeline import pdf_map


def _make_pdf(path, texts_per_page):
    doc = fitz.open()
    for texts in texts_per_page:
        page = doc.new_page(width=595, height=842)  # A4 pt
        y = 72
        for t in texts:
            page.insert_text((72, y), t, fontsize=12)
            y += 24
    doc.save(str(path))
    doc.close()


def test_renders_pages_and_manifest(tmp_path):
    pdf = tmp_path / "out.pdf"
    _make_pdf(pdf, [["hello world"], ["second page"]])
    out_dir = tmp_path / "tpages"
    m = pdf_map.render_pages(str(pdf), str(out_dir))
    assert [p["n"] for p in m["pages"]] == [0, 1]
    assert (out_dir / "tpage0.png").exists() and (out_dir / "tpage1.png").exists()
    assert abs(m["page_w"] - 595) < 1 and abs(m["page_h"] - 842) < 1
    saved = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert saved["version"] == m["version"]


def test_fresh_manifest_skips_rerender(tmp_path):
    pdf = tmp_path / "out.pdf"
    _make_pdf(pdf, [["hi"]])
    out_dir = tmp_path / "tpages"
    m1 = pdf_map.render_pages(str(pdf), str(out_dir))
    mtime = os.path.getmtime(out_dir / "tpage0.png")
    m2 = pdf_map.render_pages(str(pdf), str(out_dir))
    assert m2["version"] == m1["version"]
    assert os.path.getmtime(out_dir / "tpage0.png") == mtime  # untouched


def test_pdf_change_rerenders_and_clears_stale_pages(tmp_path):
    pdf = tmp_path / "out.pdf"
    _make_pdf(pdf, [["v1 page one"], ["v1 page two"]])
    out_dir = tmp_path / "tpages"
    pdf_map.render_pages(str(pdf), str(out_dir))
    _make_pdf(pdf, [["v2 single page"]])  # rewrite: 1 page now
    m = pdf_map.render_pages(str(pdf), str(out_dir))
    assert len(m["pages"]) == 1
    assert not (out_dir / "tpage1.png").exists()  # stale page removed
```

- [ ] **Step 2: Verify failure** (`-k pdf_map`) → import error.

- [ ] **Step 3: Implement** `backend/pipeline/pdf_map.py` (파일 헤더 + render_pages; Task 3 함수들은 아직 없음):

```python
"""Rendered-translated-PDF projections for the viewer.

The DS-converted PDF (see ds_convert.py) IS the editor's layout. This module
projects it for the frontend: page PNGs (the visual layer) and text-position
maps (segment/term bboxes for the interaction overlays), mirroring how the
source pane works (page images + fraction-based hotspots).
"""
from __future__ import annotations

import json
import os
import re

import fitz  # PyMuPDF

import onlyoffice


def render_pages(pdf_path: str, out_dir: str, dpi: int = 150) -> dict:
    """Render each page to out_dir/tpage{N}.png + manifest.json. Fresh manifest
    (same pdf version) returns without touching the PNGs."""
    version = onlyoffice.document_key(pdf_path)  # path+mtime+size hash works for any file
    manifest_path = os.path.join(out_dir, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                m = json.load(fh)
            if m.get("version") == version:
                return m
        except (OSError, ValueError):
            pass
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        if old.startswith("tpage") and old.endswith(".png"):
            os.remove(os.path.join(out_dir, old))
    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    pages = []
    for i in range(doc.page_count):
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        name = f"tpage{i}.png"
        pix.save(os.path.join(out_dir, name))
        pages.append({"n": i, "name": name})
    rect = doc[0].rect if doc.page_count else fitz.Rect(0, 0, 0, 0)
    manifest = {
        "version": version,
        "page_w": round(rect.width, 2),
        "page_h": round(rect.height, 2),
        "pages": pages,
    }
    doc.close()
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    os.replace(tmp, manifest_path)
    return manifest
```

- [ ] **Step 4: Run tests** → 3 PASS.
- [ ] **Step 5: Commit:** `feat(viewer): render DS-converted PDF pages to PNGs with a version-keyed manifest`

---

### Task 3: `pdf_map.py` — 세그먼트/용어 bbox 매핑 (TDD)

**Files:**
- Modify: `backend/pipeline/pdf_map.py`
- Test: `backend/tests/test_pdf_map.py` (추가)

**Interfaces:**
- Produces (Task 4가 소비):
  - `map_segments(pdf_path: str, segments: list[dict]) -> dict[str, dict | None]` — segments는 `[{"id": str, "text": str}]` 문서 순서; 반환은 `{seg_id: {"page","x","y","w","h"} | None}` (페이지 분수).
  - `find_terms(pdf_path: str, terms: list[str]) -> dict[str, list[dict]]` — term당 최대 200 히트, 같은 분수 bbox.
  - 내부 순수 함수 `align_words(words, segments) -> dict[str, tuple[int, fitz.Rect] | None]` — words는 `[(page:int, rect:fitz.Rect, token:str)]` 정규화 완료 목록.

- [ ] **Step 1: Write the failing tests** (`test_pdf_map.py`에 추가):

```python
from pipeline.pdf_map import _norm_tokens, align_words, find_terms, map_segments


def _w(page, x0, y0, x1, y1, tok):
    return (page, fitz.Rect(x0, y0, x1, y1), tok)


def test_norm_tokens_collapses_leaders_case_space():
    assert _norm_tokens("Scope of  Application ......... 8") == ["scope", "of", "application", "8"]
    assert _norm_tokens("통과 (공 용)") == ["통과", "(공", "용)"]
    assert _norm_tokens("···") == []


def test_align_exact_in_order():
    words = [_w(0, 0, 0, 10, 10, "alpha"), _w(0, 0, 20, 10, 30, "beta"),
             _w(0, 0, 40, 10, 50, "gamma")]
    got = align_words(words, [{"id": "s1", "text": "Alpha beta"}, {"id": "s2", "text": "gamma"}])
    page, rect = got["s1"]
    assert page == 0 and rect.y1 == 30  # union of alpha+beta
    assert got["s2"][1].y0 == 40


def test_align_skips_noise_between_segments():
    words = [_w(0, 0, 0, 10, 10, "title"),
             _w(0, 0, 90, 10, 95, "kfs"), _w(0, 0, 90, 10, 95, "0011"),  # running band noise
             _w(1, 0, 0, 10, 10, "body")]
    got = align_words(words, [{"id": "a", "text": "Title"}, {"id": "b", "text": "Body"}])
    assert got["a"][0] == 0 and got["b"][0] == 1


def test_align_miss_returns_none_and_does_not_advance():
    words = [_w(0, 0, 0, 10, 10, "one"), _w(0, 0, 20, 10, 30, "two")]
    got = align_words(words, [{"id": "x", "text": "absent text"}, {"id": "y", "text": "one two"}])
    assert got["x"] is None
    assert got["y"] is not None  # cursor was not consumed by the miss


def test_align_duplicate_segments_take_successive_occurrences():
    words = [_w(0, 0, 0, 10, 10, "go"), _w(0, 0, 20, 10, 30, "stop"),
             _w(0, 0, 40, 10, 50, "go")]
    got = align_words(words, [{"id": "a", "text": "go"}, {"id": "b", "text": "stop"},
                              {"id": "c", "text": "go"}])
    assert got["a"][1].y0 == 0 and got["c"][1].y0 == 40


def test_align_long_segment_matches_head_and_extends():
    toks = [f"w{i}" for i in range(20)]
    words = [_w(0, 0, i * 10, 10, i * 10 + 5, t) for i, t in enumerate(toks)]
    got = align_words(words, [{"id": "long", "text": " ".join(toks)}])
    page, rect = got["long"]
    assert rect.y1 == 195  # extended through ALL 20 words, not just the head


def test_map_segments_fractions_on_real_pdf(tmp_path):
    pdf = tmp_path / "t.pdf"
    _make_pdf(pdf, [["Rated load test", "Second sentence here"], ["Third page-two line"]])
    got = map_segments(str(pdf), [
        {"id": "s1", "text": "Rated load test"},
        {"id": "s2", "text": "Second sentence here"},
        {"id": "s3", "text": "Third page-two line"},
        {"id": "s4", "text": "never rendered"},
    ])
    b1, b3 = got["s1"], got["s3"]
    assert b1["page"] == 0 and b3["page"] == 1
    for b in (b1, b3):
        for k in ("x", "y", "w", "h"):
            assert 0.0 <= b[k] <= 1.0
    assert got["s2"]["y"] > b1["y"]  # below the first line
    assert got["s4"] is None


def test_find_terms_fractions(tmp_path):
    pdf = tmp_path / "t.pdf"
    _make_pdf(pdf, [["Stabilizer goes here", "another Stabilizer"], ["no hits"]])
    got = find_terms(str(pdf), ["Stabilizer", "missing"])
    assert len(got["Stabilizer"]) == 2
    assert all(h["page"] == 0 for h in got["Stabilizer"])
    assert got["missing"] == []
```

- [ ] **Step 2: Verify failure** → ImportError들.

- [ ] **Step 3: Implement** (`pdf_map.py`에 추가):

```python
# A TOC dot-leader run or decoration; drop before tokenizing (the model side
# collapses leaders too — see translator._collapse_leaders).
_LEADER_RE = re.compile(r"[.·…]{2,}")

# How many tokens must anchor a segment before greedy extension takes over.
_MATCH_HEAD = 8
# How many words ahead of the cursor we search for a segment's anchor (skips
# running-band text / page numbers sitting between body segments).
_SKIP_WINDOW = 400


def _norm_tokens(text: str) -> list[str]:
    return [t for t in _LEADER_RE.sub(" ", text).casefold().split() if t]


def align_words(words, segments):
    """Greedy in-order alignment of document-order segments onto the PDF's word
    stream. words: [(page, fitz.Rect, normalized_token)]. Returns
    {seg_id: (page, union_rect_on_first_page) | None}. A miss does NOT advance
    the cursor, so one unmatched segment can't derail the rest."""
    out = {}
    cur = 0
    n = len(words)
    for seg in segments:
        toks = _norm_tokens(seg["text"])
        if not toks:
            out[seg["id"]] = None
            continue
        head = toks[:_MATCH_HEAD]
        found = -1
        for j in range(cur, min(cur + _SKIP_WINDOW, n - len(head) + 1)):
            if all(words[j + k][2] == head[k] for k in range(len(head))):
                found = j
                break
        if found < 0:
            out[seg["id"]] = None
            continue
        k = found + len(head)
        for t in toks[_MATCH_HEAD:]:
            if k < n and words[k][2] == t:
                k += 1
            else:
                break
        first_page = words[found][0]
        rect = fitz.Rect(words[found][1])
        for w in words[found + 1:k]:
            if w[0] == first_page:
                rect |= w[1]
        out[seg["id"]] = (first_page, rect)
        cur = k
    return out


def _pdf_words(doc) -> list:
    words = []
    for pi in range(doc.page_count):
        for x0, y0, x1, y1, word, *_ in doc[pi].get_text("words"):
            toks = _norm_tokens(word)
            if not toks:
                continue  # pure leader-dot "words" are skippable noise
            words.append((pi, fitz.Rect(x0, y0, x1, y1), toks[0]))
    return words


def _fraction(page_rect, page: int, rect) -> dict:
    return {
        "page": page,
        "x": round(rect.x0 / page_rect.width, 4),
        "y": round(rect.y0 / page_rect.height, 4),
        "w": round(max(rect.width, 1.0) / page_rect.width, 4),
        "h": round(max(rect.height, 1.0) / page_rect.height, 4),
    }


def map_segments(pdf_path: str, segments: list) -> dict:
    doc = fitz.open(pdf_path)
    try:
        aligned = align_words(_pdf_words(doc), segments)
        out = {}
        for seg_id, hit in aligned.items():
            if hit is None:
                out[seg_id] = None
            else:
                page, rect = hit
                out[seg_id] = _fraction(doc[page].rect, page, rect)
        return out
    finally:
        doc.close()


def find_terms(pdf_path: str, terms: list) -> dict:
    """search_for-based term hit boxes (glossary/proper-noun highlights)."""
    doc = fitz.open(pdf_path)
    try:
        out = {t: [] for t in terms}
        for pi in range(doc.page_count):
            page = doc[pi]
            for t in terms:
                if not t.strip() or len(out[t]) >= 200:
                    continue
                for rect in page.search_for(t)[: 200 - len(out[t])]:
                    out[t].append(_fraction(page.rect, pi, rect))
        return out
    finally:
        doc.close()
```

주의: `_pdf_words`는 PDF 단어당 첫 정규화 토큰만 쓴다 — PyMuPDF `words`는 공백 분리라 세그먼트 쪽 `split()`과 대칭이므로 토큰이 1개를 넘는 경우는 리더-점 제거로 빈 게 되는 경우뿐이다. `align_words`의 head 매칭은 `min()` 상계 때문에 문서 끝을 넘지 않는다.

- [ ] **Step 4: Run tests:** `-k pdf_map` 전부 PASS (Task 2 것 포함).
- [ ] **Step 5: Commit:** `feat(viewer): order-based segment/term bbox mapping on the DS-rendered PDF`

---

### Task 4: `main.py` 라우트 4종 (TDD)

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_tpages_routes.py`

**Interfaces:**
- Consumes: `ds_convert.ensure_pdf`, `pdf_map.render_pages/map_segments/find_terms`, 기존 `_ensure_docx`(에디터-config 라우트가 쓰는 지연 렌더 헬퍼 — main.py에서 찾아 동일하게 호출), 기존 `/segments` 핸들러의 세그먼트 구성 로직(같은 id 체계 재사용 — main.py의 해당 핸들러를 읽고 같은 함수를 호출).
- Produces (Task 5가 소비):
  - `GET /api/documents/{id}/tpages?lang=` → `{"version", "page_w", "page_h", "pages": [{"n", "url"}]}`; DS 실패 시 503 `{"detail": ...}`.
  - `GET /api/documents/{id}/tpages/{n}.png?lang=&v=` → PNG.
  - `GET /api/documents/{id}/tsegments?lang=` → `{"version", "segments": [{"id", "bbox": {...}|null}]}`.
  - `POST /api/documents/{id}/tfind?lang=` body `{"terms": [...]}` → `{"hits": {term: [bbox...]}}` (terms ≤ 50).

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_tpages_routes.py`) — 기존 라우트 테스트들의 TestClient/스토리지 픽스처 패턴을 읽고 그대로 따르되, 핵심은 아래 구조(픽스처 세부는 이웃 테스트 스타일에 맞춰 완성할 것):

```python
"""tpages/tsegments/tfind routes (DS conversion monkeypatched)."""
import json
import os

import fitz
import pytest
from fastapi.testclient import TestClient

import main
from pipeline import ds_convert


@pytest.fixture()
def doc(tmp_path, monkeypatch):
    """A storage dir with one doc whose 'conversion' writes a synthetic PDF."""
    monkeypatch.setattr(main, "STORAGE_DIR", str(tmp_path))  # main.py의 실제 상수명 확인해 사용
    doc_dir = tmp_path / "d1"
    doc_dir.mkdir()

    def fake_ensure_docx(doc_id, lang):
        p = doc_dir / f"output.{lang}.docx"
        p.write_bytes(b"docx")
        return str(p)

    def fake_ensure_pdf(storage, doc_id, lang):
        out = ds_convert.pdf_path(storage, doc_id, lang)
        pdf = fitz.open()
        page = pdf.new_page(width=595, height=842)
        page.insert_text((72, 72), "Hello translated world", fontsize=12)
        pdf.save(out)
        pdf.close()
        return out

    # main.py의 실제 _ensure_docx 심볼명을 확인해 몽키패치
    monkeypatch.setattr(main, "_ensure_docx", fake_ensure_docx)
    monkeypatch.setattr(main.ds_convert, "ensure_pdf", fake_ensure_pdf)
    # /tsegments가 쓰는 세그먼트 소스도 몽키패치: id/text 2건
    monkeypatch.setattr(main, "_tpage_segments", lambda doc_id, lang: [
        {"id": "seg1", "text": "Hello translated world"},
        {"id": "seg2", "text": "not on the page"},
    ])
    return TestClient(main.app)


def test_tpages_manifest(doc):
    r = doc.get("/api/documents/d1/tpages?lang=en")
    assert r.status_code == 200
    m = r.json()
    assert m["pages"][0]["url"].startswith("/api/documents/d1/tpages/0.png")
    assert m["page_w"] > 0


def test_tpage_png_served(doc):
    doc.get("/api/documents/d1/tpages?lang=en")
    r = doc.get("/api/documents/d1/tpages/0.png?lang=en")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_tsegments_join_ids(doc):
    r = doc.get("/api/documents/d1/tsegments?lang=en")
    assert r.status_code == 200
    segs = {s["id"]: s["bbox"] for s in r.json()["segments"]}
    assert segs["seg1"] is not None and 0 <= segs["seg1"]["x"] <= 1
    assert segs["seg2"] is None


def test_tfind(doc):
    doc.get("/api/documents/d1/tpages?lang=en")
    r = doc.post("/api/documents/d1/tfind?lang=en", json={"terms": ["translated"]})
    assert r.status_code == 200
    assert len(r.json()["hits"]["translated"]) == 1


def test_ds_failure_is_503(doc, monkeypatch):
    def boom(storage, doc_id, lang):
        raise ds_convert.DsConvertError(-3)
    monkeypatch.setattr(main.ds_convert, "ensure_pdf", boom)
    r = doc.get("/api/documents/d1/tpages?lang=en")
    assert r.status_code == 503
```

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement** — `main.py`에 (기존 `/preview`·`/docx` 라우트 근처, 같은 인증 수준 = capability doc_id):

```python
# --- translated-page (editor-parity) views ---------------------------------
# The DS ConvertService renders output.{lang}.docx with the SAME engine as the
# embedded editor, so these page images match the editor pixel-for-pixel; the
# interaction layer (segment hotspots / term highlights) mirrors the source
# pane's fraction-bbox overlays.

def _tpage_segments(doc_id: str, lang: str) -> list[dict]:
    """[{id, text}] in document order, using the SAME ids as GET /segments."""
    # 구현: /segments 핸들러가 쓰는 함수를 그대로 호출해 (id, 번역 텍스트)만 추림.
    # main.py의 해당 핸들러를 읽고 정확한 심볼로 채울 것.
    ...


@app.get("/api/documents/{doc_id}/tpages")
def tpages(doc_id: str, lang: str = "en"):
    _ensure_docx(doc_id, lang)  # 실제 헬퍼 시그니처에 맞출 것
    try:
        pdf = ds_convert.ensure_pdf(STORAGE_DIR, doc_id, lang)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="document not found")
    except (ds_convert.DsConvertError, httpx.HTTPError) as e:
        raise HTTPException(status_code=503, detail=f"문서 변환 실패: {e}")
    out_dir = os.path.join(STORAGE_DIR, doc_id, f"tpages.{lang}")
    m = pdf_map.render_pages(pdf, out_dir)
    return {
        "version": m["version"],
        "page_w": m["page_w"],
        "page_h": m["page_h"],
        "pages": [
            {"n": p["n"],
             "url": f"/api/documents/{doc_id}/tpages/{p['n']}.png?lang={lang}&v={m['version']}"}
            for p in m["pages"]
        ],
    }


@app.get("/api/documents/{doc_id}/tpages/{n}.png")
def tpage_png(doc_id: str, n: int, lang: str = "en", v: str = ""):
    path = os.path.join(STORAGE_DIR, doc_id, f"tpages.{lang}", f"tpage{n}.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="page not rendered")
    return FileResponse(path, media_type="image/png")


@app.get("/api/documents/{doc_id}/tsegments")
def tsegments(doc_id: str, lang: str = "en"):
    _ensure_docx(doc_id, lang)
    try:
        pdf = ds_convert.ensure_pdf(STORAGE_DIR, doc_id, lang)
    except (ds_convert.DsConvertError, httpx.HTTPError) as e:
        raise HTTPException(status_code=503, detail=f"문서 변환 실패: {e}")
    version = onlyoffice.document_key(pdf)
    cache = os.path.join(STORAGE_DIR, doc_id, f"tpages.{lang}", "tsegments.json")
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("version") == version:
                return data
        except (OSError, ValueError):
            pass
    segs = _tpage_segments(doc_id, lang)
    boxes = pdf_map.map_segments(pdf, segs)
    data = {"version": version,
            "segments": [{"id": s["id"], "bbox": boxes.get(s["id"])} for s in segs]}
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return data


class TfindBody(BaseModel):
    terms: list[str]


@app.post("/api/documents/{doc_id}/tfind")
def tfind(doc_id: str, body: TfindBody, lang: str = "en"):
    try:
        pdf = ds_convert.ensure_pdf(STORAGE_DIR, doc_id, lang)
    except (ds_convert.DsConvertError, httpx.HTTPError) as e:
        raise HTTPException(status_code=503, detail=f"문서 변환 실패: {e}")
    terms = [t for t in body.terms if t.strip()][:50]
    return {"hits": pdf_map.find_terms(pdf, terms)}
```

`STORAGE_DIR`/`_ensure_docx`/BaseModel import 등 심볼명은 main.py 실물에 맞출 것(이 코드는 의도 사양). `/segments` 핸들러가 세그먼트를 만들 때 쓰는 함수(예: `process.build_segments` 계열)를 `_tpage_segments`에서 재사용해 **id 체계가 완전히 일치**해야 한다 — 재사용 없이 재구현 금지.

- [ ] **Step 4: Run tests** → 5 PASS; 인접 스위트 `-k "route or main or preview"`도 그린.
- [ ] **Step 5: Commit:** `feat(viewer): tpages/tsegments/tfind routes serving editor-parity page renders`

---

### Task 5: 프런트 `api.ts` + `TranslatedPages.tsx`

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/TranslatedPages.tsx`

**Interfaces:**
- Consumes: Task 4 라우트.
- Produces (Task 6이 소비): 컴포넌트 props —

```ts
export type TBbox = { page: number; x: number; y: number; w: number; h: number };
export type THighlight = { color: "amber" | "indigo"; boxes: TBbox[] };
type Props = {
  docId: string;
  lang: string;
  reloadKey: number;                 // bump -> refetch manifest (재변환 감지)
  segments?: { id: string; bbox: TBbox | null }[]; // 재번역 모드 hotspot
  activeSeg?: string | null;
  onSelectSeg?: (id: string) => void;
  highlights?: THighlight[];         // 용어 모드 오버레이
  scrollTo?: TBbox | null;           // 지정 시 해당 위치로 스크롤
};
```

- [ ] **Step 1: `api.ts`에 추가** (기존 `previewUrl`/`authedFetch` 패턴을 그대로 따름):

```ts
export async function tpages(id: string, lang: string) {
  const r = await authedFetch(`${BACKEND}/api/documents/${id}/tpages?lang=${lang}`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail ?? `tpages ${r.status}`);
  return r.json() as Promise<{
    version: string; page_w: number; page_h: number;
    pages: { n: number; url: string }[];
  }>;
}

export async function tsegments(id: string, lang: string) {
  const r = await authedFetch(`${BACKEND}/api/documents/${id}/tsegments?lang=${lang}`);
  if (!r.ok) throw new Error(`tsegments ${r.status}`);
  return r.json() as Promise<{ segments: { id: string; bbox: TBbox | null }[] }>;
}

export async function tfind(id: string, lang: string, terms: string[]) {
  const r = await authedFetch(`${BACKEND}/api/documents/${id}/tfind?lang=${lang}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ terms }),
  });
  if (!r.ok) throw new Error(`tfind ${r.status}`);
  return r.json() as Promise<{ hits: Record<string, TBbox[]> }>;
}
```

(정확한 BACKEND/authedFetch 심볼·URL 조립 방식은 api.ts 실물에 맞출 것 — 페이지 이미지 `url`은 백엔드 상대경로이므로 `previewUrl`이 쓰는 것과 같은 base로 절대화.)

- [ ] **Step 2: `TranslatedPages.tsx` 구현.** 구조 (원본 패널의 페이지 칼럼과 같은 시각 언어 — `c/[id]/page.tsx`의 소스 페이지 래퍼(aspect-ratio + `<img>`)와 hotspot 버튼(퍼센트 배치) 클래스를 읽고 미러링):

```tsx
"use client";
import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";
import type { TBbox, THighlight } from "@/lib/api";

export default function TranslatedPages({ docId, lang, reloadKey, segments, activeSeg, onSelectSeg, highlights, scrollTo }: Props) {
  const [manifest, setManifest] = useState<Awaited<ReturnType<typeof api.tpages>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(null);
    api.tpages(docId, lang)
      .then((m) => { if (alive) setManifest(m); })   // 이전 페이지 유지: 성공 시에만 교체
      .catch((e) => { if (alive) setError(String(e?.message ?? e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [docId, lang, reloadKey]);

  // scrollTo/activeSeg 변화 시 해당 bbox 요소로 scrollIntoView({block:"center"})
  // (id 부여: `tp-${page}` 페이지 래퍼, activeSeg hotspot에 ref)

  if (error) return (
    <div className="p-6 text-sm">
      <p>번역본 페이지를 준비하지 못했습니다: {error}</p>
      <button className="btn-secondary mt-2" onClick={() => api.tpages(docId, lang).then(setManifest).catch((e) => setError(String(e)))}>다시 시도</button>
    </div>
  );

  return (
    <div ref={rootRef} className="relative h-full overflow-auto">
      {loading && (
        <div className="absolute inset-0 z-10 grid place-items-center bg-white/60 text-sm">
          편집기 레이아웃으로 페이지 준비 중…
        </div>
      )}
      {manifest?.pages.map((p) => (
        <div key={p.n} id={`tp-${p.n}`} className="relative mx-auto my-3 shadow"
             style={{ aspectRatio: `${manifest.page_w} / ${manifest.page_h}`, maxWidth: "100%" }}>
          <img src={api.absUrl(p.url)} alt={`page ${p.n + 1}`} className="block w-full" />
          {segments?.filter((s) => s.bbox && s.bbox.page === p.n).map((s) => (
            <button key={s.id}
              onClick={() => onSelectSeg?.(s.id)}
              className={`absolute rounded-sm ${activeSeg === s.id ? "outline outline-2 outline-[var(--primary)] bg-[var(--primary-soft)]/30" : "hover:bg-[var(--primary-soft)]/20"}`}
              style={{ left: `${s.bbox!.x * 100}%`, top: `${s.bbox!.y * 100}%`, width: `${s.bbox!.w * 100}%`, height: `${s.bbox!.h * 100}%` }} />
          ))}
          {highlights?.flatMap((h, hi) => h.boxes.filter((b) => b.page === p.n).map((b, bi) => (
            <span key={`${hi}-${bi}`} aria-hidden
              className={`absolute rounded-sm pointer-events-none ${h.color === "indigo" ? "bg-indigo-500/25 ring-1 ring-indigo-500" : "bg-amber-400/30"}`}
              style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` }} />
          )))}
        </div>
      ))}
    </div>
  );
}
```

(클래스 토큰은 globals.css의 기존 토큰(`--primary`, `--primary-soft`)과 원본 패널 hotspot 스타일을 읽고 통일. `api.absUrl`이 없으면 previewUrl과 같은 방식으로 base를 조립하는 헬퍼를 api.ts에 추가.)

- [ ] **Step 3: Verify:** `cd frontend && npm run build` 통과 (미사용 경고 무시 가능 — Task 6에서 사용됨).
- [ ] **Step 4: Commit:** `feat(viewer): TranslatedPages component + tpages/tsegments/tfind API bindings`

---

### Task 6: `c/[id]/page.tsx` 배선 교체

**Files:**
- Modify: `frontend/src/app/c/[id]/page.tsx`
- Delete (참조 0이면): `frontend/src/components/TranslatedPreview.tsx`

**Interfaces:**
- Consumes: Task 5 컴포넌트/API, 기존 상태(`activeSeg`, `segments`, `activePnTerm`, `glossaryReport`, `previewReloadKey`, `manageKey`).

- [ ] **Step 1: 재번역 모드 교체** — 번역본(중앙) 패널의 `SegmentPreview` 사용처를 `TranslatedPages`로:
  - `tsegments`를 매니페스트와 함께 로드(`reloadKey`=`previewReloadKey` 변화 시 재조회)해 `segments` prop으로.
  - 클릭 → 기존 `setActiveSeg`; `activeSeg` 변화 시 해당 bbox로 스크롤(`scrollTo`).
  - **원본(왼쪽) 패널의 SegmentPreview(HWP source-preview)는 그대로 둔다.**
- [ ] **Step 2: 용어 모드 교체** — `TranslatedPreview` 사용처를 `TranslatedPages`로:
  - 앰버 하이라이트: glossaryReport의 용어들(적용된 target 용어 문자열)을 `tfind`로 조회해 `highlights[{color:"amber"}]`.
  - 활성 고유명사: `activePnTerm`의 번역어를 `tfind`로 조회해 `highlights[{color:"indigo"}]` + 첫 히트로 `scrollTo`.
  - GlossaryManagePanel의 `onJump`(기존 iframe hash 점프)는 해당 용어의 첫 tfind 히트로 스크롤하는 것으로 대체.
  - `manageKey` 변화 시 재조회.
- [ ] **Step 3: 재번역 적용 후 갱신 확인** — 적용 → `/render` → `previewReloadKey` bump가 이미 있으니 매니페스트 재조회로 자동 재변환됨(코드상 흐름 확인만; 추가 배선 불필요해야 정상).
- [ ] **Step 4: `TranslatedPreview.tsx` 참조 검색** (`grep -r TranslatedPreview frontend/src`) — 참조 0이면 파일 삭제, 남으면 유지하고 보고.
- [ ] **Step 5: Verify:** `npm run build` 통과.
- [ ] **Step 6: Commit:** `feat(viewer): translated pane renders editor-parity pages in retranslate/manage modes`

---

### Task 7: dev-local 통합 테스트 + 전체 스위트

**Files:**
- Create: `backend/tests/test_tpages_integration.py`
- Create: `backend/storage/_parity-verify-ladder` (기존 `backend/storage/XRwYS6dUQHKIklcKhuexq3Rm` 복사 — 원본 무수정)

**Pattern:** `test_band_integration_pharma.py`와 같은 skippable 스캐폴딩(스토리지 픽스처 없거나 DS 미기동 시 skip).

- [ ] **Step 1: Test:**

```python
"""Dev-local: real DS conversion on a cached ladder doc (skips without DS/fixture)."""
import os

import httpx
import pytest

import onlyoffice
from pipeline import ds_convert, pdf_map

STORAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage")
DOC = "_parity-verify-ladder"


def _ds_up() -> bool:
    try:
        return httpx.get(onlyoffice.DS_INTERNAL_URL.rstrip("/") + "/healthcheck",
                         timeout=3).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(STORAGE, DOC, "output.en.docx")) or not _ds_up(),
    reason="dev-local fixture or DS not available",
)


def test_real_conversion_and_mapping():
    pdf = ds_convert.ensure_pdf(STORAGE, DOC, "en")
    m = pdf_map.render_pages(pdf, os.path.join(STORAGE, DOC, "tpages.en"))
    assert len(m["pages"]) >= 10
    import json
    with open(os.path.join(STORAGE, DOC, "translated.en.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    from models import SemanticDocument, iter_text_fields
    doc = SemanticDocument.model_validate(data)
    segs = [{"id": f"s{i}", "text": g()} for i, (g, _) in enumerate(iter_text_fields(doc))
            if g().strip()][:120]
    boxes = pdf_map.map_segments(pdf, segs)
    matched = sum(1 for b in boxes.values() if b)
    assert matched / len(segs) >= 0.7, f"match rate {matched}/{len(segs)}"
```

주의: 이 테스트의 ConvertService url은 `ONLYOFFICE_BACKEND_URL`이 가리키는 백엔드에서 `_parity-verify-ladder`를 서빙할 수 있어야 성립하므로, **fixture 복사가 곧 이를 보장**한다(백엔드 스토리지에 실린 실제 디렉토리). 로컬 백엔드가 :8012인 세션 환경에서는 `ONLYOFFICE_BACKEND_URL=http://host.docker.internal:8012`로 실행(미설정 기본 :8000이면 skip이 아니라 실패할 수 있으니, 변환 요청 실패 시 `pytest.skip`으로 강등 처리).

- [ ] **Step 2: Run** (DS 기동 상태) → PASS; 매칭률 출력 확인.
- [ ] **Step 3: 전체 스위트:** `./.venv/Scripts/python.exe -m pytest -q` — 기존 15개 protected-file 실패만.
- [ ] **Step 4: Commit:** `test(viewer): dev-local DS conversion + bbox mapping integration check`

---

### Task 8 (컨트롤러 직접): 브라우저 검증

- [ ] 백엔드 재시작(새 라우트 반영, :8012 + ONLYOFFICE_BACKEND_URL 유지), 프런트 재빌드+기동.
- [ ] ladder 문서: 편집 모드 페이지 N 스크린샷 vs 용어 모드 같은 페이지 스크린샷 — 커버 배경/러닝 밴드/쪽번호/표가 동일한지 시각 대조.
- [ ] 재번역 모드: hotspot 클릭 → 세그먼트 선택 → 재번역 적용 → 페이지 자동 갱신(재변환) 확인.
- [ ] 용어 모드: 고유명사 클릭 → 양쪽 패널 하이라이트 + 스크롤 확인.

## Self-Review

- 스펙 §1→T1, §2→T2+T3, §3→T4, §4→T5+T6, 검증→T7+T8. ✓
- 타입 일관성: bbox `{page,x,y,w,h}` 분수 규약이 T3 `_fraction` ↔ T4 응답 ↔ T5 `TBbox`로 일치; `ensure_pdf(storage_dir, doc_id, lang)` 시그니처 T1↔T4↔T7 일치. ✓
- placeholder: T4 `_tpage_segments` 내부와 프런트 심볼 몇 곳은 "실물 읽고 미러링" 지시로 의도적 위임(재사용 강제) — 그 외 전 스텝 실제 코드. ✓
