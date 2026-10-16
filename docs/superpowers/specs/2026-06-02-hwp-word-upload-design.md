# HWP/Word 업로드 지원 — 설계 (길 B: DOCX 네이티브 직접 읽기)

작성일: 2026-06-02 · 브랜치: workflowqa-2026-06-02

## 1. Context (왜 이 변경을 하는가)

현재 앱은 PDF 업로드만 허용한다 — 프론트엔드 `accept` 속성([frontend/src/app/page.tsx:116](../../../frontend/src/app/page.tsx))과 백엔드 확장자 검증([backend/main.py:116](../../../backend/main.py)) 둘 다 PDF로 하드락되어 있다. 사용자는 한글(HWP)과 Word 문서도 업로드해 번역할 수 있길 원한다.

이 앱의 본질은 **"구조가 없는 PDF에서 Claude 비전으로 구조(제목·표·목록)를 복원"**하는 것이다. 페이지를 이미지로 래스터화 → 비전 모델이 구조 추론 → 의미 모델(`SemanticDocument`)로 재구성한다. 이는 PDF가 평평한 레이아웃이라 구조 정보가 없기 때문에 필요한 과정이다.

그런데 HWP·Word는 제목 스타일·표·목록·이미지를 **파일 안에 명시적으로** 갖고 있다. 이미 가진 구조를 PDF로 납작하게 뭉갰다가 비싼 비전 패스로 다시 추론하는 것은 거꾸로다 — 손실이 생기고, 페이지마다 비전 API 비용이 들며, 충실도가 비전 추론 정확도에 묶인다.

**의도하는 결과**: Office 입력은 DOCX의 명시적 구조를 직접 읽어 `SemanticDocument`로 매핑한다(비전 단계 건너뜀). API 비용 0, 구조 정확, 충실도↑. 그 이후 translator·렌더러·OnlyOffice 편집·저장 구조는 기존 그대로 재사용한다.

## 2. 결정 사항 (사용자 확인 완료)

- **지원 포맷**: Word(.docx/.doc) + HWP(.hwp/.hwpx)
- **변환 엔진**: LibreOffice headless — Word+HWP를 한 도구로 커버하는 유일한 선택(OnlyOffice 변환 API는 HWP 미지원). **입력 변환 전용**이며, 출력 .docx 편집(OnlyOffice Document Server)에는 영향이 없다.
- **인제스트 전략**: 길 B (DOCX 네이티브 직접 읽기). 대안인 길 A(HWP→PDF→기존 비전 파이프라인)는 손실·API 비용·충실도 제한 때문에 기각.
- **게이팅**: 구현 전 실제 한글 샘플로 변환 품질 스파이크를 선행한다.

## 3. 아키텍처

두 인제스트 경로가 하나의 의미 모델로 수렴한다.

```
PDF 업로드   ─→ extractor(fitz) → analyzer(비전) → reconciler ──┐
                                                                 ├─→ SemanticDocument → translator → 렌더러 → OnlyOffice 편집
Office 업로드 ─→ [HWP/DOC → DOCX] → docx_extractor(신규) ────────┘      (전부 기존 재사용)
 (.hwp/.hwpx/.doc/.docx)
```

- 기존 PDF 비전 경로는 **손대지 않는다**(회귀 위험 0).
- Office 경로만 신규 추가. 두 경로 모두 `SemanticDocument`로 수렴하므로 translator·렌더러·OnlyOffice·`source.json` 저장 형식을 100% 공유한다.
- 분기는 업로드 확장자 기준으로 백그라운드 잡([backend/main.py `_run_job`](../../../backend/main.py))에서 일어난다.

## 4. 컴포넌트 (격리된 단위)

### 4.1 신규 `backend/pipeline/convert.py` — "Office → DOCX"

- 인터페이스: `to_docx(src_path: str, out_dir: str) -> str` (DOCX 경로 반환).
- `.docx` 입력은 no-op. `.doc/.hwp/.hwpx`는 LibreOffice 호출:
  ```
  soffice --headless --convert-to docx --outdir <out_dir> <src_path>
        -env:UserInstallation=file:///<고유-임시-프로필>
  ```
- **동시성 안전**: soffice는 프로필 공유 시 동시 실행이 깨진다 → 호출마다 고유 `-env:UserInstallation` 프로필 디렉터리로 격리한다.
- `soffice` 실행 경로는 `SOFFICE_BIN` 환경변수(기본값 `soffice`)로 오버라이드 — 로컬 Windows와 Railway Linux 경로 차이 대응.
- 변환 실패 시 포맷·stderr를 포함한 명확한 예외를 던진다 → 백그라운드 잡이 `jobs.fail`로 사용자에게 노출.
- 내부 구현(LibreOffice)을 몰라도 쓸 수 있고, 구현을 바꿔도 호출자는 불변이다.

### 4.2 신규 `backend/pipeline/docx_extractor.py` — "DOCX → SemanticDocument"

- 인터페이스: `extract_docx(docx_path: str, asset_dir: str) -> SemanticDocument`.
- python-docx로 읽어 의미 블록으로 매핑:

| DOCX 요소 | → 의미 블록 | 근거 |
|---|---|---|
| 단락 스타일 `Heading N` | `HeadingBlock(level=N)` | 스타일 이름 |
| 번호/글머리 목록 단락 | `ListItemBlock(level=ilvl, marker)` | numbering `ilvl`/번호 |
| 일반 단락 | `ParagraphBlock(text, align, indent)` | paragraph_format |
| 표 | `TableBlock` + `TableCell(rowspan/colspan, is_header)` | XML `gridSpan`/`vMerge` |
| 인라인 이미지 | `FigureBlock(image_ref)` | docx zip에서 이미지 추출 → asset_dir |
| 섹션 머리말/꼬리말 | `running_header`/`running_footer (RunningBand)` | section.header/footer |
| 캡션 스타일 | `CaptionBlock` | 스타일 이름 |

- `bold`/`align`/`font_size`는 run·단락 속성에서 채운다.
- `page`·`source_span_ids` 등 PDF 전용 필드는 None/빈값으로 둔다. 의미 모델([backend/models.py](../../../backend/models.py))의 기하 필드는 모두 Optional이고, `source_span_ids`는 reconciler 전용이라 translator/렌더러 동작에 영향이 없다.

### 4.3 라우팅 — `backend/main.py` (`upload`, `_run_job`)

- `upload`: 검증을 허용 집합 `{.pdf, .docx, .doc, .hwp, .hwpx}`로 확장. 업로드 파일을 **원본 확장자 그대로**(`source<ext>`) 저장하고, 원본 경로를 잡에 전달.
- `_run_job`: 첫 단계에서 확장자로 분기.
  - `.pdf` → 기존 `process.build_source_model`(비전 경로) 그대로.
  - Office → `convert.to_docx` → `docx_extractor.extract_docx` → 결과를 `source.json`으로 저장(기존 캐시 형식과 동일) → 이후 translator/렌더 공유.
- 진행 표시: Office 경로는 `report("convert", …)` → `report("extract", …)`. 비전 "analyze" 단계가 없어 더 빠르다. 변환 실패는 기존 `jobs.fail` 경로로 노출.

### 4.4 프론트엔드

- [frontend/src/app/page.tsx:116](../../../frontend/src/app/page.tsx): `<input accept>`에 office 확장자 추가, 안내 문구 갱신.
- [frontend/src/lib/api.ts:26](../../../frontend/src/lib/api.ts): FormData 필드명 `pdf`는 유지(기능 무관, 선택적으로 `file` 리네임).
- 진행 표시 UI에 `convert` 단계 라벨 추가.

### 4.5 배포 — 신규 `backend/nixpacks.toml`

- 백엔드는 Railway Nixpacks 빌드([backend/railway.json](../../../backend/railway.json)). LibreOffice를 빌드 단계에 추가:
  ```toml
  [phases.setup]
  aptPkgs = ["libreoffice"]   # 또는 nixPkgs = ["...", "libreoffice"]
  ```
  이미지/빌드 시간이 ~수백 MB 늘어난다(감수).
- 로컬 개발: README/DEPLOY에 LibreOffice 설치 안내 + Windows는 `soffice.exe` 경로를 `SOFFICE_BIN`으로 지정.

## 5. 게이팅 스파이크 (구현 전 선행, 프로덕션 코드 변경 없음)

길 B의 진짜 리스크 = **HWP→DOCX 변환이 구조를 보존하는가**. LibreOffice가 HWP 제목을 `Heading` 스타일로 살리는지, 전부 `Normal` 단락으로 납작하게 만드는지가 길 B의 성패를 가른다(구조가 사라지면 네이티브 읽기의 이점이 없다).

1. 로컬 Windows에 LibreOffice 설치 → `soffice.exe` 경로 확보.
2. 사용자의 실제 `.hwp`/`.hwpx` 샘플을 `--convert-to docx`로 변환.
3. 결과 DOCX를 python-docx 또는 Word로 열어 **① 제목이 `Heading N` 스타일로 보존됐는지 ② 표 구조(병합 포함) ③ 목록 번호/레벨 ④ 이미지**를 점검.
4. 판정: 보존 양호 → 길 B 진행. HWP가 구조를 못 살리면 → 해당 포맷만 길 A(PDF 비전) 폴백으로 처리하거나 보류. `.docx/.doc`는 이미 구조가 있어 안전.

## 6. 재사용 (새로 만들지 않음)

PDF 비전 경로 전체, translator, docx/html 렌더러, OnlyOffice 편집·콜백, `jobs.progress`/`jobs.fail` 진행·오류 채널, `source.json` 저장 형식.

## 7. 리스크 / 주의

- **HWP→DOCX 구조 보존**(특히 신형 .hwpx): 스파이크로 선검증. 미흡 시 폴백/보류.
- **soffice 동시성**: 고유 프로필 격리 미적용 시 동시 업로드에서 변환이 깨진다 → 필수.
- **두 인제스트 경로 유지비**: PDF=비전, Office=네이티브. 단 SemanticDocument 이후는 단일 경로라 분기 비용은 추출 단계에 한정된다.
- **변환 지연**: 큰 문서는 수 초~십수 초. 백그라운드 잡 처리라 업로드 요청 자체는 즉시 반환된다.

## 8. 검증 (엔드투엔드)

1. 스파이크 통과(§5) 확인.
2. `docx_extractor` 단위: 헤딩/표(병합)/목록(레벨)/이미지가 올바른 블록으로 매핑.
3. `convert` 단위: .doc/.hwp/.hwpx → DOCX 정상 생성.
4. E2E: 로컬에서 백엔드+프론트+OnlyOffice 기동 → 각 포맷 업로드 → 진행바에 convert/extract 표시 → 번역 .docx가 OnlyOffice에서 정상 열리고 **편집 가능**.
5. 회귀: 기존 PDF 비전 경로 불변 동작.
6. 동시성: 두 office 문서 동시 업로드 충돌 없음.
7. 배포: Railway 빌드 성공 + 배포 환경에서 `soffice --version` 실행 가능.

## 9. 스파이크 결과 및 HWP 설계 수정 (2026-06-02)

실제 KFI 소방 인증기준 `.hwp` 6개로 §5 스파이크를 수행한 결과, **핵심 가정이 틀렸음**을 확인했다.

**발견**
- **LibreOffice는 실제 `.hwp`를 전혀 로드하지 못한다** ("source file could not be loaded").
  libhwp 필터가 실무 한글 HWP를 못 읽음(txt→docx는 정상이므로 엔진 문제 아님). →
  당초 계획의 "LibreOffice로 HWP→DOCX"는 **불가능**.
- **pyhwp `hwp5txt`는 본문을 완벽히 추출**하고, `hwp5html`은 표·본문을 보존한 XHTML을 만든다.
  단 `hwp5html`은 제목을 `<h>`가 아닌 일반 `<p>`로 출력 → **제목 계층이 의미적으로 소실**된다
  (docx_extractor의 `Heading N` 감지가 안 됨). `hwp5odt`는 RelaxNG 검증 버그로 실패.

**결정 (사용자 승인)**: HWP는 네이티브 경로 대신 **vision 경로**로 처리한다.
- `.hwp` → `pyhwp hwp5html`(XHTML) → `LibreOffice`(HTML→PDF) → **기존 비전 파이프라인**
  (`build_source_model`)이 제목·표·목록을 시각적으로 복원. 페이지당 비전 API 비용 발생(수용).
- `.doc/.docx` → 기존대로 LibreOffice→docx→`docx_extractor`(네이티브 Path B). 영향 없음.
- `.hwpx` → **보류**(검증 안 됨, allow-list·accept에서 제외).

**구현 반영**: `convert.hwp_to_pdf`(hwp5html+soffice), `main._run_job`이 `.hwp`를 PDF로
변환 후 비전 빌드로 라우팅, `pyhwp`/`six`를 requirements에 추가. 실제 샘플로 hwp_to_pdf가
유효한 6페이지 PDF(텍스트 4939자)를 생성함을 확인.

**남은 검증**: 비전 빌드까지 도는 풀 E2E(번역 .docx가 OnlyOffice에서 편집되는지)는 Anthropic
API 키·비용이 필요해 사용자 실행 대기.
