# 재번역 모드: HTML 뷰어 + 문장 클릭 선택 (Design)

**Date:** 2026-06-25
**Status:** Approved (design); pending implementation plan

## Goal

재번역(retranslate) 모드를 OnlyOffice 에디터에서 **클릭 가능한 HTML 뷰어**로 바꾼다. 사용자가 원본/번역본 뷰어에서 **문장을 직접 클릭해 선택**하고, 오른쪽 패널에서 재번역하도록 한다. OnlyOffice는 **문서 편집** 모드에서만 마운트한다.

## Motivation

1. **일관성** — 용어 관리·고유명사 검수는 이미 HTML 미리보기인데 재번역만 무거운 OnlyOffice라, 모드 전환 시 중앙 패널이 에디터↔HTML로 튄다.
2. **성능/안정성** — 재번역 진입마다 OnlyOffice가 재마운트되고, Community Edition에선 apply마다 docx 전체 re-render + editor reload가 발생해 느리고 불안정하다.
3. **클릭 선택** — (핵심) 사용자가 원본/번역본 화면에서 문장을 클릭해 선택하길 원한다. 이는 HTML이라야 가능하고 OnlyOffice 에디터에선 불가능하다.

## Current architecture (as explored)

- **/c 페이지 모드:** `edit | retranslate | manage | proper-nouns`. 중앙 "번역본" 패널은 `manage|proper-nouns` → `TranslatedPreview`(HTML iframe), 그 외(`edit|retranslate`) → `OnlyOfficeEditor`. 즉 **현재 재번역도 OnlyOffice를 쓴다.**
- **`TranslatedPreview`** (`frontend/src/components/TranslatedPreview.tsx`): same-origin iframe (`api.previewUrl`), `reloadKey`(새로고침) + `jumpBlock`(`#blk{n}` 해시 스크롤) 지원.
- **`RetranslatePanel`** (`frontend/src/components/RetranslatePanel.tsx`): 문장 리스트 + 선택 시 프리셋/지시/미리보기/적용. 현재 OnlyOffice 커넥터로 `highlightInEditor`(문장 하이라이트) 및 `searchAndReplace`(Developer Edition 즉시 치환) 사용; Community Edition은 `api.render` 후 에디터 reload.
- **세그먼트↔블록 granularity** (`backend/models.py:iter_segments`): 세그먼트 = 번역 대상 텍스트 필드. Heading·단일 라인 문단은 1 세그먼트 = 1 블록이지만, **다중 라인 문단·리스트·표 셀**은 한 `blk{i}` 블록 안에 **여러 세그먼트**가 있다. `build_segments`(`backend/pipeline/process.py:426`)가 `{index, source, translated, source_bbox, block}`를 산출하며, `index`는 `iter_segments` 열거 위치(비번역 atomic 세그먼트도 인덱스 보존, 리스트에선 제외).
- **HTML 렌더러** (`backend/renderers/html_renderer.py`): 블록마다 `id="blk{i}"`만 부착하고 `block.text`(블록 전체)를 렌더. **개별 세그먼트(문장) 마크업 없음.**
- **원본 패널** (`/c` 페이지): `doc.source_html ? <iframe(raw hwp5html)> : page_images.map(<img>)`. PDF 업로드 → 페이지 이미지 + `source.pdf` 기반 `source_bbox`. HWP/오피스 → `source_html` = **raw hwp5html**(`/api/documents/{id}/source-html/index.xhtml`, `main.py:656`), bbox·세그먼트 마크업 없음. (참고: `render_html`은 `/preview`(번역본)와 `/view`에만 쓰이고, HWP 원본은 raw hwp5html.)

## Chosen approach: 타입별 최적 (Approach 2)

원본 패널을 타입별로 가장 적합한 방식으로 클릭 가능하게 한다. 번역본은 전 타입 공통.

## Design

### A. 모드 → 뷰 매핑 변경
중앙 "번역본" 패널:
- `edit` → `OnlyOfficeEditor` (유일하게 에디터 마운트)
- `retranslate` → `SegmentPreview`(클릭 가능 HTML) — **변경점**
- `manage | proper-nouns` → 기존 HTML 미리보기 (변경 없음)

원본(좌) 패널 — `retranslate` 모드에서만 클릭 활성:
- **PDF** → 페이지 이미지 유지 + 각 세그먼트 `source_bbox`를 투명 **클릭 핫스팟**으로 오버레이 (프런트엔드만)
- **HWP/오피스** → `source-preview`(우리 렌더러로 그린 source 모델 + `data-seg`)로 교체 (raw hwp5html는 `retranslate`에서만 대체; 다른 모드는 그대로)

### B. 백엔드

**B1. `html_renderer`에 `wrap_segments` 모드.**
번역 대상 텍스트 필드마다 `<span class="seg" data-seg="{i}">…</span>`로 감싼다. `i`는 `iter_segments` 열거 인덱스이며 **`build_segments`의 `index`와 정확히 일치해야 한다.** 다중 라인 문단·리스트·표 셀은 필드별로 감싸야 하므로, 렌더러는 `block.text` 단일 문자열이 아니라 `iter_segments`와 동일한 순서로 필드를 순회해 각 필드를 span으로 감싼다. `.seg { cursor: pointer }`, `.seg:hover`, `.seg-active`(선택 강조) CSS를 렌더러 출력 `<style>`에 추가. 기존 용어 하이라이트(`_mark`)와 공존해야 한다(span 중첩/인접 허용).
- **리스크(최대):** 렌더러의 `data-seg` 인덱스가 `build_segments` 인덱스와 어긋나면 선택이 엉뚱한 문장을 가리킨다. → 공유 열거 로직 + "렌더된 `data-seg` 집합 == `build_segments` 인덱스 집합" 테스트로 고정(다중 라인 문단 + 표 셀 포함 픽스처).

**B2. source-preview 엔드포인트.**
`GET /api/documents/{id}/source-preview` → `render_html(source 모델, wrap_segments=True)` (owner-guarded, 기존 preview 라우트 패턴). HWP/오피스 원본 클릭용. 기존 `/preview`(번역본)도 `wrap_segments=True` 적용. `api` 클라이언트에 `sourcePreviewUrl(docId)` 추가.
- **manage/proper-nouns 무변경 보장:** `data-seg` span은 레이아웃에 영향 없는 **비활성 마크업**이다. 클릭/hover 커서 스타일(`.seg:hover` 등)은 `SegmentPreview`가 인터랙티브일 때만 iframe 문서에 적용(예: body에 `clickable-segs` 클래스 추가)하므로, `manage|proper-nouns`의 `TranslatedPreview`(클릭 비활성)는 시각·동작이 동일하다. `.seg-active`도 active가 설정될 때만 나타난다.

### C. 프런트엔드 — `SegmentPreview` 컴포넌트
`TranslatedPreview`를 확장(또는 신규)한 클릭 가능 미리보기.
- props: `src`(번역본=previewUrl / 원본=sourcePreviewUrl), `activeSeg: number | null`, `onSelectSeg(index)`, `reloadKey`.
- same-origin iframe이므로 직접 DOM 접근: `onLoad` 시 iframe 문서에 `[data-seg]` 클릭 위임 리스너 부착 → `onSelectSeg(Number(el.dataset.seg))`.
- `activeSeg` 변경 시: 이전 `.seg-active` 제거, `[data-seg="{activeSeg}"]`에 `.seg-active` 추가 + `scrollIntoView({block:"nearest"})`.
- `reloadKey` 변경 시 iframe 재마운트(적용 후 새 번역 반영) 후 active 재적용.
- 번역본(전 타입) + HWP/오피스 원본(retranslate)에서 재사용.

### D. /c 페이지 — 선택 상태 리프트 + 양방향 동기화
- `activeSegIndex: number | null`(+ 선택 세그먼트 객체)를 페이지 상태로 끌어올린다.
- 선택 트리거(모두 `setActiveSegIndex` 호출):
  - 리스트 행 클릭 (RetranslatePanel)
  - 번역본 `SegmentPreview` `[data-seg]` 클릭
  - 원본 클릭: PDF → bbox 핫스팟 클릭 / HWP·오피스 → 원본 `SegmentPreview` `[data-seg]` 클릭
- 선택 시: 번역본·원본 미리보기에 `activeSeg` 전달(하이라이트+스크롤), RetranslatePanel 해당 행 펼침.
- **PDF 원본 핫스팟:** 기존 active-bbox 오버레이(page.tsx:361–376)를 확장 — `build_segments`의 모든 `source_bbox`를 투명 클릭 div로 렌더(hover 시 윤곽), active는 강조. bbox가 `null`인 세그먼트는 핫스팟 없음(번역본/리스트로 선택).

### E. RetranslatePanel 재배선
- OnlyOffice 커넥터 의존 제거(`highlightInEditor`, `searchAndReplace`, `connector` prop, `onlyofficeConnector` import 삭제).
- 선택은 controlled: `activeSeg` prop을 받아 해당 행을 active로 표시/펼침. 내부 `active` 상태 대신 부모 주도.
- 리스트 유지(순차 검토 + 검색용).
- **적용(apply):** `doPreview`가 이미 `translated.{lang}.json`에 영속화. apply 시 → 리스트 로컬 반영 + 번역본 미리보기 `reloadKey++`(모델 기반이라 즉시 반영) + `api.render(docId, lang)`로 docx 재생성(다음 문서 편집 진입 시 최신). 에디터 reload 없음.

### F. 데이터 흐름 (재번역 1회)
1. 사용자가 문장 클릭(리스트/번역본/원본 중 하나) → `activeSegIndex` 설정 → 양 패널 하이라이트+스크롤, 패널 컨트롤 표시.
2. 프리셋/지시 입력 → 재번역 → `POST .../retranslate-segment`가 새 번역을 `translated.json`에 영속화하고 미리보기 텍스트 반환.
3. 적용 → 리스트 반영 + 번역본 `reloadKey++`(새 텍스트 표시) + `api.render`로 docx 동기화.
4. 문서 편집 진입 시 에디터가 최신 docx 로드.

## Error handling / edge cases
- bbox가 `null`인 PDF 세그먼트 → 원본 핫스팟 없음(번역본/리스트로 선택).
- 표 셀·다중 라인·도형 오버레이 세그먼트 → `data-seg`로 처리(B1).
- 비번역(atomic/빈) 세그먼트 → 인덱스 보존하되 클릭 대상 아님(리스트에서도 제외).
- 재번역 적용 후 문서 편집 진입 시 docx 최신화(apply의 `api.render`).
- HWP 원본은 `retranslate` 모드에서만 source-preview로 대체; `manage|proper-nouns|edit`은 기존 동작 유지.

## Testing
- **백엔드(pytest):** `wrap_segments` 인덱스 정렬 테스트 — 다중 라인 문단 + 표 픽스처에서 렌더된 `data-seg` 집합 == `build_segments` 인덱스. source-preview 엔드포인트(200 + `data-seg` 포함, owner-guard). 기존 preview/term-highlight 회귀.
- **프런트:** `tsc --noEmit` + eslint. 브라우저: 번역본 클릭→선택·하이라이트·스크롤; PDF 원본 핫스팟 클릭→선택; HWP source-preview 클릭→선택; 리스트↔뷰어 양방향 동기화; 적용 후 미리보기 새로고침; 문서 편집 진입 시 최신 반영.

## Build order
1. 렌더러 `wrap_segments` + 인덱스 정렬 테스트
2. `source-preview` 엔드포인트 + `api` 클라이언트
3. `SegmentPreview` 컴포넌트
4. /c 선택 상태 리프트 + 번역본 클릭 연결
5. 원본 클릭 (PDF bbox 핫스팟 + HWP source-preview)
6. RetranslatePanel 재배선 (커넥터 제거, controlled, apply 흐름)
7. 검증 (pytest + tsc + 브라우저)

## Out of scope
- 문장 단위보다 더 세밀한 선택(구·단어).
- Developer Edition의 에디터 내 즉시 치환(재번역은 에디터를 안 쓰므로 불필요).
- `manage|proper-nouns` 모드의 사용자 동작 변경 (미리보기 HTML에 비활성 `data-seg` span이 추가되나 시각·동작은 동일).
- raw hwp5html에 세그먼트 마크업 주입(Approach 3, 미채택).
