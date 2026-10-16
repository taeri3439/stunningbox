# 재번역 모드 (Partial Re-translation Mode) — 설계

작성일: 2026-06-03 · 브랜치: `partialretranslate-2026-06-03`

## 1. 배경 / 목적

사용자는 compare 화면 우측 OnlyOffice 에디터에 뜬 번역 결과(`output.{lang}.docx`)를 보며
검수한다. 일부 문장의 번역이 마음에 안 들 때, **그 문장만 방향을 지정해 재번역**하고 싶다
(예: "이 문장은 더 간략하게", "여기는 법률 용어를 그대로 유지").

전체 재번역은 비싸고(API), 검수한 다른 부분까지 바뀐다. 그래서 **문장(세그먼트) 단위로
골라 가이드를 주어 재번역**하는 "재번역 모드"를 도입한다.

핵심 통찰: 번역 파이프라인은 이미 **leaf 텍스트 필드(문장/줄/문단/셀)** 단위로 동작한다
([`iter_text_fields`](../../../backend/models.py) L267). 재번역을 "문장 단위"로 못 박으면
임의 부분문자열 선택 → fuzzy 매칭 → 부분 교체라는 난제가 통째로 사라지고, **세그먼트
인덱스로 원문↔번역을 정확히 매핑**할 수 있다.

## 2. 사용자 결정 사항 (확정)

- **UI 패턴**: 패턴 2 — *문장 목록 모드*. "재번역 모드" 토글 시 우측이 모델에서 렌더한
  `원문 ↔ 번역` 문장 목록으로 전환. 행을 클릭해 재번역.
- **재번역 기준**: *원문 기준* — 선택한 문장의 한국어 원문을 가이드를 반영해 새로 번역
  (현재 영문을 다듬는 것이 아니라). 의미 드리프트가 적고 충실함.
- **기본 방향 프리셋(택1)** + **커스텀 프롬프트(항상 제공)**:
  - 간략하게 → "more concise — keep only the essential meaning"
  - 상세하게 → "more detailed and explanatory; make implicit meaning explicit"
  - 격식·문어체 → "formal, written register suitable for an official document"
  - 자연스럽게(의역) → "idiomatic / natural rather than literal, preserving meaning"
  - 커스텀 프롬프트는 프리셋 문구 뒤에 그대로 append.
- **양쪽 하이라이트**: 문장 목록에서 한 행을 선택(hover/click)하면 그 문장을 **원본(좌)과
  번역본 OnlyOffice(우)** 양쪽에서 시각적으로 하이라이트해 "어디를 고치는지" 보여준다.
  - 우(OnlyOffice): 적용에 쓰는 동일 connector로 `Search` → `Select`(스크롤+선택 표시) 또는
    `SetHighlight`(배경색). 같은 connector 스파이크에 묶임.
  - 좌(PDF): 세그먼트의 `source_span_ids` → `source.pdf` 재추출로 bbox 복원 → 페이지 비율
    오버레이 사각형. 데이터 가용성 확인됨(§8).
  - 좌(HWP): PDF 좌표가 없음 → **v1 보류**(오른쪽만 하이라이트). iframe DOM 텍스트 검색
    근사 하이라이트는 후속 best-effort.

## 3. 비목표 (v1 범위 밖)

- 단어/부분문자열 단위 재번역 — **세그먼트(문장/줄/문단/셀) 단위만**.
- 여러 문장 동시 선택 재번역 — v1은 행 1개씩(추후 다중 선택 확장 여지).
- 표 구조·그림 등 비텍스트 재번역.
- 재번역 모드와 별개의 자동 용어집 학습/저장.
- HWP 좌측(원본) 하이라이트 — bbox 부재로 보류(오른쪽만).
- 단어 단위 정밀 bbox — 좌측 하이라이트는 **블록(문장) 단위 union bbox**.

## 4. 진실의 원천(Source of Truth)과 일관성

기존 아키텍처는 **편집 후 `.docx`가 진실의 원천**이다:
[`_ensure_docx`](../../../backend/main.py) L50 은 파일이 **없을 때만** 렌더하므로, OnlyOffice가
저장한 docx를 모델로 덮어쓰지 않는다. 이 원칙을 깨지 않는다.

따라서 재번역 적용은 **에디터 안에서 교체**하고 OnlyOffice 자동저장에 맡긴다(.docx 진실 유지).
동시에 **문장 목록의 정합성**을 위해 `translated.{lang}.json`의 해당 세그먼트도 갱신한다
(이건 "번역 캐시"의 정당한 업데이트이며, docx를 모델로 재렌더하지 않으므로 수동 편집을
덮어쓰지 않는다). `edited.{lang}.json`은 v1에서 **건드리지 않는다**.

요약 표:

| 대상 | 적용 시 처리 | 이유 |
|---|---|---|
| OnlyOffice 에디터(.docx) | connector로 old→new 교체 → 자동저장 | .docx 진실 유지, 수동 편집 보존 |
| `translated.{lang}.json` | 해당 인덱스 세그먼트만 새 번역으로 write-back | 문장 목록 재진입 시 정합 |
| `edited.{lang}.json` | 손대지 않음 | 모델↔docx 발산 방지 |

## 5. 아키텍처 / 데이터 흐름

```
[재번역 모드 토글 ON]
  → GET /api/documents/{id}/segments?lang=en
       · load_source_model + get_translated_model 을 iter_text_fields로 lockstep 순회
       · source.pdf 1회 재추출 → span_id→bbox 맵 → 세그먼트별 union bbox(페이지 비율)
       · [{ index, source_text, translated_text, source_bbox|null }] (빈/atomic 제외) 반환
  → 우측 패널이 '원문 ↔ 번역' 목록으로 렌더

[행 hover/select → 양쪽 하이라이트]
  → 좌(PDF): source_bbox로 페이지 비율 오버레이 사각형 표시 (HWP면 생략)
  → 우(OnlyOffice): connector.callCommand(Search(translated_text) → Select/SetHighlight)
  → 행 이동 시 이전 하이라이트 해제

[행 클릭 → 가이드 패널]
  → 프리셋(택1) + 커스텀 프롬프트 입력
  → POST /api/documents/{id}/retranslate-segment
        body: { lang, index, preset, guidance }
        · source 모델에서 index의 원문 추출(iter_text_fields)
        · 가이드 주입한 1-세그먼트 번역 수행
        · translated.{lang}.json[index] write-back
        · 반환: { index, old_text, new_text }
  → 패널에 [기존 ↔ 재번역] 미리보기

[적용]
  → connector.executeMethod("SearchAndReplace", [{ searchString: old_text,
                                                    replaceString: new_text }])
  → 목록 행을 new_text로 갱신
  → (못 찾으면) "에디터에서 해당 문장이 수정되어 자동 반영 불가" + [복사] 폴백
```

세그먼트 인덱스는 `source.json` / `translated.{lang}.json`이 **동일 구조·순서**라서
GET과 POST 사이에서 안정적이다(둘 다 같은 `iter_text_fields` 순회).

## 6. 백엔드 변경

### 6.1 신규 엔드포인트 ([backend/main.py](../../../backend/main.py))

- `GET /api/documents/{doc_id}/segments?lang=<t>`
  - `load_source_model` + `get_translated_model`을 lockstep 순회.
  - **bbox 부착(PDF)**: `source.pdf`가 있으면 `extractor`로 1회 재추출해 `span_id→bbox` 맵을
    만들고, 각 세그먼트의 `source_span_ids` union bbox를 **페이지 비율**(`{page,x,y,w,h}` 0~1)로
    계산해 첨부. id 미발견/ HWP(소스 pdf 없음) → `source_bbox = null`(좌측 하이라이트 생략).
  - 반환: `{ "lang", "segments": [{ "index": int, "source": str, "translated": str,
    "source_bbox": {page,x,y,w,h}|null }] }`.
  - 번역 대상이 아닌 세그먼트(`_is_atomic` / 공백)는 제외하되 **원래 인덱스는 보존**.
  - span bbox 맵은 `(doc_id)`별로 캐시(매 요청 재추출 방지).
- `POST /api/documents/{doc_id}/retranslate-segment`
  - body: `{ "lang": str, "index": int, "preset": str|None, "guidance": str|None }`.
  - `load_source_model`로 원문 세그먼트 텍스트 취득(인덱스 검증, 범위 밖 → 422).
  - `translate_segment(...)`로 가이드 반영 번역.
  - `get_translated_model` 로드 → 해당 index setter로 new_text 적용 → `translated.{lang}.json` 저장.
  - 반환: `{ "index", "old_text", "new_text" }`.

### 6.2 번역기 가이드 주입 ([backend/pipeline/translator.py](../../../backend/pipeline/translator.py))

- `Translator.translate(..., guidance: str = "")` 인자 추가(Mock·Claude 양쪽).
  - `ClaudeTranslator`: 기존 system 프롬프트(충실 번역 + 숫자/단위/코드 보존 + 글로서리)에
    `guidance`가 있으면 한 문단 append. 글로서리·충실성 규칙은 유지(가이드는 가산).
  - `MockTranslator`: 가이드를 태그에 포함시켜 테스트에서 확인 가능.
- `PRESET_GUIDANCE: dict[str,str]` 상수 + `build_guidance(preset, custom)` 헬퍼
  (프리셋 문구 + 커스텀 프롬프트 결합). 단일 출처.
- `translate_segment(source_text, source_lang, target_lang, guidance, translator=None)`:
  단일 문자열을 글로서리와 함께 번역해 반환(빈/atomic이면 그대로 반환).

### 6.3 건드리지 않는 것

- `iter_text_fields`, `translate_document`, `render_docx`, `_ensure_docx`,
  `get_export_model`, OnlyOffice 콜백 — 변경 없음.

## 7. 프론트엔드 변경

> ⚠️ 구현 주의: [frontend/AGENTS.md](../../../frontend/AGENTS.md) — "이 Next.js는 너가 아는
> 버전이 아니다. 코딩 전 `node_modules/next/dist/docs/` 확인." 구현 시 준수.

### 7.1 [OnlyOfficeEditor.tsx](../../../frontend/src/components/OnlyOfficeEditor.tsx)

- `events.onDocumentReady`에서 `editorRef.current.createConnector()`를 만들어 ref/상위로 노출.
- 부모가 쓸 수 있게 `onConnector?(connector)` 콜백 prop 또는 `forwardRef`로 노출.
- 적용 헬퍼: `searchAndReplace(connector, oldText, newText): Promise<boolean>`
  (`executeMethod("SearchAndReplace", ...)`, 콜백을 Promise로 래핑, 미발견 시 false).
- 하이라이트 헬퍼: `highlight(connector, text): Promise<boolean>` — `callCommand`로
  `Api.GetDocument().Search(text)` → 첫 `ApiRange.Select()`(스크롤+표시) 또는 `SetHighlight`.
  `clearHighlight(connector)` — 이전 하이라이트 해제(SetHighlight 사용 시).

### 7.2 [page.tsx](../../../frontend/src/app/c/[id]/page.tsx) (compare 화면)

- 상단 액션바에 **"재번역 모드" 토글** 추가.
- 모드 ON: **우측은 `RetranslatePanel`로 전환하되 `OnlyOfficeEditor`는 unmount하지 말고
  숨김 유지**(connector/세션·하이라이트 대상이므로). 좌측(원본)은 그대로 보이며 오버레이만 추가.
- 상태: `mode: "edit" | "retranslate"`, `segments`, `activeIndex`, `preview`, `highlightBbox`.
- 좌측 하이라이트는 page.tsx의 PDF 페이지 렌더 위에 **절대배치 오버레이**(아래 7.4)로 그림.

### 7.3 신규 컴포넌트 `RetranslatePanel`

- 마운트 시 `GET /segments` → `원문 ↔ 번역` 행 목록 렌더(스크롤). 각 행에 ✎ 버튼.
- 행 hover/select → 상위로 `activeIndex` 전파 → 좌측 오버레이 + 우측 connector 하이라이트.
- 행 클릭 → 인라인 가이드 박스: 프리셋 칩(택1) + 커스텀 프롬프트 textarea + [미리보기].
- [미리보기] → `POST /retranslate-segment` → `old ↔ new` 비교 표시 + [적용].
- [적용] → `searchAndReplace(old, new)`:
  - 성공 → 행을 new로 갱신, "적용됨" 표시.
  - 실패 → 경고 + [복사] 버튼(new_text 클립보드 복사, 수동 교체 안내).

### 7.4 좌측 원본 하이라이트 오버레이

- `segments[i].source_bbox = {page, x, y, w, h}`(0~1 비율). 활성 세그먼트의 `page`에 해당하는
  PDF 페이지 이미지 컨테이너 위에 `position:absolute; left:x%; top:y%; width:w%; height:h%`
  반투명 박스. 좌표가 비율이라 표시 크기와 무관.
- 활성 세그먼트의 페이지로 좌측 패널 **자동 스크롤**.
- `source_bbox=null`(HWP/미발견) → 좌측 오버레이 생략(우측만).

### 7.5 API 클라이언트 ([frontend/src/lib/api.ts](../../../frontend/src/lib/api.ts)) + 타입

- `segments(docId, lang)`, `retranslateSegment(docId, body)` 추가.
- [types.ts](../../../frontend/src/lib/types.ts)에 `Segment`(`source_bbox` 포함), `RetranslateResponse` 타입.

## 8. 리스크 / 스파이크 (착수 전 1순위)

**connector 스파이크** — 현재 `customization.plugins:false`
([onlyoffice.py](../../../backend/onlyoffice.py) L102). 이 플래그는 플러그인 *탭 UI*만 끄고
자동화 connector API는 보통 살아있다. 그러나 **현 Community DS 버전에서
`createConnector` / `executeMethod("SearchAndReplace")`가 실제로 동작하는지** 실행 중인 DS로
먼저 검증한다(스모크: 빈 doc에 connector 만들어 SearchAndReplace + Search/Select 1회).
이 스파이크는 **적용(SearchAndReplace)과 우측 하이라이트(Search/Select) 둘 다**를 게이트한다.

- ✅ 동작 → 본 설계대로(에디터 in-place 교체 + 우측 하이라이트).
- ❌ 미동작 → 폴백 결정 필요:
  - (폴백 A, 무 connector) 미리보기 패널의 **[복사]만 제공**, 사용자가 수동 붙여넣기.
    적용은 수동이지만 .docx 진실 유지·구현 단순.
  - (폴백 B) `translated.json` 갱신 후 **docx 강제 재렌더 + 에디터 reload**. 단 수동 편집
    유실 위험 → 재번역 모드에서는 수동 편집 비권장 안내 동반. (docx_extractor 라운드트립은 v1 미사용.)

부차 리스크:
- **SearchAndReplace 중복 일치**: 짧거나 반복되는 문장은 다중 교체 위험. 완화: old_text가
  충분히 길/고유할 때만 자동 적용, 아니면 폴백 안내. (스파이크에서 매칭 옵션 확인.)
- **수동 편집 드리프트**: 사용자가 OnlyOffice에서 그 문장을 이미 고쳤으면 old_text 미발견 →
  폴백 메시지로 명확히 처리(무해).
- **DS 세션/토큰**: 재번역 모드에서 에디터를 숨기되 unmount하지 않아 토큰/세션 유지.
- **좌측 bbox 정합(재추출 id 안정성)**: 좌측 하이라이트는 `source.pdf`를 추출기로 재추출해
  `source_span_ids`를 bbox에 매핑한다. id 체계가 추출기와 결정적으로 동일(`p{pg}b{bk}l{ln}s{sp}`,
  [extractor.py](../../../backend/pipeline/extractor.py) L48)하므로 안정적이나, PyMuPDF 버전 변동 시
  id 불일치 가능 → 미발견 세그먼트는 `source_bbox=null`(좌측 생략)로 **무해 처리**. 확인됨: 실제
  PDF 문서에서 블록 `source_span_ids` 채워져 있음.
- **HWP 좌측 하이라이트**: bbox 없음 → v1 생략(우측만). 후속으로 동일 출처 iframe DOM 텍스트
  검색 근사 가능하나 fuzzy.

## 9. 테스트

- 백엔드(`backend/tests/`, 기존 스타일):
  - `test_translator_guidance.py`: `build_guidance` 결합, `translate` guidance가 system에
    반영(Mock으로 확인), `translate_segment` 빈/atomic no-op.
  - `test_retranslate_endpoint.py`(또는 기존 api 테스트 확장): `/segments`가 source↔translated
    1:1·인덱스 보존, `/retranslate-segment`가 해당 인덱스만 갱신하고 `translated.json`에 반영,
    범위 밖 인덱스 422.
  - `test_segment_bbox.py`: PDF 픽스처에서 세그먼트 `source_bbox`가 0~1 비율·올바른 page,
    span id 미발견 → `null`, source.pdf 없는(HWP) 모델 → 전부 `null`.
- 프론트: connector 래퍼(검색/교체/하이라이트)는 스파이크 후 수동 검증(실제 DS 필요).
  좌측 오버레이 비율 계산은 순수 함수로 단위 테스트 가능. 패널 렌더/상태는 단순 유지.
- 통합: 대표 문서로 재번역 모드 진입 → 한 문장 "간략하게" + 커스텀 → 적용 → 에디터 반영
  육안 확인(메모리: dev HMR 깨짐 → prod build + next start).

## 10. 빌드 순서

1. **connector 스파이크**(실행 중 DS) — SearchAndReplace + Search/Select 통과/폴백 확정. ← 게이트
2. 백엔드: translator guidance + `translate_segment` + 테스트.
3. 백엔드: `/segments`(+ source_bbox), `/retranslate-segment` 엔드포인트 + 테스트.
4. 프론트: api 클라이언트/타입 → `OnlyOfficeEditor` connector(교체+하이라이트) 노출 →
   `RetranslatePanel` → 좌측 오버레이 → compare 화면 토글.
5. 통합 검증(재처리 불필요 — 기존 doc로 모드 진입). prod build로 OnlyOffice 확인:
   행 선택 시 좌(원본 bbox)·우(에디터) 동시 하이라이트 + 재번역 적용.
