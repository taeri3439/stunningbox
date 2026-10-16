# Template Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. TDD.

**Goal:** 고정 양식(디자인 리포트, EN→KO류 축소 방향)에서 페이지 1:1 대응 + 의도적 여백 + 캡션 부착 + 폰트 클래스 일치로 시각 유사도 ~99%.

**Architecture:** 방향 게이트(`target_lang ∈ {ko,ja,zh}` 또는 `doc.page_parity` 강제)가 켜졌을 때만 docx 렌더러가 소스 페이지 경계에 `page_break_before`를 놓고(빈 문단 금지 — 2026-06 bloat 재발 방지), reconciler가 항상 측정해 두는 `page_space_before`(희소 페이지 수직 리듬)를 적용한다. 캡션 keep-with-next와 폰트 eastAsia 스왑·TOC 들여쓰기는 무조건 적용. ko→en 기존 문서는 완전 무변경.

**Tech Stack:** python-docx(+기존 _ooxml 헬퍼), PyMuPDF(테스트 픽스처), pytest.

## Global Constraints

- 절대 `git add -A` 금지. 보호 파일 무변경: `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, `backend/_*.py`, `test-pdfs/`, 루트 이미지. `backend/storage/**` 커밋 금지.
- `iter_segments`/`build_segment_index_map` 무변경. HTML 렌더러 무변경. 기존 `space_before`(커버 리듬) 의미 무변경.
- ko→en 문서의 렌더 출력 무변경 (패리티 자동 OFF) — 회귀 테스트로 보장.
- 백엔드 테스트: `backend/`에서 `./.venv/Scripts/python.exe -m pytest …`; 마지막에 전체 스위트(기존 15개 test_auth/test_documents_store 실패 제외).
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- 상수(전 태스크 공통): `PARITY_TARGET_LANGS = {"ko","ja","zh"}`, `RHYTHM_MAX_TEXT_BLOCKS = 8`, `RHYTHM_MIN_GAP_PT = 40.0`, 리듬 clamp `[0,300]`pt·스킵 `≤6`pt(커버와 동일), TOC `INDENT_PER_LEVEL_IN = 0.18`(cap 4).

---

### Task 1: 모델 필드 + 페이지 패리티 앵커링 (TDD)

**Files:**
- Modify: `backend/models.py` (SemanticDocument에 `page_parity: Optional[bool] = None`; Heading/Paragraph/ListItem/Caption 4개 텍스트 블록에 `page_space_before: Optional[float] = None` — 기존 `space_before` 필드 옆)
- Modify: `backend/renderers/docx_renderer.py`
- Test: `backend/tests/test_page_parity.py`

**Interfaces:**
- Produces: `_page_parity_on(doc) -> bool` (renderer 내부), 앵커링 렌더 동작. T3·T5가 의존.

- [ ] **Step 1: Failing tests** (`backend/tests/test_page_parity.py`) — 이웃 렌더 테스트(`test_cover_rhythm.py`, `test_table_render_occupancy.py`)의 SemanticDocument 조립·docx 읽기 패턴을 먼저 읽고 그대로 따르되, 케이스는 다음과 같이:

```python
"""Page-parity anchoring: source-page boundaries become page_break_before in docx."""
import docx as docxlib

import models
from renderers.docx_renderer import render_docx


def _doc(blocks, target_lang="ko", page_parity=None):
    d = models.SemanticDocument(source_lang="en", blocks=blocks)
    d.target_lang = target_lang
    d.page_parity = page_parity
    return d


def _para_texts_with_break(path):
    out = []
    doc = docxlib.Document(path)
    for p in doc.paragraphs:
        pf = p.paragraph_format
        out.append((p.text, bool(pf.page_break_before)))
    return out


def test_break_before_first_block_of_each_source_page(tmp_path):
    doc = _doc([
        models.ParagraphBlock(text="p0 a", page=0, source_span_ids=["s1"]),
        models.ParagraphBlock(text="p0 b", page=0, source_span_ids=["s2"]),
        models.ParagraphBlock(text="p1 first", page=1, source_span_ids=["s3"]),
        models.ParagraphBlock(text="p2 first", page=2, source_span_ids=["s4"]),
    ])
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, str(tmp_path))
    marks = dict(_para_texts_with_break(out))
    assert marks["p0 a"] is False and marks["p0 b"] is False
    assert marks["p1 first"] is True and marks["p2 first"] is True


def test_no_empty_break_paragraphs(tmp_path):
    doc = _doc([
        models.ParagraphBlock(text="p0", page=0, source_span_ids=["s1"]),
        models.ParagraphBlock(text="p1", page=1, source_span_ids=["s2"]),
    ])
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, str(tmp_path))
    texts = [t for t, _ in _para_texts_with_break(out)]
    # 과거 bloat의 원인이던 브레이크 전용 빈 문단이 없어야 한다
    assert "" not in [t for t in texts if t is not None] or all(
        t != "" for t, br in _para_texts_with_break(out) if br
    )


def test_zero_block_source_page_gets_no_extra_break(tmp_path):
    # page 1에 블록이 없어도(전량 소실) page 2 첫 블록은 브레이크 1개만 갖는다
    doc = _doc([
        models.ParagraphBlock(text="p0", page=0, source_span_ids=["s1"]),
        models.ParagraphBlock(text="p2", page=2, source_span_ids=["s2"]),
    ])
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, str(tmp_path))
    marks = dict(_para_texts_with_break(out))
    assert marks["p2"] is True  # 하나의 break_before일 뿐 blank page 2장 아님


def test_table_first_page_uses_1pt_spacer(tmp_path):
    doc = _doc([
        models.ParagraphBlock(text="p0", page=0, source_span_ids=["s1"]),
        models.TableBlock(page=1, rows=[[models.TableCell(text="c")]], source_span_ids=["s2"]),
    ])
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, str(tmp_path))
    d = docxlib.Document(out)
    spacers = [p for p in d.paragraphs
               if p.paragraph_format.page_break_before and not p.text.strip()]
    assert len(spacers) == 1
    sp = spacers[0]
    assert sp.runs == [] or all(r.font.size.pt <= 2 for r in sp.runs if r.font.size)
    # 스페이서는 여백을 만들지 않아야 한다
    assert (sp.paragraph_format.space_after or 0) == 0


def test_parity_off_for_expanding_direction(tmp_path):
    doc = _doc([
        models.ParagraphBlock(text="p0", page=0, source_span_ids=["s1"]),
        models.ParagraphBlock(text="p1", page=1, source_span_ids=["s2"]),
    ], target_lang="en")
    doc.source_lang = "ko"
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, str(tmp_path))
    assert not any(br for _, br in _para_texts_with_break(out))


def test_explicit_page_parity_overrides_direction(tmp_path):
    doc = _doc([
        models.ParagraphBlock(text="p0", page=0, source_span_ids=["s1"]),
        models.ParagraphBlock(text="p1", page=1, source_span_ids=["s2"]),
    ], target_lang="en", page_parity=True)
    doc.source_lang = "ko"
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, str(tmp_path))
    marks = dict(_para_texts_with_break(out))
    assert marks["p1"] is True
```

(TableBlock/TableCell의 실제 필드명은 models.py를 읽고 맞출 것. `target_lang`이 생성자 인자인지 사후 대입인지도 models.py 실물에 따름.)

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement.**
  - `models.py`: 필드 추가 (Optional, default None — 스키마 하위호환).
  - `docx_renderer.py`: 블록 루프 머리(기존 "deliberately NO hard page break" 주석 자리, ~line 143-155)에:

```python
    parity = _page_parity_on(doc)
    cover_page0 = doc.cover_background is not None  # 커버 브레이크와 중복 방지용
    prev_page = None
    ...
    for i, block in enumerate(doc.blocks):
        page = getattr(block, "page", None)
        break_before = False
        if parity and page is not None and prev_page is not None and page != prev_page:
            # 커버 직후 첫 본문 페이지는 커버 브레이크가 이미 새 페이지를 보장한다
            break_before = not (cover_page0 and prev_page == 0)
        if page is not None:
            prev_page = page
        ...
```

  - 각 블록 타입 분기에서 첫 생성 문단에 `p.paragraph_format.page_break_before = True` 적용 (`break_before`일 때). TableBlock 분기는 문단이 없으므로 스페이서:

```python
        if break_before and isinstance(block, TableBlock):
            sp = d.add_paragraph()
            sp.paragraph_format.page_break_before = True
            sp.paragraph_format.space_before = Pt(0)
            sp.paragraph_format.space_after = Pt(0)
            r = sp.add_run("")
            r.font.size = Pt(1)
```

  - `_page_parity_on(doc)`:

```python
PARITY_TARGET_LANGS = {"ko", "ja", "zh"}


def _page_parity_on(doc) -> bool:
    """Anchored pagination: ON when translation direction shrinks text (latin→CJK)
    or the model explicitly opts in. Expanding directions (e.g. ko→en) stay OFF —
    see the 2026-06-15 bloat removal (e7b3fbc); breaks make page drift LOCAL
    (each anchor resyncs) instead of cumulative."""
    if doc.page_parity is not None:
        return doc.page_parity
    return (doc.target_lang or "") in PARITY_TARGET_LANGS
```

  - 기존 "NB: deliberately NO..." 주석은 방향 게이트 설명으로 대체(과거 결정 사유를 보존하며 갱신).
  - 주의: FigureBlock·Divider 등 문단 기반 블록이 페이지 첫 블록일 수도 있다 — 각 분기에서 첫 문단에 일괄 적용할 수 있도록 공통 헬퍼(예: `_mark_break(p, break_before)`)로 처리.
- [ ] **Step 4: Run:** `-k page_parity` 전부 PASS + `-k "render or cover"` 그린.
- [ ] **Step 5: Commit:** `feat(fidelity): direction-gated page-parity anchoring at source-page boundaries`

---

### Task 2: 희소 페이지 수직 리듬 측정 (reconciler, TDD)

**Files:**
- Modify: `backend/pipeline/reconciler.py`
- Test: `backend/tests/test_page_rhythm.py`

**Interfaces:**
- Consumes: T1의 `page_space_before` 필드.
- Produces: 희소 비커버 페이지의 텍스트 블록들에 `page_space_before` 채움. T3이 렌더에 적용.

- [ ] **Step 1: Failing tests** — `test_cover_rhythm.py`의 픽스처 패턴(레콘사일 구동, 스팬 y좌표 조작)을 먼저 읽고 미러링. 케이스:
  - 희소 페이지(텍스트 블록 3개, 첫 블록이 페이지 절반 아래서 시작): 첫 블록 `page_space_before` ≈ 측정 gap(±1pt), 이후 블록들도 gap ≥ 6pt면 세팅.
  - 조밀 페이지(텍스트 블록 9개): 아무 블록에도 세팅 안 됨.
  - TableBlock 있는 페이지: 세팅 안 됨.
  - gap 전부 < 40pt인 희소 페이지: 세팅 안 됨(게이트 미통과).
  - 커버 페이지(page 0, cover_background): 기존 `space_before` 로직만 동작, `page_space_before` 미세팅(이중 적용 방지).
- [ ] **Step 2: Verify failure. Step 3: Implement** — `_apply_cover_rhythm` 근처에 `_apply_page_rhythm(local, page_index, margin_top, by_id, has_table)` 추가; 측정 내부는 커버 헬퍼와 동일 방식(스팬 bbox 기반; 해당 헬퍼를 읽고 공통화 가능하면 내부 함수 추출 — 단 커버 경로 동작 무변경이 우선). 게이트 상수는 Global Constraints 값. 호출부는 페이지 로컬 블록 확정 지점(커버 rhythm 호출부 ~line 770 부근의 페이지 루프)에서 비커버 페이지에 대해 호출.
- [ ] **Step 4: Run:** `-k "rhythm"` 전부(커버 포함) PASS. **Step 5: Commit:** `feat(fidelity): measure sparse-page vertical rhythm into page_space_before`

---

### Task 3: 리듬 렌더 적용 + 캡션 keep-with-next (TDD)

**Files:**
- Modify: `backend/renderers/docx_renderer.py`
- Test: `backend/tests/test_page_rhythm_render.py`

**Interfaces:** Consumes T1 `_page_parity_on` + T2 필드.

- [ ] **Step 1: Failing tests:**

```python
"""page_space_before applies only in parity mode; captions keep with next."""
import docx as docxlib

import models
from renderers.docx_renderer import render_docx


def test_page_space_before_applied_in_parity_mode(tmp_path):
    d = models.SemanticDocument(source_lang="en", blocks=[
        models.ParagraphBlock(text="a", page=0, source_span_ids=["s1"]),
        models.ParagraphBlock(text="b", page=1, page_space_before=120.0, source_span_ids=["s2"]),
    ])
    d.target_lang = "ko"
    out = str(tmp_path / "o.docx")
    render_docx(d, out, str(tmp_path))
    doc = docxlib.Document(out)
    p = next(p for p in doc.paragraphs if p.text == "b")
    assert p.paragraph_format.space_before.pt == 120.0


def test_page_space_before_ignored_when_parity_off(tmp_path):
    d = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="a", page=0, source_span_ids=["s1"]),
        models.ParagraphBlock(text="b", page=1, page_space_before=120.0, source_span_ids=["s2"]),
    ])
    d.target_lang = "en"
    out = str(tmp_path / "o.docx")
    render_docx(d, out, str(tmp_path))
    doc = docxlib.Document(out)
    p = next(p for p in doc.paragraphs if p.text == "b")
    assert p.paragraph_format.space_before is None or p.paragraph_format.space_before.pt != 120.0


def test_cover_space_before_still_wins(tmp_path):
    # 블록에 space_before(커버 리듬)와 page_space_before가 둘 다 있으면 space_before 우선
    d = models.SemanticDocument(source_lang="en", blocks=[
        models.ParagraphBlock(text="t", page=0, space_before=50.0, page_space_before=99.0,
                              source_span_ids=["s1"]),
    ])
    d.target_lang = "ko"
    out = str(tmp_path / "o.docx")
    render_docx(d, out, str(tmp_path))
    doc = docxlib.Document(out)
    p = next(p for p in doc.paragraphs if p.text == "t")
    assert p.paragraph_format.space_before.pt == 50.0


def test_caption_keeps_with_next(tmp_path):
    d = models.SemanticDocument(source_lang="ko", blocks=[
        models.CaptionBlock(text="그림 1 제목", page=0, source_span_ids=["s1"]),
        models.FigureBlock(page=0, image_ref="img.png", source_span_ids=["s2"]),
    ])
    d.target_lang = "en"  # 방향 무관하게 적용되는지 확인
    (tmp_path / "img.png").write_bytes(_PNG_1PX)  # 1px png 픽스처 — 이웃 테스트에서 복사
    out = str(tmp_path / "o.docx")
    render_docx(d, out, str(tmp_path))
    doc = docxlib.Document(out)
    cap = next(p for p in doc.paragraphs if "그림 1" in p.text)
    assert cap.paragraph_format.keep_with_next is True
```

(FigureBlock 필드명·1px PNG 픽스처는 이웃 figure 테스트에서 미러링.)

- [ ] **Step 2: Verify failure. Step 3: Implement** — `_apply_style`(space_before 적용부, ~line 322-327)에서: `sb = block.space_before if block.space_before is not None else (block.page_space_before if parity else None)` — parity 플래그는 render_docx에서 계산해 `_apply_style`에 전달(시그니처 확장 또는 모듈 컨텍스트; 기존 호출부 전부 갱신). CaptionBlock 분기에 `p.paragraph_format.keep_with_next = True`(무조건).
- [ ] **Step 4: Run + `-k "render or rhythm or caption"` 그린. Step 5: Commit:** `feat(fidelity): render sparse-page rhythm in parity mode; captions keep with next`

---

### Task 4: 폰트 eastAsia 클래스 매핑 + TOC 들여쓰기 (TDD)

**Files:**
- Modify: `backend/renderers/docx_renderer.py` (`_set_run_font` ~line 268, `_render_toc` ~line 346 + 그 호출부)
- Test: `backend/tests/test_font_class_toc.py`

- [ ] **Step 1: Failing tests:**

```python
"""Latin-only fonts get a class-matched eastAsia substitute; TOC entries indent by depth."""
import docx as docxlib
from docx.oxml.ns import qn

import models
from renderers.docx_renderer import render_docx


def _rfonts(path, needle):
    doc = docxlib.Document(path)
    for p in doc.paragraphs:
        if needle in p.text:
            for r in p.runs:
                rPr = r._element.find(qn("w:rPr"))
                if rPr is None:
                    continue
                rf = rPr.find(qn("w:rFonts"))
                if rf is not None:
                    return {k.split('}')[-1]: v for k, v in rf.attrib.items()}
    return None


def _render_one(tmp_path, block):
    d = models.SemanticDocument(source_lang="en", blocks=[block])
    d.target_lang = "ko"
    out = str(tmp_path / "o.docx")
    render_docx(d, out, str(tmp_path))
    return out


def test_latin_sans_font_swaps_eastasia_to_malgun(tmp_path):
    out = _render_one(tmp_path, models.ParagraphBlock(
        text="시장 개요", page=0, font="FranklinGothic-Book", source_span_ids=["s1"]))
    rf = _rfonts(out, "시장 개요")
    assert rf["eastAsia"] == "Malgun Gothic"
    assert rf["ascii"] == "FranklinGothic-Book"  # 라틴은 소스 이름 유지


def test_latin_serif_font_swaps_eastasia_to_batang(tmp_path):
    out = _render_one(tmp_path, models.ParagraphBlock(
        text="본문", page=0, font="TimesNewRomanPSMT", source_span_ids=["s1"]))
    rf = _rfonts(out, "본문")
    assert rf["eastAsia"] == "Batang"


def test_korean_font_name_unchanged(tmp_path):
    out = _render_one(tmp_path, models.ParagraphBlock(
        text="본문", page=0, font="맑은 고딕", source_span_ids=["s1"]))
    rf = _rfonts(out, "본문")
    assert rf["eastAsia"] == "맑은 고딕" and rf["ascii"] == "맑은 고딕"


def test_toc_entries_indent_by_marker_depth(tmp_path):
    d = models.SemanticDocument(source_lang="en", blocks=[
        models.ListItemBlock(marker="12.", text="ZOETIS ......... 149", page=0, source_span_ids=["a"]),
        models.ListItemBlock(marker="12.2", text="개요 ......... 149", page=0, source_span_ids=["b"]),
        models.ListItemBlock(marker="12.2.1", text="사업 ......... 150", page=0, source_span_ids=["c"]),
    ])
    d.target_lang = "ko"
    out = str(tmp_path / "o.docx")
    render_docx(d, out, str(tmp_path))
    doc = docxlib.Document(out)
    tocs = [p for p in doc.paragraphs if "\t" in p.text or "149" in p.text or "150" in p.text]
    ind = [(p.paragraph_format.left_indent.inches if p.paragraph_format.left_indent else 0.0)
           for p in tocs[:3]]
    assert ind[0] < ind[1] < ind[2]
    assert abs(ind[1] - 0.18) < 0.02 and abs(ind[2] - 0.36) < 0.02
```

(TOC로 인식되려면 리더 점 + 페이지 번호 형태가 `_toc_split` 정규식에 맞아야 한다 — `_TOC_RE`를 읽고 텍스트를 매칭되게 조정. 마커 깊이 계산: `depth = max(0, len([p for p in marker.rstrip('.').split('.') if p]) - 1)`, cap 4.)

- [ ] **Step 2: Verify failure. Step 3: Implement**
  - `_set_run_font(run, name)`: 이름이 ASCII 전용이고 한글/CJK 폰트명이 아니면 eastAsia만 스왑:

```python
_SANS_PAT = re.compile(
    r"gothic|arial|helvetica|franklin|verdana|tahoma|segoe|calibri|noto ?sans|malgun|dotum|돋움|고딕",
    re.IGNORECASE)
_CJK_NAME_PAT = re.compile(r"[가-힣]|batang|바탕|malgun|맑은|gulim|굴림|dotum|돋움|gungsuh|궁서|noto.*(kr|cjk)", re.IGNORECASE)


def _east_asia_for(name: str) -> str:
    """A latin-only source font has no hangul glyphs — Word/DS would silently fall
    back to a default SERIF for CJK. Substitute a class-matched Korean family so a
    sans-serif report stays sans-serif in translation."""
    if _CJK_NAME_PAT.search(name):
        return name
    return "Malgun Gothic" if _SANS_PAT.search(name) else "Batang"
```

  - rFonts 세팅에서 `w:eastAsia`만 `_east_asia_for(name)`, 나머지 3속성은 기존대로 `name`.
  - `_render_toc` 호출부(heading/list-item TOC 분기)에서 marker 깊이로 `p.paragraph_format.left_indent = Inches(min(depth, 4) * 0.18)` — 리더 탭 스톱 위치는 content_width 그대로(우측 정렬 유지).
- [ ] **Step 4: Run + `-k "font or toc"` 그린 (기존 test_toc_multiline 포함). Step 5: Commit:** `feat(fidelity): class-matched CJK font substitution + TOC depth indentation`

---

### Task 5: dev-local 통합 검증 + 회귀 + 전체 스위트

**Files:**
- Create: `backend/tests/test_template_fidelity_integration.py`
- Create: `backend/storage/_tpl-verify-vet` (`backend/storage/L8QPjeG06JIktCo1drj9vjjn` 복사 — 원본 무수정, 커밋 금지)
- 기존 `backend/storage/_parity-verify-ladder` 재사용 (ko→en 회귀)

- [ ] **Step 1: Test** (skippable 스캐폴딩은 `test_tpages_integration.py` 미러):
  - vet(EN→KO): `process.rebuild_source_model` 후 translated.ko.json 모델 로드 → `render_docx` → LibreOffice 우회 없이 **PyMuPDF로 페이지 수를 셀 수 없으므로** docx를 `pipeline/convert._run_soffice`(diagnostics 경로)로 PDF 변환 가능하면 페이지 수 20 == 원본 20 assert; soffice 부재 시 skip. 추가로 docx 문단들의 `page_break_before` 개수 == (블록이 있는 소스 페이지 수 - 1 - 커버스킵) assert (soffice 불필요한 구조 검증).
  - ladder(ko→en) 회귀: 재렌더 후 `page_break_before` 문단 0개 + `page_space_before` 적용 0 (파일 바이트 비교 대신 구조 확인).
- [ ] **Step 2: Run — PASS. Step 3: 전체 스위트** `-q` (기존 15개 제외 전부 그린). **Step 4: Commit** (테스트 파일만): `test(fidelity): vet page-parity + ladder regression integration checks`

---

### Task 6 (컨트롤러 직접): 브라우저 before/after

- [ ] 백엔드 재시작(새 렌더 코드) → vet 문서 `POST /render?lang=ko` 재렌더 → tpages 재변환 → 원본 vs 번역 나란히 스크린샷: 페이지 수 20=20, Disclaimer 상단 여백, TOC 계층 들여쓰기, 산세리프 한글, FIGURE 캡션 부착.
- [ ] ladder 문서 회귀 스팟체크 (쪽 수·모양 무변화).

## Self-Review

- 스펙 A→T1, B→T2+T3, C→T3, D→T4, 검증→T5+T6. ✓
- 타입 일관성: `page_parity`/`page_space_before` 필드명이 T1(모델)↔T2(레콘사일)↔T3(렌더)↔T5(검증)에서 동일; `_page_parity_on` 시그니처 T1↔T3 공유. ✓
- 과거 결정 존중: e7b3fbc의 두 근거(빈 문단 bloat, ko→en 팽창)를 각각 메커니즘(문단 속성)과 게이트(방향)로 해소 — 주석으로 이력 보존. ✓
- placeholder: 이웃 테스트 미러링 지시 외 전 스텝 실코드. ✓
