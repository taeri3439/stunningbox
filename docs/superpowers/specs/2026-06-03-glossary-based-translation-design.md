# 용어집 기반 번역 (Glossary-Based Translation) — 설계

작성일: 2026-06-03 · 브랜치: `glossarybasedtranslation2026-06-03`

## 배경 / 목적

번역 시작 전에 사용자가 **용어집(CSV)을 업로드**하면, 그 문서의 번역이 해당 용어집을
참고하도록 한다. 현재는 `backend/glossary/{src}-{tgt}.json`(정적 도메인 용어집)만
`ClaudeTranslator` 시스템 프롬프트에 "Use this glossary strictly"로 주입된다
([translator.py](../../../backend/pipeline/translator.py) `load_glossary`). 즉 "번역이 용어집을
참고"하는 배선은 이미 있고, 추가할 것은 **사용자 업로드 용어집을 문서별로 저장해 번역에
먹이는 것**이다.

## 사용자 결정 사항 (확정)

- **형식**: CSV `원어,번역어` (두 열). 헤더 행은 자동 스킵.
- **시점·범위**: 홈 업로드 화면에서 **문서와 함께(문서별)**. `storage/{doc_id}/glossary.json`.
- **병합**: 내장 용어집 + 사용자 용어집, **충돌 시 사용자 우선**.
- **양식 제공**: 홈 화면에서 **용어집 CSV 양식을 다운로드**해 그 양식대로 채워 올린다.

## 내장 vs 사용자 용어집

- **내장**: `backend/glossary/{src}-{tgt}.json` — 리포에 포함된 정적 도메인 사전. 해당 언어쌍의
  **모든 문서**에 자동 적용(소방장비 전문용어). 사용자가 못 바꿈.
- **사용자**: 업로드 CSV — **그 문서에만** 적용되는 사용자 고유 용어. 충돌 시 내장을 덮어씀.
- 병합 결과 = "도메인 기본 용어 + 사용자 용어(우선)". (내장이 도메인 특화라 무관한 문서엔
  부적합할 수 있으나, v1은 병합 유지; "내장 비활성화" 옵션은 후순위.)

## 비목표 (v1)

- xlsx / JSON 업로드 (CSV만).
- 전역(모든 문서 공용) 용어집, 용어집 관리/편집 UI.
- 용어집 미리보기/수정 화면 (업로드만).
- 방향 자동 전환·다국어 용어집 (문서의 source→target 쌍에 그대로 적용).

## 아키텍처 / 흐름

```
[홈 업로드 화면]
  문서 파일 + (선택) 용어집 CSV
  → POST /api/documents  (FormData: pdf + glossary?)
      · glossary 있으면: parse_glossary_csv → storage/{doc_id}/glossary.json 저장
  → _run_job → get_translated_model
      · load_doc_glossary(doc_id) 로드
      · translate_document(..., extra_glossary=doc_glossary)
          glossary = { **load_glossary(src,tgt)(내장), **doc_glossary(사용자 우선) }
  → 재번역 모드(retranslate_one_segment)도 동일 병합으로 일관 적용
```

## 백엔드 변경

### translator.py
- `parse_glossary_csv(text: str) -> dict[str, str]`: `csv` 모듈로 앞 두 열을 `{원어: 번역어}`로.
  빈 행·2열 미만 행 스킵, 양끝 공백 strip, **헤더 행 휴리스틱 스킵**(첫 행의 셀이
  `source/target/원어/번역/번역어/term/translation` 등에 해당하면 제외).
- `translate_document(doc, target_lang, ..., extra_glossary: dict | None = None)`:
  `glossary = {**load_glossary(src,tgt), **(extra_glossary or {})}` (사용자 우선) 후 기존대로 사용.
- `translate_segment(source_text, src, tgt, guidance="", translator=None, extra_glossary=None)`:
  동일하게 병합한 glossary로 번역. (`.translate()` 시그니처는 불변 — 호출부에서 병합해 전달.)

### process.py
- `load_doc_glossary(storage_dir, doc_id) -> dict`: `storage/{id}/glossary.json` 읽기(없으면 `{}`).
- `get_translated_model`: 번역 직전 `extra = load_doc_glossary(...)` → `translate_document(..., extra_glossary=extra)`.
- `retranslate_one_segment`: `translate_segment(..., extra_glossary=load_doc_glossary(...))`.
- `save_doc_glossary(storage_dir, doc_id, mapping)`: `storage/{id}/glossary.json` 기록(업로드 시).

### main.py
- `POST /api/documents`에 `glossary: UploadFile | None = File(None)` 추가. 제공되면 바이트를
  텍스트로 디코드(utf-8-sig 우선) → `parse_glossary_csv` → `process.save_doc_glossary`.
  파싱은 관대(잘못된 행 스킵); 0개여도 업로드는 실패시키지 않음(내장 용어집만 사용).
- 응답에 `glossary_terms: int`(적재된 용어 수) 포함(프론트가 "용어집 N개 적용" 표시 가능).
- `GET /api/glossary/template`: 용어집 CSV **양식** 다운로드. `utf-8-sig`(Excel에서 한글 정상),
  헤더 `원어,번역어` + 예시 2행(예: `소방사다리차,Aerial Ladder Vehicle`). `Content-Disposition:
  attachment; filename="glossary-template.csv"`. 헤더 행은 parse_glossary_csv가 스킵하므로
  양식↔파서 정합.

## 프론트엔드 변경

### [frontend/src/lib/api.ts](../../../frontend/src/lib/api.ts)
- `upload(file, sourceLang, targetLang, glossary?)`: `glossary`가 있으면 FormData에 `glossary` 추가.

### [frontend/src/app/page.tsx](../../../frontend/src/app/page.tsx)
- 상태 `glossaryFile: File | null`. 문서 입력 아래에 **"용어집 (CSV · 선택)"** 파일 input
  (`accept=".csv,text/csv"`), 선택 파일명 표시. `onStart`에서 `api.upload(file, src, tgt, glossaryFile)`.
- 용어집 input 옆에 **"양식 다운로드"** 링크 (`<a href="/api/glossary/template" download>`) —
  사용자가 양식을 받아 채워서 업로드하도록 안내.

> 주의: [frontend/AGENTS.md](../../../frontend/AGENTS.md) — 이 Next.js는 표준과 다를 수 있음, 코딩 전
> `node_modules/next/dist/docs/` 확인. 본 변경은 표준 폼/FormData라 영향 적음.

## 검증

- 백엔드 테스트(`backend/tests/`):
  - `test_glossary_csv.py`: `parse_glossary_csv` — 헤더 스킵, 빈/1열 행 스킵, 따옴표·콤마 포함 값, BOM(utf-8-sig).
  - `test_glossary_translation.py`: `translate_document`/`translate_segment`의 `extra_glossary` 병합·사용자 우선(Mock로 확인 — 사용자 항목이 내장 항목을 덮어쓰는지).
  - `test_glossary_upload.py`(또는 기존 api 테스트 확장): `POST /api/documents`에 CSV 첨부 시
    `storage/{id}/glossary.json` 저장 + 응답 `glossary_terms` 정확. (TestClient, MockTranslator로 오프라인.)
- 프론트: `npm run build` clean. 수동: 용어집 CSV 업로드 → 번역 결과에 사용자 용어 반영 확인(prod build).

## 빌드 순서

1. translator: `parse_glossary_csv` + `extra_glossary` 병합(`translate_document`/`translate_segment`) + 테스트.
2. process: `load_doc_glossary`/`save_doc_glossary` + `get_translated_model`/`retranslate_one_segment` 전달 + 테스트.
3. main.py: 업로드에 glossary 파싱·저장 + 응답 term 수 + `GET /api/glossary/template` + 테스트.
4. 프론트: `api.upload` glossary 인자 → `page.tsx` 용어집 input + "양식 다운로드" 링크.
5. 통합 검증(prod build, 양식 다운로드 → 채워서 업로드 → 번역 반영 확인).
