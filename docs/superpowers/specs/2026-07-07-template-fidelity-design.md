# Template fidelity — 고정 양식(디자인 리포트) 99% 유사도 — Design

**Date:** 2026-07-07  **Status:** Approved  **Branch:** `template-fidelity-2026-07-07` (from main)
**Evidence doc:** vet-market 리포트 `L8QPjeG06JIktCo1drj9vjjn` (EN→KO, 20p; 실측 ~90-93%)

## Problems (실측 오류 클래스)

1. **페이지 경계 시프트**: 원본 20p → 번역 21p, 내용이 페이지마다 밀림. 누적 드리프트라 뒤로 갈수록 원본 페이지와 대응이 깨진다.
2. **의도적 수직 여백 소실**: Disclaimer 페이지의 상단 절반 여백, 차트 페이지 하단 여백이 리플로우로 당겨진다 (커버에만 있는 rhythm 로직이 본문 페이지에는 없음).
3. **캡션-도표 고아화**: FIGURE 캡션이 페이지 끝에 고립되고 도표는 다음 페이지.
4. **폰트 클래스 불일치 + TOC 계층 평탄화**: 라틴 전용 소스 폰트(FranklinGothic 등)가 `w:eastAsia`에 그대로 들어가 한글이 기본 세리프로 폴백 — 산세리프 리포트가 세리프 한글로 렌더됨. TOC 들여쓰기 계층("12.2" vs "12.2.1")이 사라짐.

## History guard (2026-06-15 e7b3fbc의 교훈)

과거 "소스 페이지마다 하드 브레이크"는 두 가지 이유로 제거됐다: (a) 빈 문단에 `WD_BREAK.PAGE`를 넣는 방식이라 브레이크 문단 자체가 공백/블랭크 페이지를 만들었고, (b) ko→en은 번역문이 ~1.5배 팽창해 페이지가 넘치면 어차피 desync. 이번 설계는 (a)를 문단 속성 `page_break_before`(빈 문단 없음)로, (b)를 **방향 게이트**(축소 방향에서만 활성)로 해결한다. 앵커의 핵심 성질: 페이지가 넘쳐도 다음 앵커에서 재동기화되므로 시프트가 **국소적**이 된다(현재는 누적).

## Approach

### A. 페이지 패리티 앵커링 (방향 게이트)

- `SemanticDocument.page_parity: Optional[bool] = None` 필드 추가 (None=자동, True/False=강제; 장래 양식 프로파일용).
- 렌더 시 `_page_parity_on(doc)` = `doc.page_parity`가 명시돼 있으면 그 값; None이면 **target_lang ∈ {ko, ja, zh}** (라틴→CJK는 텍스트가 축소되는 방향이라 안전). ko→en(팽창)은 자동 OFF → 기존 KFS 출력 무변경.
- ON일 때 docx_renderer는 소스 페이지가 바뀌는 첫 블록에 `paragraph_format.page_break_before = True`를 설정한다:
  - 빈 문단 삽입 금지 (과거 bloat의 원인).
  - 첫 블록이 `TableBlock`이면 브레이크를 실을 문단이 없으므로 1pt/공백0 스페이서 문단에 `page_break_before`를 준다 (유일한 예외 삽입).
  - 스킵: 문서 첫 페이지; 커버 직후 첫 본문 페이지(커버 브레이크가 이미 새 페이지를 보장); 블록이 없는 소스 페이지(자연히 브레이크 없음 — 페이지 통째 소실 페이지는 앵커도 없음).
- HTML 렌더러 무변경 (연속 스크롤 유지).

### B. 본문 페이지 수직 리듬 (측정은 항상, 적용은 패리티 모드에서만)

- reconciler: `_apply_cover_rhythm`과 동일한 측정(이전 블록 bottom = 스팬 max y1, 첫 블록은 페이지 top margin부터, clamp [0,300]pt, ≤6pt 스킵)을 **희소 본문 페이지**로 일반화.
  - 페이지 게이트: 해당 페이지의 로컬 텍스트 블록 수 ≤ 8 **그리고** 페이지에 TableBlock 없음(표 높이는 재현 높이가 달라 측정 무의미) **그리고** (첫 블록 top-gap ≥ 40pt 또는 블록 간 gap ≥ 40pt 존재) — "듬성한 디자인 페이지"만.
  - 저장 필드는 기존 `space_before`가 아니라 **새 필드 `page_space_before: Optional[float]`** (커버 rhythm과 분리 — 기존 문서/커버 동작 무변경).
- docx_renderer: 패리티 모드일 때만, `block.space_before`가 없으면 `page_space_before`를 space_before로 적용. 패리티 OFF 문서(ko→en)는 필드가 있어도 무시 → 무변경.

### C. 캡션 keep-with-next (무조건)

- `CaptionBlock` 문단에 `paragraph_format.keep_with_next = True`. 캡션은 정의상 다음 도표/표에 붙는 라벨이므로 방향 무관하게 보편 타당. (표 위 imgtable 캡션도 CaptionBlock이므로 함께 해결.)

### D. 폰트 클래스 매핑 + TOC 들여쓰기

- `_set_run_font(run, name)`: `name`이 **CJK 글리프가 없는 라틴 폰트**(휴리스틱: 이름이 ASCII로만 구성 && 한글 폰트명 아님)일 때 `w:eastAsia`만 클래스별 대체로 스왑 — sans 패턴(`gothic|arial|helvetica|franklin|verdana|tahoma|segoe|calibri|noto sans|malgun`) → `Malgun Gothic`, 그 외 → `Batang`. `w:ascii/w:hAnsi/w:cs`는 소스 이름 유지(라틴 텍스트 폴백은 워드/DS에 위임). 한글 폰트명(바탕/맑은 고딕 등)은 현행대로 4속성 동일 — KFS 무변경.
- TOC 계층: `_render_toc` 경로에서 마커의 점-구분 숫자 깊이(`12.`=0, `12.2`=1, `12.2.1`=2, cap 4)에 비례해 `left_indent = 0.18" × depth` 적용. 비-TOC 리스트는 무변경.

## Out of scope

- 커버에 구워진 그래픽 텍스트 번역(스팬 없음 — 구조적 불가), 폰트 완전 일치(대체 폰트 메트릭), FIGURE N 밑줄 등 캡션 마이크로 스타일, ko→en 방향의 패리티(자동 OFF; 필요 시 `page_parity=True` 강제로 옵트인).

## Verification

- 단위(TDD): 앵커(2페이지 문서 docx 읽어 `page_break_before` 확인; 표-첫 페이지 스페이서; 커버 직후 스킵; ko→en OFF), 리듬(게이트 통과/차단 픽스처, 측정값, 패리티 OFF 시 미적용), keep_with_next XML, 폰트 스왑(라틴 sans/serif/한글 3케이스), TOC indent 깊이.
- dev-local 통합(skippable): vet 픽스처 `rebuild_source_model` + 번역본 재렌더 → **페이지 수 20=20** + 페이지별 첫 블록 텍스트가 원본 페이지 첫 스팬과 대응 ≥ 90%; ladder(ko→en) 회귀 — 패리티 OFF로 페이지 수/출력 무변경.
- 브라우저 before/after: vet 문서 재렌더 후 원본 vs 번역 페이지 나란히 (Disclaimer 여백, TOC 계층, 산세리프 한글, FIGURE 캡션 부착).
- 전체 스위트 (기존 15개 protected-file 실패 제외).
