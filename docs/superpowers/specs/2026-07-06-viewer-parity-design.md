# Viewer parity — 번역본 패널을 편집기와 100% 일치시키기 — Design

**Date:** 2026-07-06  **Status:** Approved  **Branch:** `viewer-parity-2026-07-06` (stacked on `fidelity-followups-2026-07-02`)

## Problem

번역본 중앙 패널이 모드마다 다른 렌더러로 그려진다: 문서 편집 모드는 OnlyOffice가 `output.{lang}.docx` 실물을, 재번역/용어 모드는 `render_html`이 만든 HTML(iframe)을 보여준다. HTML 렌더러는 페이지 개념이 없고(연속 스크롤) 커버 배경·러닝 밴드 장식·PAGE 필드·TOC 리더·폰트를 구현하지 않아, 편집기 화면과 절대 일치할 수 없다. 렌더러가 둘이라 DOCX 쪽 시각 기능을 추가할 때마다 이중 유지보수가 생긴다.

## Approach (verified)

**같은 엔진, 같은 파일.** OnlyOffice Document Server의 ConvertService는 편집기와 동일한 레이아웃 엔진(x2t)으로 변환하므로, `output.{lang}.docx` → PDF 변환 결과는 편집기 화면과 픽셀 단위로 동일하다. 이 PDF를 150 DPI 페이지 PNG로 렌더해 번역본 패널에 깔고, 상호작용(세그먼트 클릭·용어/고유명사 하이라이트)은 **원본 패널과 동일한 방식**(PyMuPDF bbox → 퍼센트 오버레이)으로 재현한다.

스모크 테스트로 검증됨(2026-07-06, 로컬 DS Community): 21페이지 docx→pdf 2.2초/534KB, `page.get_text('words')`·`search_for` 정상 동작(텍스트 레이어 있음).

## Components

### 1. `backend/pipeline/ds_convert.py` — ConvertService 클라이언트

- `ensure_pdf(storage_dir, doc_id, lang) -> str`: `output.{lang}.docx` → `output.{lang}.pdf`. 신선하면 스킵.
  - **신선도**: `onlyoffice.document_key(docx_path)`를 사이드카 `output.{lang}.pdf.key`에 저장; 불일치/부재 시 재변환.
  - **요청**: `POST {DS_INTERNAL_URL}/ConvertService.ashx`, JSON body `{async: false, filetype: "docx", outputtype: "pdf", key: document_key(docx)+"-pdf", url: "{BACKEND_URL}/api/documents/{id}/docx?lang=", title}`, body에 `token=onlyoffice.sign(body)` (forcesave와 동일 관례), `Accept: application/json`, timeout 120s.
    - DS는 key로 변환 결과를 캐시하므로 key는 docx 버전마다 달라야 하고 편집기 세션 key와 구분되게 `-pdf` 접미사를 붙인다 (charset `[0-9a-zA-Z._=-]`, ≤128자 준수).
  - **응답**: `endConvert:true`의 `fileUrl`을 `onlyoffice.ds_fetch_url()`로 재작성해 다운로드. `{"error": -N}`이면 `DsConvertError(code)` (라우트에서 503으로 표면화).
- 동기(async:false) 방식 — 실측 2.2초라 폴링 불필요. httpx 사용.

### 2. `backend/pipeline/pdf_map.py` — 페이지 이미지 + bbox 매핑

- `render_pages(pdf_path, out_dir, dpi=150) -> dict`: 페이지별 `tpage{N}.png` + 매니페스트 `{version, page_w, page_h, pages:[{n, name}]}` (pt 단위 페이지 크기). 신선도는 사이드카 `manifest.json`의 `version`(= pdf의 document_key)으로 판정, 재생성 시 디렉토리 정리.
- `map_segments(pdf_path, segments: list[{id, text}]) -> dict[id, bbox|None]`: **순서 기반 탐욕 정렬**. 문서 순서의 세그먼트 텍스트를, 페이지 순서로 이어붙인 PDF 단어 스트림에 커서를 전진시키며 매칭. 매칭된 단어들의 union rect(세그먼트 첫 페이지)를 페이지 분수 좌표 `{page, x, y, w, h}`로 반환 — `extractor.segment_source_bbox`와 같은 모양.
  - 정규화: casefold + 공백 붕괴 + 리더 점 런(`[.·…]{2,}`) 제거.
  - 커서와 세그먼트 사이의 비매칭 단어(러닝 밴드, 쪽번호)는 건너뛴다(lookahead 윈도).
  - 못 찾으면 `None` — 그림 폴백 테이블처럼 텍스트가 이미지인 세그먼트는 정상적으로 hotspot 없음.
  - 코어 정렬은 `(words, segments)`를 받는 **순수 함수**로 분리해 단위 테스트.
- `find_terms(pdf_path, terms: list[str]) -> dict[term, list[bbox]]`: `page.search_for(term)` 기반, 페이지 분수 좌표, term당 최대 200히트.

### 3. `backend/main.py` — 라우트 4종 (모두 기존 `/docx`처럼 doc_id 캐퍼빌리티 기반, 인증 의존성 동일 수준)

- `GET /api/documents/{id}/tpages?lang=`: `_ensure_docx` → `ensure_pdf` → `render_pages` → `{version, page_w, page_h, pages:[{n, url}]}`. DS 실패 시 503 `{detail}`.
- `GET /api/documents/{id}/tpages/{n}.png?lang=&v=`: PNG FileResponse (`v`는 캐시버스터).
- `GET /api/documents/{id}/tsegments?lang=`: `/segments`와 같은 세그먼트 id 체계로 `{segments:[{id, bbox}]}` — 프런트가 id로 조인.
- `POST /api/documents/{id}/tfind?lang=` body `{terms:[...]}` → `{term: [bbox...]}`.

### 4. Frontend — `TranslatedPages.tsx` + 배선

- 새 컴포넌트 `frontend/src/components/TranslatedPages.tsx`: 원본 패널과 같은 페이지-이미지 칼럼(aspect-ratio 래퍼 + `<img>`), 그 위에
  - 재번역 모드: 세그먼트 hotspot 버튼(퍼센트 배치, 활성 = primary 아웃라인 + scrollIntoView, 클릭 → onSelectSeg) — 원본 패널 hotspot과 동일한 시각 언어.
  - 용어 모드: 용어 하이라이트 오버레이(앰버) + 활성 고유명사 오버레이(인디고, 첫 히트로 스크롤).
  - 매니페스트 로딩/재변환 동안: 이전 페이지 유지 + 소프트 로딩 오버레이("편집기 레이아웃으로 페이지 준비 중…"); 첫 로드는 스켈레톤.
- `frontend/src/lib/api.ts`: `tpages(id, lang)`, `tpageUrl`, `tsegments`, `tfind` 추가.
- `frontend/src/app/c/[id]/page.tsx`: 번역본 패널에서 재번역 모드의 `SegmentPreview`와 용어 모드의 `TranslatedPreview`를 `TranslatedPages`로 교체. **편집 모드(OnlyOffice)와 원본 패널은 무변경.** `previewReloadKey`/`manageKey` 갱신 → 매니페스트 재조회(재번역 적용 후 `/render` → docx 갱신 → version 변경 → 자동 재변환). HWP 원본 패널용 `SegmentPreview` 사용처는 유지. `TranslatedPreview`가 참조 0이 되면 컴포넌트 삭제.

## Error handling

- DS 미기동/변환 실패: `/tpages` 503 → 프런트는 패널에 재시도 버튼과 안내 문구 표시(편집 모드는 영향 없음).
- 세그먼트 bbox 미매칭: hotspot 없음(RetranslatePanel의 리스트 선택은 그대로 동작 — 기존 경로 유지).
- ConvertService는 로컬 DS 컨테이너 호출 — 외부 비용/네트워크 없음.

## Out of scope

- 편집 모드(OnlyOffice editor) 및 원본 패널 변경.
- `render_html`/`/preview`/`/view` 엔드포인트 제거(다른 소비자 유지; 번역본 패널에서의 사용만 대체).
- 편집기 스크롤 위치 ↔ 페이지 뷰 스크롤 동기화.
- 타이핑 중 실시간 반영(저장 시점 스냅샷 — 기존 forcesave 게이트 흐름 그대로).

## Verification

- 단위: ds_convert(요청 모양·token·신선도·에러 매핑, httpx 모킹), pdf_map(합성 PDF로 렌더/매니페스트; 순수 정렬 함수로 정확 매칭·노이즈 스킵·미스·다중 페이지·정규화; find_terms), 라우트(TestClient + ensure_pdf 몽키패치).
- dev-local 스킵 가능 통합 테스트: DS 기동 시 실제 ladder 문서로 `/tpages` 200 + 페이지 수 > 0 + tsegments 매칭률 ≥ 70%.
- 프런트: `npm run build` + 브라우저 dogfood — 편집 모드 페이지와 용어/재번역 모드 페이지 시각 대조(스크린샷), 세그먼트 클릭·재번역 적용·pn 하이라이트 동작 확인.
- 전체 스위트(기존 15개 protected-file 실패 제외).
