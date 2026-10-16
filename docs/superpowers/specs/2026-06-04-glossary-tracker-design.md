# 용어 관리 (Glossary Tracking & Management) — 설계

작성일: 2026-06-04 · 브랜치: `glossarytracker-2026.06.04`
(의존: 머지된 [용어집 기반 번역](2026-06-03-glossary-based-translation-design.md) + [용어 일괄변경](2026-06-03-bulk-term-replace-design.md). 본 기능은 후자의 `용어 일괄변경` 모드를 **`용어 관리`로 개편**하고 그 엔진(`bulk_replace`)을 재사용한다.)

## 배경 / 목적

사용자가 업로드한 용어집은 "이 단어들은 이렇게 번역되게 해줘"라는 의도다. 번역 후 뷰어에서
**그 의도가 실제로 반영됐는지 보고, 바로 손볼 수 있게** 한다. 핵심 3가지:

1. **하이라이트** — 용어집 단어를 원문·번역본에서 색으로 표시. 누락은 원문에서 다른 색 → 사용자가 원문에서 미반영을 바로 확인.
2. **번역어 수정** — 리스트에서 특정 용어의 번역어를 고치면 현재 번역본에 즉시 통일.
3. **용어 추가** — 일관 번역이 필요한 단어를 용어집에 새로 등록.

## 사용자 결정 사항 (확정)

- **추적 대상**: 사용자 **업로드 용어집만**(`glossary.json`). 내장 도메인 용어집은 제외.
- **표시**: 용어별 **적용 / 누락**(원어는 원문에 있는데 번역어가 번역에 없음 = 미반영 *추정*) / **문서에 없음**.
- **배치**: 액션바 `용어 일괄변경` → **`용어 관리`**. 우측 패널 = 편집 가능한 용어집 리스트.
- **번역본 표시**: OnlyOffice CE는 편집기 내 프로그램 하이라이트 불가(`createConnector` = Developer Edition 전용). 그래서 **용어 관리 모드에서는 가운데를 하이라이트된 읽기전용 번역본(HTML)** 으로 전환. 편집은 `편집` 모드의 OnlyOffice에서.
- **수정·추가 반영**: 지금 번역본에 즉시 통일. 수정=`옛 번역어→새 번역어` 치환(기존 `bulk_replace`); 추가=용어집 등록 + 선택적 재번역.
- **직접 교체 유지**: 용어집과 무관한 임의 단어 보정(find→replace)을 용어 관리 안에 **보조 섹션**으로 유지(기존 BulkReplacePanel 로직 재사용).

## 핵심 메커니즘 — 적용 audit (추정)

용어집은 `{원어(source): 번역어(target)}`. LLM이 어떤 항목을 썼는지 알려주는 계측은 없으므로,
**번역 후 세그먼트 단위 대조**로 추정한다(재번역 패널이 쓰는 source↔translated 정렬 재사용):

- 각 용어집 항목에 대해, **원어가 등장하는 원문 세그먼트**를 찾고(원어는 source_lang 매칭), 그
  세그먼트의 **번역에 번역어가 있는지**(번역어는 target_lang 매칭) 검사.
- 세그먼트별 결과 = 적용(번역어 있음) / 누락(없음). 항목 상태:
  - `absent`(문서에 없음): 원문 등장 0.
  - `applied`: 등장 전부 적용.
  - `missed`: 등장하나 전부 미적용.
  - `partial`: 일부만 적용.
- **추정 한계 명시**: target이 우연히 등장(거짓 적용)하거나 LLM이 활용형·복수형으로 써서(거짓 누락) 어긋날 수 있음 → UI는 "적용/누락"을 단정이 아니라 **추정**으로 표기(예: 색 + "추정" 툴팁).
- 매칭은 [text_replace.compile_rule](../../../backend/pipeline/text_replace.py)을 재사용 → 일괄변경과 의미 일치(공백 언어=단어경계, CJK=부분일치, 대소문자 무시).

## 비목표 (v1)

- 내장 용어집 적용 추적. 거짓 적용/누락의 정밀 교정(활용형 인식). 누락 "무시(dismiss)" 상태 저장.
- Word(.docx/.doc) **원문** 색 하이라이트(원문 패널 자체가 없음 — 리스트 상태점만). HWP는 아래 참조.
- 번역 후 용어집 항목 **삭제** UI(수정·추가만; 삭제는 후순위).
- 미리보기와 OnlyOffice 직접편집(.docx) 간 완전 동기화(역할 분리로 수용 — 아래 엣지).

## 아키텍처 / 흐름

```
뷰어 c/[id]/page.tsx  mode: 'edit' | 'retranslate' | 'manage'   (← 'replace'를 'manage'로 대체)
 [용어 관리] 토글 → mode='manage'
   · 가운데: OnlyOffice 편집기 → 하이라이트된 번역본 HTML 미리보기로 전환
   · 왼쪽 원문: 용어집 원어 색칠(🟢적용 / 🔴누락)  (PDF=단어 박스 오버레이, HWP=<mark>, Word=없음)
   · 오른쪽: GlossaryManagePanel
       - 요약(적용/누락/미등장/총)
       - 행: 원어 → [번역어 ✎] · 상태점 · 클릭→점프(원문 하이라이트 + 미리보기 앵커)
       - + 용어 추가
       - (보조) 직접 교체: find→replace (기존 bulk-replace)
 [편집]/[재번역]: 가운데 = OnlyOffice 편집기 (기존 그대로)
```

데이터:
- `GET /documents/{id}/glossary-report?lang=` → audit 결과(항목별 상태 + 위치). lazy(항상 최신 모델 기준).
- `GET /documents/{id}/preview?lang=` → 번역본 HTML(번역어 `<mark>` + 블록 앵커). 읽기전용.
- 번역어 수정 → `bulk_replace`(기존) + 용어집 갱신. 용어 추가 → 용어집 갱신 (+선택 재번역).

## 백엔드 변경

### `backend/pipeline/glossary_audit.py` (신규)
- `audit_glossary(source_model, translated_model, glossary, source_lang, target_lang, span_map=None, page_sizes=None) -> list[dict]`:
  세그먼트 정렬(`iter_segments`/`iter_text_fields`, build_segments와 동일 방식)을 돌며 각 용어집 항목의
  원문 등장 세그먼트와 적용 여부를 산출. 항목별 반환:
  `{ "source": 원어, "target": 번역어, "source_count": int, "applied_count": int, "status": "applied|partial|missed|absent",
     "locations": [ {"segment_index": int, "block": int|None, "source_bbox": {...}|null, "applied": bool} ] }`.
  매칭: 원어는 `compile_rule(ReplaceRule(find=원어, replace=""), source_lang)`, 번역어는 동일하게 target_lang. 순수 함수(I/O 없음).

### `backend/pipeline/process.py`
- `glossary_report(storage_dir, doc_id, target_lang) -> list[dict]`: source/translated(=export) 모델 + `load_doc_glossary` 로드, PDF면 `extract_span_bboxes`로 블록 위치(`build_segments`와 동일 방식), `glossary_audit.audit_glossary` 호출. (lazy; 디스크 변경 없음)
- `update_glossary_term(storage_dir, doc_id, source, new_target, target_lang) -> dict`:
  현재 번역어(old_target) = `load_doc_glossary`에서 조회 → `bulk_replace(storage, doc_id, target_lang, [ReplaceRule(find=old_target, replace=new_target, match="smart", save=False)])`로 번역본 통일 + `glossary.json`의 `{source: new_target}` 갱신. (old_target이 비었으면 치환 생략, 등록만.) 반환 `{replaced: int}`.
- `add_glossary_term(storage_dir, doc_id, source, target) -> None`: `glossary.json`에 `{source: target}` 추가(`save_doc_glossary` 머지). 즉시 적용은 호출부(재번역)에서 별도.
  - 적용용: 기존 `retranslate_one_segment`를 영향 세그먼트(원어 포함)에 대해 호출(엔드포인트가 오케스트레이션).

### `backend/renderers/html_renderer.py`
- `render_html(doc, asset_url_prefix=..., highlight_terms: dict | None = None, anchor_blocks: bool = False)`:
  기존 시그니처에 옵션 추가. `highlight_terms`({번역어: css-class/색}) 있으면 본문 텍스트에서 매칭을 `<mark data-term=...>`로 감싸고, `anchor_blocks`면 블록에 `id="blk{n}"` 부여(미리보기 점프용). 매칭은 `compile_rule`로 통일. 기존 호출부 영향 없음(기본값 None/False).

> 원문 색 하이라이트는 **블록 단위**다: audit `locations[].source_bbox`(세그먼트/블록 박스, PDF만 non-null)를 그 위치의 `applied`로 🟢/🔴 칠한다. 단어 단위 정밀 박스는 적용/누락 색을 occurrence별로 묶기 어려워 v1 비목표(후순위).

### `backend/main.py`
- `GET /api/documents/{doc_id}/glossary-report?lang=` → `process.glossary_report`.
- `GET /api/documents/{doc_id}/preview?lang=` → 번역본 HTML(`render_html(..., highlight_terms=용어집 번역어, anchor_blocks=True)`), `HTMLResponse`.
- `POST /api/documents/{doc_id}/glossary/term?lang=` (body `{source, target}`):
  기존 항목이면 `update_glossary_term`(치환 통일), 신규면 `add_glossary_term`(등록) + body `{apply: bool}`이면 영향 세그먼트 재번역. 반환 `{status, replaced?|retranslated?}`.
- 직접 교체는 기존 `/replace`·`/replace/preview` 그대로 사용.

## 프론트엔드 변경

> 주의: [frontend/AGENTS.md](../../../frontend/AGENTS.md) — 코딩 전 `node_modules/next/dist/docs/` 확인. 변경은 기존 컴포넌트/fetch 패턴 답습.

### [frontend/src/lib/types.ts](../../../frontend/src/lib/types.ts)
- `GlossaryTermStatus = "applied" | "partial" | "missed" | "absent"`.
- `GlossaryLocation { segment_index; block: number|null; source_bbox: SourceBBox|null; applied: boolean }`.
- `GlossaryTermReport { source; target; source_count; applied_count; status; locations: GlossaryLocation[] }`.
- `GlossaryReportResponse { lang; terms: GlossaryTermReport[] }`.

### [frontend/src/lib/api.ts](../../../frontend/src/lib/api.ts)
- `glossaryReport(docId, lang) -> GlossaryReportResponse`.
- `previewUrl(docId, lang) -> string` (`/api/documents/{id}/preview?lang=`, iframe src).
- `saveGlossaryTerm(docId, lang, {source, target, apply}) -> {...}`.
- (직접 교체) 기존 `replacePreview`/`replaceApply` 재사용.

### `frontend/src/components/GlossaryManagePanel.tsx` (신규)
- `glossaryReport`로 항목 로드. 요약 줄 + 행 목록(원어→번역어 인라인 input, 상태점 🟢/🔴/⚪, 클릭→`onJump(location)`).
- 번역어 수정 적용 → `saveGlossaryTerm({apply:true})` → 성공 시 미리보기·원문 하이라이트 새로고침(report 재요청).
- `+ 용어 추가` 행. 추가 시 `saveGlossaryTerm`(+선택 재번역).
- 하단 접이식 **직접 교체** 섹션: 기존 BulkReplacePanel 로직 재사용(또는 컴포넌트 추출 후 양쪽에서 사용).

### `frontend/src/components/TranslatedPreview.tsx` (신규)
- `previewUrl(docId, lang)`를 iframe으로 렌더(하이라이트된 번역본). `onJump` 시 iframe 내 `#blk{n}`로 스크롤.

### [frontend/src/app/c/[id]/page.tsx](../../../frontend/src/app/c/[id]/page.tsx)
- `mode` 유니온 `'replace'` → `'manage'`. 액션바 토글 라벨 `용어 일괄변경` → `용어 관리`.
- 가운데: `mode==='manage'`면 `<TranslatedPreview>` , 아니면 기존 `<OnlyOfficeEditor>`.
- 우측: `mode==='manage'`면 `<GlossaryManagePanel onJump=...>` (재번역 모드는 기존 RetranslatePanel).
- `onJump(location)`: 원문 패널을 해당 위치로 스크롤+하이라이트(기존 activeSeg 하이라이트 재사용, location의 source_bbox 사용) + 미리보기 iframe `#blk{n}` 스크롤.
- 원문 하이라이트: 기존 단일 active 박스 → 용어 관리에서는 **여러 블록 박스**(🟢적용/🔴누락) 오버레이(locations의 source_bbox). PDF=박스 표시, HWP=원문 iframe은 점프만(<mark>는 후순위), Word=없음.

## 엣지 / 에러 처리

- **용어집 미업로드**: report `terms: []` → 패널 "업로드된 용어집 없음" + `+ 용어 추가`는 동작(추가 시 glossary.json 생성).
- **Word 업로드**: 원문 패널 없음 → 원문 색 생략, 리스트 상태점만. 미리보기 하이라이트는 정상.
- **HWP**: 원문은 hwp5html iframe → v1은 점프만(원문 단어 색칠은 후순위), 리스트 상태점 + 미리보기 색은 정상.
- **표/머리말·꼬리말 위치**(block None): 점프 비활성(상태만 표시).
- **미리보기 vs .docx 드리프트**: 미리보기는 모델 기준 → OnlyOffice 직접편집은 미반영 가능. 패널에 "검토용(편집은 편집 모드)" 안내.
- **번역어 수정 시 old_target 부재/누락 세그먼트**: 치환은 등장한 곳만 통일; 누락 세그먼트는 그대로(🔴 유지) → 필요시 재번역.
- **추가 즉시 적용(재번역) 비용**: 영향 세그먼트 수만큼 호출 → "지금 적용"은 명시적 버튼(기본은 등록만).

## 검증

백엔드 테스트:
- `test_glossary_audit.py`: applied/partial/missed/absent 분류, 위치(segment/block) 정확, 매칭 의미(영어 단어경계 — "Load"가 "Download" 미적용, CJK 부분일치, 대소문자 무시), 거짓-누락 케이스(대소문자만 다른 번역어는 적용으로).
- `test_glossary_report.py`(process): 모델+용어집 로드 → 항목별 상태 + 위치(PDF면 source_bbox non-null); 용어집 없음 → [].
- `test_glossary_term_update.py`: `update_glossary_term`이 번역본 치환(bulk_replace) + glossary.json 갱신; `add_glossary_term` 등록.
- `test_render_html_highlight.py`: `render_html(highlight_terms=...)`가 번역어를 `<mark>`로 감싸고 `anchor_blocks`가 `id="blk{n}"` 부여; 옵션 없으면 기존 출력 불변(회귀).
- `test_glossary_api.py` 확장: `/glossary-report`, `/preview`, `/glossary/term` 라우트(TestClient, MockTranslator).

프론트: `npm run build` clean. 수동(prod build): 용어집 업로드 → 용어 관리 → 원문 색(적용/누락) + 번역본 미리보기 색 확인 → 번역어 수정 → 통일 확인 → 용어 추가 → 재번역 적용 확인.

## 빌드 순서

1. `glossary_audit.py`(순수 분류) + 테스트.
2. `render_html` 하이라이트/앵커 옵션 + 테스트(회귀 포함).
3. `process.glossary_report` / `update_glossary_term` / `add_glossary_term` + 테스트.
4. `main.py` 라우트(`/glossary-report`, `/preview`, `/glossary/term`) + 테스트.
5. 프론트: types → api → `TranslatedPreview` → `GlossaryManagePanel`(직접 교체 섹션 포함) → `c/[id]/page.tsx` 모드 개편(`replace`→`manage`, 가운데 스왑, 원문 블록 하이라이트).
6. 통합 검증(prod build).
