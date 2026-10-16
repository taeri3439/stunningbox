# 뷰어 공간 효율 정비 — 설계 (Design Spec)

**Date:** 2026-06-15
**Branch:** `viewer-layout-efficiency-2026-06-15`
**Scope:** Frontend 중심 — `frontend/src/app/c/[id]/page.tsx`, 뷰어 컴포넌트, `globals.css`. A·B·C는 프론트 전용. D(OnlyOffice 줌)만 검증 후 백엔드 config 한 필드 추가가 필요할 수 있는 예외이며, 동작 안 하면 D는 제외한다.

## 목표 (Goal)

Step 3 번역 뷰어(`/c/[id]`)의 화면 공간을 더 효율적으로 쓴다. 원본·번역본(OnlyOffice)을
동시에 비교하면서, 재번역·용어 관리 도구를 한 화면 안에서 효율적으로 띄울 수 있게 한다.

## 결정된 방향 (확정)

브레인스토밍에서 사용자가 선택:

1. **주 화면:** 노트북·외장 모니터 둘 다 → 반응형으로 둘 다 최적화.
2. **최적화 우선:** 원본·번역 동시 비교가 핵심 → 50/50 기본 유지 + **비율 조절 핸들** 추가.
3. **도구 패널:** **통합 도구 독(탭 전환)** + **폭 조절** 가능.

## 현재 상태 (As-is) — 낭비 지점

1440px 노트북에서 실제 구동 확인. (참고 스크린샷: `viewer-edit-mode.png`, `viewer-retranslate-mode.png`)

- **세로:** 마스트헤드(로고+단계+제목)와 액션바가 별개의 두 줄 → 본문 시작 전 ~110px 소비.
- **세로:** OnlyOffice 상단 탭줄(파일/홈…)을 가린 불투명 32px 띠 = 늘 빈 밴드.
- **세로:** 원본 패널 `pt-6` + `"원본 — 한국어"` h2 + 페이지마다 `— 1 —` 캡션.
- **가로:** OnlyOffice 가운데 패널의 회색 "책상" 여백 — 문서 페이지는 물리 크기 고정이라
  칼럼이 넓으면 페이지 좌우로 회색만 늘어남.
- **가로:** 고정 50/50 분할 — 비율 조절 불가.
- **가로:** 재번역/용어 모드에서 `원본 + 편집기 + 400px 패널`이 동시에 떠 셋 다 좁아짐(3단 크램핑).
  추가로 용어 모드는 가운데를 OnlyOffice → TranslatedPreview로 통째 교체 → 모드 전환 시 큰 레이아웃 점프.

## 설계 (To-be)

전체 구조는 위→아래로 **① 단일 상단 바 → ② 리사이즈 가능한 스프레드(원본 │ 메인 │ 도구 독)**.

```
┌──────────────────────────────────────────────────────────────────────┐
│ ① 상단 바 (1줄): 로고·검토·편집·제목 · 한국어→EN · 누락0·반영률   [재번역][용어] 자동저장· 테마·계정·새문서·DOCX내보내기 │
├───────────────┬───────────────────────────┬───────────────────────────┤
│ 원본 (PDF/HWP) │‖ 메인: OnlyOffice 편집기    │‖┌재번역┬용어 관리┐         │
│  [◀ 접기]      │‖   (용어 탭 → 하이라이트     │‖│  탭 콘텐츠         │ [✕] │
│               │‖    프리뷰로 교체)          │‖│                    │     │
│               │‖                           │‖└────────────────────┘     │
└───────────────┴───────────────────────────┴───────────────────────────┘
   리사이즈 핸들 ‖           리사이즈 핸들 ‖          (도구 독은 탭 닫기 시 사라짐)
```

### A. 헤더 압축 — 마스트헤드 + 액션바 → 단일 상단 바

현재 `c/[id]/page.tsx`의 `<header>`(116–141행)와 액션바 `<div>`(143–173행)를 **하나의 바**로 합친다.

- **왼쪽 그룹:** 작은 로고(`Logo size={22}`) · `eyebrow` "검토·편집" · `slug` 제목(말줄임, `truncate`) ·
  언어쌍 칩 `한국어 → English` · 상태 `stamp`(`누락 0` 또는 `확인 필요 · 누락 N`) · `label` 반영률.
- **오른쪽 그룹:** `[재번역]` `[용어]` 토글 버튼 · "편집 내용 자동 저장" `slug`(sm 이상) · `ThemeToggle` ·
  `AuthMenu` · "새 문서" · **"DOCX 내보내기"**(`btn-primary`).
- 좁은 화면에선 `flex-wrap`으로 자연 줄바꿈(2줄 허용), 넓은 화면에선 1줄.
- 세로 패딩 축소(`py-2.5` 수준 1줄). 기대 효과: 본문이 ~58px 위로.
- `reveal reveal-1/2` 진입 애니메이션은 단일 바 하나로 합쳐 유지.

### B. 좌우 비율 조절 + 패널 접기

스프레드(176–295행)를 **CSS Grid 3컬럼**으로 재구성: `grid-template-columns: <원본fr> <handle> <메인fr> [<handle> <독px>]`.

- **리사이즈 핸들 컴포넌트** (신규 `ResizeHandle.tsx`, `"use client"`):
  - 현재 정적 `.gutter`(1px divider) 자리를 드래그 가능한 핸들로 교체(폭 ~6px, hover 시 `--primary` 힌트, `cursor: col-resize`).
  - pointer 이벤트(`pointerdown`/`pointermove`/`pointerup` + `setPointerCapture`)로 좌우 컬럼 비율 조절.
  - 새 npm 의존성 없음(라이브러리 미설치 확인). 순수 React + pointer 이벤트.
  - 비율을 **localStorage**에 저장(키: `sb.viewer.split.main`, `sb.viewer.dock.w`). 다음 방문 시 복원.
  - 최소/최대 클램프(예: 원본·메인 각 25%~75%; 독 280px~560px).
- **패널 접기:** 원본 패널 헤더에 접기 버튼(◀). 접으면 그 컬럼 `0fr`/숨김 → 메인이 전체폭. 다시 펴기(▶).
  기본값은 둘 다 표시(동시 비교 우선).
- **원본 chrome 다이어트:** `pt-6` 축소, `"원본 — 한국어"`는 얇은 패널 헤더 한 줄로, `figcaption`의 `— 1 —`는
  제거하거나 `--fs-caption`보다 작게/연하게.
- 반응형: `lg` 미만(좁은 화면)에서는 현재처럼 세로 스택(`flex-col`)으로 폴백 — 리사이즈는 `lg` 이상에서만 활성.

### C. 통합 도구 독 (탭 + 폭 조절)

현재 `mode: "edit" | "retranslate" | "manage"` 상태를 유지하되, 오른쪽을 **단일 도구 독**으로 통합.

- 도구 독 헤더에 탭 `[재번역]` `[용어 관리]` + 닫기(✕). 탭 클릭 시 `mode` 전환, ✕ 시 `mode="edit"`(독 사라짐, 원본/메인 전체폭).
- `mode === "edit"`: 도구 독 없음(현행과 동일).
- `mode === "retranslate" | "manage"`: 도구 독 표시, 폭은 드래그 조절(B의 핸들 재사용).
- **메인(가운데) 동작 — 결정 포인트(확정):** 현행 동작 유지.
  - `edit` / `retranslate`: 메인 = `OnlyOfficeEditor`.
  - `manage`: 메인 = `TranslatedPreview`(용어 적용/누락 색 하이라이트는 OnlyOffice cross-origin iframe 안에서
    그릴 수 없으므로 하이라이트 프리뷰 HTML이 필요). **단, 원본 패널과 도구 독은 그대로 유지**되어
    탭 전환 시 메인 한 칸만 바뀐다 → 레이아웃 점프 최소화(현재는 3단 구조 자체가 재배치됨).
- 신규 컴포넌트는 만들지 않고, 기존 `RetranslatePanel` / `GlossaryManagePanel`을 도구 독 탭 콘텐츠로 배치.
  탭 칩 UI는 기존 토글 버튼 패턴(`btn-primary`/`btn-secondary`)을 재사용.

### D. OnlyOffice 회색 "책상" 여백 — best-effort

- `OnlyOfficeEditor`가 받는 `config.editorConfig.customization`에 **기본 줌 "페이지 폭 맞춤"**을 설정해
  문서가 칼럼 폭을 채우게 한다(좌우 회색 여백 최소화).
- **제약/검증 필요:** 편집기 내부는 cross-origin iframe이라 우리 CSS로 못 건드린다 → config 옵션으로만 가능.
  이 줌 옵션이 현재 DS 9.4.0 빌드에서 실제로 먹히는지 **구현 단계에서 검증**하고, 동작 안 하면 D 항목만 제외(나머지 A·B·C는 독립).
  config는 백엔드(`backend/onlyoffice.py`)가 서명해 내려주므로, 이 한 항목은 예외적으로 백엔드 config 필드 추가가 필요할 수 있음 — 검증 후 결정.
- 가린 32px 탭 띠는 현행 유지(빌드 버그로 `toolbarNoTabs`가 깨져 있어 마스킹 중).

## 컴포넌트/파일 영향

- `frontend/src/app/c/[id]/page.tsx` — 헤더 통합, 그리드 스프레드, 리사이즈 상태/localStorage, 도구 독 탭.
- `frontend/src/components/ResizeHandle.tsx` — **신규**. 드래그 핸들(좌우 컬럼/독 폭 공용).
- `frontend/src/components/RetranslatePanel.tsx` / `GlossaryManagePanel.tsx` — 도구 독 탭 콘텐츠로 들어가도록
  최상위 래퍼만 소폭 조정(자체 스크롤 유지). 기능 변경 없음.
- `frontend/src/components/OnlyOfficeEditor.tsx` — (D 검증 통과 시) editorConfig 줌 전달.
- `frontend/src/app/globals.css` — `.gutter`를 리사이즈 핸들 스타일로 확장(또는 신규 `.resize-handle`), 상단 바 정렬용 소량 유틸.

## 데이터 흐름 / 상태

- 기존 상태(`mode`, `activeSeg`, `connector`, `editorReloadKey`, `editorBookmark`, `manageKey`, `jumpBlock`,
  `glossaryReport`)는 그대로. 리사이즈는 **순수 UI 상태**(컬럼 비율/독 폭)만 추가하며 데이터 흐름에 영향 없음.
- localStorage 읽기는 클라이언트에서만(이미 `"use client"`), SSR 하이드레이션 불일치를 피하려고
  초기값은 기본 비율로 렌더 후 `useEffect`에서 저장값 적용.

## 에러 처리 / 엣지 케이스

- localStorage 접근 불가(프라이빗 모드 등) → try/catch로 무시, 기본 비율 사용.
- 저장된 비율이 클램프 범위 밖 → 클램프.
- HWP 경로(`source_html` iframe)·PDF 경로(page_images) 둘 다 원본 패널 접기/리사이즈가 동일하게 동작.
- 좁은 화면(`lg` 미만): 세로 스택 폴백, 리사이즈 핸들 비활성(드래그 불가).

## 검증 (Manual)

- `cd frontend && npm run lint && npm run build` 통과.
- `BACKEND_URL=http://127.0.0.1:8001` + `next start`로 구동, 실제 문서로 확인(테스트 문서 `a2i8TOoVVNPHRs7caCqinybs`).
- 라이트/다크 둘 다, 노트북(1440)·넓은 화면 둘 다에서:
  - 상단 바 1줄(넓은 화면)·2줄(좁은 화면) 줄바꿈.
  - 원본│메인 핸들 드래그 → 비율 변함, 새로고침 후 복원.
  - 원본 접기 → 메인 전체폭, 펴기 복원.
  - `[재번역]`/`[용어]` 탭 전환 시 메인 한 칸만 바뀌고 원본·독 위치 유지. 독 폭 드래그·복원.
  - `[✕]`로 독 닫으면 `edit`로 돌아가 원본/메인 전체폭.
  - (D) OnlyOffice 기본 줌이 페이지 폭 맞춤인지 — 안 되면 D 제외했음을 보고.

## 비범위 (Out of scope / YAGNI)

- OnlyOffice 편집기 내부 테마/툴바 재구성(cross-origin 불가).
- 백엔드 렌더/모델 변경(D의 config 한 필드는 검증 후 예외 검토).
- 새 번역/용어 기능 추가(이번은 레이아웃·공간 효율만).
- 모바일 전용 레이아웃(반응형 폴백으로 충분).
