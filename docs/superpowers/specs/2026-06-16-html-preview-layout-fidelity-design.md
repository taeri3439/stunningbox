# HTML 미리보기 레이아웃 충실도 — 설계 (Design Spec)

**Date:** 2026-06-16
**Branch:** `html-preview-layout-2026-06-16`
**Scope:** Backend only — `backend/renderers/html_renderer.py` (+ tests). 모델·엔드포인트·프론트엔드 변경 없음.

## 목표 (Goal)

용어 관리 모드의 번역본 HTML 미리보기(`/api/documents/{id}/preview`, `TranslatedPreview` iframe)가
원본 문서의 형식에 가깝게 보이도록, **DOCX 렌더러가 각 블록 필드로 하는 일을 `html_renderer`에서
CSS로 동일하게 재현**한다(=DOCX 패리티). 픽셀 단위 절대배치가 아니라 DOCX와 같은 "서식 기반 흐름".

## 배경 / 현재 상태

조사 결과(워크플로 `understand-html-preview-layout`):

- **모델은 서식 데이터를 이미 충분히 보유** — 각 블록에 `font_size·bold·italic·underline·color·align·indent·line_spacing`,
  표 `col_widths`·셀 `align/valign/shading/vertical`, 그림 `width/height/align/wrap`, 구분선 `thickness/color`,
  문서 `page_width/height·margin_*`, `running_header/footer`(RunningBand).
- **`docx_renderer.py`는 이 필드들을 전부 사용**해 원본과 유사한 .docx를 만든다(OnlyOffice/내보내기 출력).
- **`html_renderer.py`는 거의 다 무시** — 텍스트 + 구조(heading/list/table/figure) + 소스 페이지 변화 시 page-break div만
  사용. 그래서 "텍스트를 흐름대로 나열"처럼 보인다. `DividerBlock`은 아예 처리 안 됨(조용히 사라짐).

핵심: 좌표(bbox)가 없어서가 아니라 **있는 서식 필드를 HTML이 안 쓰기 때문**. DOCX도 좌표가 아닌 서식 기반
흐름 문서이므로, **DOCX와 동일한 필드 사용 = 원본 형식에 근접**.

## 핵심 결정 (확정)

- 접근법 **A** (DOCX 수준 서식을 CSS로 적용). B(bbox 절대배치)/C(DOCX 변환 표시)는 기각.
- 범위 **전체 DOCX 패리티**.
- 단위: **CSS `pt`를 그대로 사용**(1pt=1/72in). docx의 pt 기반 지오메트리/스케일을 그대로 옮겨 물리 비율 일치.
  (`page_width`, `margin_*`, `indent`, 스케일된 `font_size`, 그림 `width`, 구분선 `thickness` 모두 pt.)

## 설계 (To-be)

`render_html(doc, asset_url_prefix, highlight_pattern, anchor_blocks)` 시그니처·반환(HTML 문자열)·용어
`<mark class="gloss">` 주입·`blk{i}` 앵커는 **그대로 유지**. 내부에서 아래를 추가/변경한다.

### 1. 종이 시트 (page geometry)

- 본문 컨테이너를 `page_width`(pt) 폭의 시트로: `.page{ width:{page_width}pt; padding:{margin_top}pt {margin_right}pt {margin_bottom}pt {margin_left}pt; margin:0 auto; background:#fff; }`.
- `page_width`/여백이 없으면(레거시 source.json) 현행 폴백: `max-width:820px; padding:0 1rem`.
- 콘텐츠 폭(`content_pt = page_width - margin_left - margin_right`)은 표/그림 폭 계산의 기준.

### 2. 블록 타이포그래피 — `_inline_style(block, body_pt)` 헬퍼 (docx `_apply_style` 이식)

heading/paragraph/list_item/caption에 inline `style`로 적용:

- **font-size**: docx의 `_scaled_pt(size, body_pt) = max(8, min(30, round(11 * size / body_pt)))` 를 그대로 재사용
  → `font-size:{scaled}pt`. `body_pt`는 문서의 지배적 본문 크기(아래 4).
- **font-weight**: `bold is True`→`700`, `bold is False`→`400`(미지정은 생략).
- **font-style/text-decoration**: `italic`→`italic`, `underline`→`underline`.
- **color**: `color`(hex)→`#{color}`, 없으면 ink `#1a1a1a`(현행 본문색).
- **text-align**: `align`∈{center,right,justify} 그대로; None(=left)은 생략.
- **margin-left(indent)**: `indent`(pt)가 있으면 `margin-left:{indent}pt`. list_item은 `indent` 우선, 없으면 현행 `level*1.5em`.
- **line-height**: `line_spacing`(있으면)을 `min(line_spacing, 1.4)`로 캡(번역문 bloat 방지, docx의 MAX_LINE_PITCH=1.4 의도),
  하한 1.15 → `line-height:{ls}`. 없으면 스타일시트 기본 1.5.
- `font`(가족)는 폰트 가용성/CJK 폴백 이슈로 **미적용**(스타일시트의 시스템 스택 유지) — docx는 eastAsia 폴백을 쓰지만
  HTML 미리보기에선 시스템 폰트가 더 안전. (의도적 제외; 색/크기/굵기가 형식 인지에 더 큼.)

### 3. 표 (`_render_table` 확장)

- **열 폭**: `col_widths`(fractions)가 있고 "끼임"이 아니면 `<colgroup><col style="width:{f*content_pt}pt">…`+`table-layout:fixed`.
  - "끼임" 판정은 docx의 `_widths_would_cram`(PR #22에서 도입)을 이식/공유: 어떤 열이 그 열의 가장 긴 분절-불가
    Latin run보다 좁으면 고정폭 포기하고 `table-layout:auto`(현행) 폴백. CJK는 끼임 없음.
  - `col_widths` 없으면 현행(균등 분배).
- **셀**: 기존 rowspan/colspan/`th`(is_header) 유지 + inline style로
  `text-align`(cell.align), `vertical-align`(valign: top/middle/bottom — center→middle), 배경(`shading`→`background:#{shading}`), 색(color).
- **세로 글자**(`cell.vertical`): `writing-mode:vertical-rl;` (+필요 시 `text-orientation:upright` 또는 단순 회전). docx는 btLr.

### 4. 지배적 본문 크기 `body_pt`

- 문서 블록들의 `font_size` 중 **가장 빈도 높은(또는 중앙값)** 크기를 본문 기준으로 산출(없으면 폴백 11.0).
- docx와 동일 개념(상대 크기 계층 보존). 한 번 계산해 모든 `_scaled_pt` 호출에 전달.

### 5. 그림 (FigureBlock)

- `<img>`에 `width`(pt)가 있으면 `style="width:{min(width, content_pt)}pt; height:auto"` (원본 크기, 컨테이너 한도). 없으면 현행 `max-width:100%`.
- `align`: figure에 `text-align`(center/right) 또는 `margin:auto`(center).
- `wrap`(left/right): `figure{ float:{wrap}; margin:…; max-width:50% }` 로 옆 텍스트가 흐르게(docx의 square wrap 근사). float clear는 다음 블록에서 처리.

### 6. 구분선 `DividerBlock` (신규 처리)

- 현재 미처리 → 추가: `<hr style="border:0; border-top:{thickness or 0.75}pt solid #{color or '1a1a1a'}; margin:.6em 0">`.

### 7. 러닝 헤더/푸터 (RunningBand)

- `running_header`/`running_footer`가 있으면 좌/중/우 3칸 밴드로 렌더:
  `<div class="band"><span>{left}</span><span>{center}</span><span>{right}</span></div>` (flex space-between),
  `rule`면 헤더는 아래 테두리·푸터는 위 테두리(`rule_size`/`rule_color`).
- **반복**: 소스 페이지 경계(=현행 page-break 지점)마다 "이전 페이지 푸터 → 페이지 간격 → 다음 페이지 헤더"로 반복 렌더해
  원본의 페이지 느낌을 재현. 문서 첫 머리에 헤더, 끝에 푸터.
- 텍스트가 비어 rule만 있는 밴드도 허용(가는 선).

### 8. 페이지 구분 표시

- 기존 `data-page-break` 빈 div를 **보이는 페이지 간격**으로: 위/아래 여백 + 옅은 점선/배경 갭(페이지 사이가 눈에 보이게).
  러닝밴드(7)가 있으면 그 간격 안에 푸터/헤더를 끼움.

## 스타일시트 변경 (`<style>` 블록)

- `body` 배경을 페이지 바깥 색(예: `--surface-sunken` 유사한 옅은 회색)으로, `.page`는 흰 시트(그림자 옵션)로 — 종이 위 문서 느낌.
- `.band`(러닝밴드), `.page-gap`(페이지 간격), `td/th` 기본은 유지하되 inline style이 우선.
- 다크모드: 미리보기 iframe은 현재도 흰 시트(`bg-white`) 기준이므로 시트는 흰색 유지(프론트 결정과 일관). 본문색 ink.

## 컴포넌트/파일 영향

- `backend/renderers/html_renderer.py` — 위 1–8 전부. 헬퍼: `_inline_style`, `_dominant_body_pt`, 표/그림/밴드/구분선 처리.
  가능하면 docx와 공유 가능한 순수 함수(`_scaled_pt`, `_widths_would_cram`)는 **공용 모듈로 추출**해 두 렌더러가 공유
  (중복 방지). 추출이 과하면 html_renderer 내 복제로 시작하되 동일 로직 보장.
- `backend/renderers/docx_renderer.py` — (공용 추출을 택할 경우에만) `_scaled_pt`/`_widths_would_cram` import 경로 변경. 동작 불변.
- `backend/tests/test_html_renderer.py`(있으면 확장, 없으면 신규) — 아래 검증.
- **모델/엔드포인트/프론트엔드 변경 없음.**

## 데이터 흐름

- 미리보기 엔드포인트(`GET /api/documents/{id}/preview`)는 그대로 `render_html(doc, …, highlight_pattern, anchor_blocks=True)` 호출.
  `doc`(SemanticDocument)에 이미 모든 필드가 있으므로 추가 로드 없음. 용어 하이라이트·`blk{i}` 앵커 그대로.

## 에러 처리 / 엣지 케이스

- `page_width`/여백·`font_size`·`col_widths`·`line_spacing` 등은 모두 Optional → **없으면 현행 동작으로 폴백**(레거시 source.json 안전).
- 번역문 길이 증가 → reflow로 세로 확장(넘침 없음). 표 고정폭만 끼임 위험 → `_widths_would_cram` 폴백.
- `body_pt` 산출 불가(모든 font_size None) → 11.0 폴백(스케일 미적용과 동등).
- 그림 float 후 다음 블록이 겹치지 않도록 적절한 `clear`/overflow 처리.

## 검증 (Tests)

`backend` pytest 확장(`test_html_renderer.py`):

- 블록 style 방출: align→`text-align`, 스케일된 `font-size:..pt`, `indent`→`margin-left:..pt`, `line_spacing`→`line-height`(캡 확인), bold/italic/underline/color.
- 표: `col_widths`→`<colgroup>`+`table-layout:fixed`; 끼임 케이스→autofit 폴백; 셀 shading/align/valign/세로글자.
- 그림: `width`→`width:..pt`, `align`, `wrap`→`float`.
- `DividerBlock`→`<hr>` 렌더(현재 누락 회귀 테스트).
- RunningBand→밴드 렌더 + 페이지 경계 반복.
- 종이 시트: `page_width`→`.page` width; 지오메트리 없으면 폴백.
- **회귀:** 용어 `<mark class="gloss">` 주입 유지, `blk{i}` 앵커 유지, page-break 표시 유지.
- 전체 백엔드 스위트 무회귀.
- 실제 앱 시각 확인: 로컬에서 문서 업로드/기존 문서 → 용어 관리 모드 → 미리보기가 DOCX/원본과 유사한지(정렬·들여쓰기·표 비율·그림·구분선·종이 폭) 좌측 원본과 대조.

## 비범위 (Out of scope / YAGNI)

- 픽셀 단위 절대배치(bbox 좌표 재현) — 번역문 길이로 겹침, 범위 외.
- 진짜 페이지네이션(고정 높이 페이지로 콘텐츠 분할) — CSS paged media 필요, 범위 외. (러닝밴드 반복은 소스 페이지 경계 기준 근사.)
- `font`(가족) 적용 — 폰트 가용성/CJK 폴백 리스크로 의도적 제외.
- 프론트엔드/엔드포인트/모델 변경.
