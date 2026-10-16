# 용어 일괄변경 (Bulk Term Replace in Viewer) — 설계

작성일: 2026-06-03 · 브랜치(예정): `bulktermreplace-2026-06-03`
(의존성: [용어집 기반 번역](2026-06-03-glossary-based-translation-design.md) — 같은 per-doc 저장/재번역 배선 위에 쌓는다. 아래 "브랜치/의존성" 참고.)

## 배경 / 목적

번역이 끝난 뒤, 번역 결과(OnlyOffice 뷰어)에서 **특정 단어/구를 문서 전체에 걸쳐 일괄로 바꾸고
싶다**. 용어집은 번역 *전*에 source→target 매핑을 주입하지만, 번역 *후*에 "이 용어를 전부
이렇게 바꿔달라"는 교정 요구는 현재 충족되지 않는다. (OnlyOffice 내장 Ctrl+H는 한 번에 한 단어,
수동, 용어집/카운트 미리보기 연동 없음. 프로그램적 `SearchAndReplace` 커넥터는 Developer
Edition 전용 — 우리 CE에선 `createConnector`가 null로 확인됨.)

현재 재번역 "적용"은 백엔드 모델 갱신 → `.docx` 재생성/직접편집 → 에디터 reload(`reloadKey` +
bookmark)로 동작한다([RetranslatePanel.tsx](../../../frontend/src/components/RetranslatePanel.tsx),
[c/[id]/page.tsx](../../../frontend/src/app/c/[id]/page.tsx)). 본 기능은 이 결정적 백엔드 경로를
재사용한다.

## 사용자 결정 사항 (확정)

- **기능 형태**: 여러 "찾을말 → 바꿀말" **행을 패널에 추가해 한 번에 적용**(단일 단어 교체도 행 1개로 가능).
- **용어집 저장**: **교체마다 선택** — 각 행에 "저장" 체크박스(기본 ON). 저장하면 이후 특정 문장 재번역 시에도 새 용어 유지.
- **일치(매칭)**: **스마트** 기본 — 대소문자 무시; 띄어쓰기 쓰는 언어(en/es/fr/de/vi)는 단어 단위, CJK(ko/ja/zh)는 부분 일치. **행마다 토글**로 변경.
- **UI 배치**: 액션바에 **세 번째 모드 "용어 일괄변경"** 토글 추가 → 재번역 모드처럼 **편집기를 덮지 않는 우측 패널**.

## 적용 방식 — `.docx` 직접 편집 + 모델 미러 (채택)

백엔드가 현재 `output.{lang}.docx`(편집 후 source of truth)를 열어 **run-aware** 찾아바꾸기를
수행하고 저장한다. 동일 교체를 `translated/edited.{lang}.json`(모델)에도 미러해 재번역 패널·후속
재생성과 정합을 유지한다. 그 뒤 에디터를 reload한다.

- **장점**: OnlyOffice에서 사용자가 직접 한 수정이 **보존**된다(살아있는 `.docx` 위에서 교체). 결정적, CE에서 동작, reload 배선 재사용, LLM 비용 0.
- **대안(미채택)**: 모델만 바꾸고 `.docx` 재생성 → 단순하지만 **수동 편집을 날림**(재생성은 모델에서 재구성). 검토 막바지의 교정 도구로는 부적합.

## ⚠️ "용어집 저장"의 의미 (중요)

업로드 용어집은 **source→target**(`정격하중 → Rated Load`)이고 번역 *중* **source** 항목 매칭으로
적용된다. 본 일괄변경은 **target 텍스트**(예: `Rated Load → Load Rating`, 둘 다 영어)를 대상으로
한다. 영어→영어 쌍을 source→target 용어집에 넣는 것은 의미가 맞지 않는다.

따라서 "용어집 저장"은 내부적으로 **번역 후 교체 규칙(target-side replacement)**을 문서별로
별도 저장하며, 업로드 용어집(`glossary.json`)과는 **구분된** 저장소
(`storage/{doc_id}/replacements.{lang}.json`)를 쓴다. 이 규칙들은 **이후 재번역·재생성 후 자동으로
재적용**되어 "새 용어가 계속 유지"되게 한다. 사용자에겐 "용어집에 저장 → 계속 유지"로 읽히고,
내부적으론 target 교체 목록이다.

## 비목표 (v1)

- source→target 업로드 용어집에 대한 *직접* 추가/편집(본 기능은 target 교체 규칙만 저장).
- 정규식(regex) 사용자 입력(스마트/정확/느슨 3가지 모드만; 내부 구현은 regex여도 UI엔 노출 안 함).
- 실행취소(undo) — 잘못 바꾸면 다시 반대 규칙으로 바꾸거나 OnlyOffice undo 사용.
- 교체 위치별 개별 선택(전체 일괄만; 미리보기는 개수만, 토글식 선택 적용은 후순위).
- 전역(모든 문서 공용) 교체 규칙.

## 아키텍처 / 흐름

```
[뷰어 c/[id]/page.tsx]  mode: 'edit' | 'retranslate' | 'replace'
  '용어 일괄변경' 토글 → 우측 BulkReplacePanel
    행들: { find, replace, matchMode('smart'|'exact'|'loose'), save:bool }
    [미리보기] → POST /api/documents/{id}/replace/preview?lang=
        · 살아있는 .docx 텍스트에서 행별 매칭 개수 산출 → 행마다 "N곳" 표시
    [적용]     → POST /api/documents/{id}/replace?lang=   (body: rules[])
        1) output.{lang}.docx 열기 → docx_edit.apply_replacements(doc, rules) → 저장
        2) translated.{lang}.json (+ edited.{lang}.json 있으면) 에 동일 교체 미러
        3) save=true 행 → replacements.{lang}.json 에 누적(merge, find 기준 dedup)
        → 응답 { results: [{find, replace, count}], total }
    → onApplied() → reloadKey 증가 → OnlyOfficeEditor 재초기화 → 바뀐 .docx 로드
[재번역] retranslate_one_segment: 새 번역문 생성 후 저장된 replacements 규칙을 재적용
```

## 매칭 의미 (`text_replace.py`)

행마다 `matchMode`:
- **smart**(기본): `re.IGNORECASE`. target_lang ∈ {en,es,fr,de,vi} 이면 단어 경계
  `(?<![\w])find(?![\w])` 사용(라틴 target이라 안전); CJK(ko/ja/zh) 이면 부분 일치.
- **exact**: 대소문자 구분, 부분 일치(입력 문자열 그대로).
- **loose**: `re.IGNORECASE`, 부분 일치.

규칙은 `re.escape(find)`로 리터럴화(정규식 메타문자 무해화). 여러 행은 **나열 순서대로** 적용
(앞 규칙 결과를 뒤 규칙이 볼 수 있음). `find`가 빈 문자열이거나 `find == replace`인 행은 스킵.
`apply_to_text(text, rules, target_lang) -> (new_text, per_rule_count)`와
`count_in_text(text, pattern) -> int`(컴파일된 패턴 입력)를 제공해 미리보기·적용·모델 미러·재번역
재적용이 동일 로직을 공유한다.

## run-aware `.docx` 편집 (`docx_edit.py`)

`apply_replacements(document, rules) -> dict[find,int]`:
- 본문 문단 + 모든 표 셀 문단(중첩 표 포함) 순회.
- 문단별: 우선 각 run 텍스트에 `apply_to_text` 시도(서식 보존). run 경계를 가로지르는 매칭을 위해,
  문단 전체 텍스트를 합쳐 매칭이 더 있으면 **문단 run들을 단일 run으로 재구성**해 교체
  (첫 run의 서식 유지) — 우리 렌더 .docx는 문단당 run이 비교적 균일하므로 손실 최소.
- 표 셀, 머리말/꼬리말 문단도 포함. 반환은 find별 교체 횟수 합.

> 표준 python-docx의 run 분할 이슈를 다루는 검증된 헬퍼. 테스트로 균일 run/분할 run/표 셀 케이스 고정.

## 백엔드 변경

### `backend/pipeline/text_replace.py` (신규)
- `ReplaceRule` (pydantic): `find: str`, `replace: str`, `match: Literal['smart','exact','loose']='smart'`, `save: bool=True`.
- `compile_rule(rule, target_lang) -> re.Pattern`, `count_in_text(text, pattern) -> int`,
  `apply_to_text(text, rules, target_lang) -> tuple[str, dict[str,int]]`. 순수 함수(부작용 없음).

### `backend/pipeline/docx_edit.py` (신규)
- `apply_replacements(document, rules, target_lang) -> dict[str,int]` (위 run-aware 로직).

### `backend/pipeline/process.py`
- `replacements_path(storage_dir, doc_id, lang)`, `load_replacements(...) -> list[dict]`(없거나 손상 시 `[]`),
  `save_replacements(..., rules)`(`find` 기준 dedup, 나중 것 우선).
- `bulk_replace(storage_dir, doc_id, lang, rules) -> dict`:
  1. `_ensure_docx`로 `.docx` 보장 → `Document(path)` → `docx_edit.apply_replacements` → 저장.
  2. `translated.{lang}.json` 로드 → `iter_text_fields` 각 필드에 `apply_to_text` → 저장. `edited.{lang}.json` 있으면 동일.
  3. `save=true` 규칙을 `save_replacements`로 누적.
  4. 반환 `{results:[{find,replace,count}], total}`.
- `preview_replace(storage_dir, doc_id, lang, rules) -> dict`: `.docx` 텍스트 추출 후 행별 `count_in_text` → `{results:[{find,replace,count}], total}` (디스크 변경 없음).
- `retranslate_one_segment`: 새 번역문 생성 직후 `load_replacements(...)`가 비어있지 않으면
  `apply_to_text(new_text, saved_rules, target_lang)`로 재적용한 값을 저장/반환(저장된 용어 유지).

### `backend/main.py`
- `POST /api/documents/{doc_id}/replace/preview?lang=` — body `{rules: ReplaceRule[]}` → `process.preview_replace`.
- `POST /api/documents/{doc_id}/replace?lang=` — body `{rules: ReplaceRule[]}` → `process.bulk_replace`.
- 빈 `find`/`find==replace` 행은 서버에서도 방어적으로 스킵. 0건이어도 200(프론트가 reload 생략 판단).

## 프론트엔드 변경

### [frontend/src/lib/types.ts](../../../frontend/src/lib/types.ts)
- `ReplaceRule { find; replace; match: 'smart'|'exact'|'loose'; save: boolean }`.
- `ReplaceResult { find; replace; count }`, `ReplaceResponse { results: ReplaceResult[]; total: number }`.

### [frontend/src/lib/api.ts](../../../frontend/src/lib/api.ts)
- `replacePreview(docId, lang, rules) -> ReplaceResponse` (POST …/replace/preview).
- `replaceApply(docId, lang, rules) -> ReplaceResponse` (POST …/replace).

### `frontend/src/components/BulkReplacePanel.tsx` (신규)
- 상태: `rules: ReplaceRule[]`(기본 1행), `preview: ReplaceResponse|null`, `busy`, `note`.
- UI: 행마다 `find`/`replace` 입력 + 매칭 토글(smart/exact/loose) + "저장" 체크박스(기본 ON) + 행 삭제, "+ 행 추가".
- `[미리보기]` → `api.replacePreview` → 행별 "N곳"·합계 표시.
- `[적용]` → `api.replaceApply` → `total>0`이면 `onApplied()`(reload), 결과 토스트("M곳 변경"); `total==0`이면 reload 없이 안내.
- 빈 행 제외 후 전송. 적용 후 미리보기 카운트 초기화.

### [frontend/src/app/c/[id]/page.tsx](../../../frontend/src/app/c/[id]/page.tsx)
- `mode` 유니온에 `'replace'` 추가. 액션바에 "용어 일괄변경" 토글. `mode==='replace'`면 우측 패널에 `BulkReplacePanel` 마운트(재번역 패널과 동일 레이아웃 슬롯, 상호배타).
- `BulkReplacePanel.onApplied` → 기존 `setEditorReloadKey(k=>k+1)` 재사용(bookmark 없이 reload; 첫 변경 위치로의 이동은 후순위).

> 주의: [frontend/AGENTS.md](../../../frontend/AGENTS.md) — Next.js 표준과 다를 수 있으니 코딩 전 `node_modules/next/dist/docs/` 확인. 본 변경은 표준 컴포넌트/fetch라 영향 적음.

## 에러 처리

- 빈 `find` 또는 `find==replace` 행 스킵(프론트+백엔드 양쪽).
- 0건 매칭: 안내만, reload 생략.
- `.docx` 부재: `_ensure_docx`가 먼저 렌더.
- `.docx` 직접 편집으로 mtime/size 변경 → `document_key` 변경 → DS가 재초기화 시 새 파일 로드(정합).
- `replacements.{lang}.json` 손상: `[]`로 강등(재번역 막지 않음).
- run-aware 교체에서 매칭이 run 경계를 가로지르면 문단 run 재구성(해당 문단의 인라인 혼합서식만 평탄화 — 드묾, 허용).

## 검증

백엔드 테스트(`backend/tests/`):
- `test_text_replace.py`: smart(en 단어경계 — "load"가 "download" 미변경; ko 부분일치), exact(대소문자), loose, 다행 순차 적용, 빈/동일 행 스킵, regex 메타문자 리터럴화.
- `test_docx_edit.py`: 균일 run 교체, run 경계 가로지르는 교체(재구성), 표 셀 교체, 카운트 정확.
- `test_bulk_replace.py`: `bulk_replace`가 .docx+translated(+edited) 미러, `save` 규칙만 `replacements.json` 누적·dedup, `preview_replace` 디스크 불변·카운트 정확.
- `test_retranslate_reapply.py`: 저장된 규칙이 `retranslate_one_segment` 출력에 재적용됨.
- `test_replace_api.py`(TestClient, MockTranslator): preview/apply 라우트 + 빈 규칙 방어 + 응답 형태.

프론트: `npm run build` clean. 수동(prod build): 뷰어 → "용어 일괄변경" 토글 → 행 입력 → 미리보기 카운트 → 적용 → 에디터에 반영 확인. (메모리: dev HMR 깨짐, prod build로 검증.)

## 브랜치/의존성

본 기능은 용어집 기능(`load_doc_glossary`, `retranslate_one_segment`, per-doc 저장)을 전제로 한다.
`glossarybasedtranslation2026-06-03` 브랜치가 **아직 미머지**이므로, 실행 전 둘 중 하나로 정리:
(a) 용어집 브랜치를 main에 먼저 머지 → main에서 `bulktermreplace-2026-06-03` 분기(권장, PR 리뷰 깔끔),
(b) 용어집 브랜치 위에 stack(이 PR에 용어집 커밋 포함됨). — 사용자 확인 후 진행.

## 빌드 순서

1. `text_replace.py`(순수 매칭/카운트/적용) + 테스트.
2. `docx_edit.py`(run-aware) + 테스트.
3. `process.py`(`load/save_replacements`, `bulk_replace`, `preview_replace`, `retranslate` 재적용) + 테스트.
4. `main.py`(preview/apply 라우트) + 테스트.
5. 프론트: types → api → `BulkReplacePanel` → `c/[id]/page.tsx` 세 번째 모드.
6. 통합 검증(prod build, 미리보기 → 적용 → 반영).
