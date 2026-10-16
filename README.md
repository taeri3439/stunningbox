# PDF 구조 보존 번역기 (Skeleton-Preserving PDF Translator)

PDF를 업로드하면 **시각적 논리구조(제목 계층·번호 체계·표·문단·캡션)를 보존한 채**
번역해 주는 웹앱. 한→영처럼 분량이 늘어나도 페이지를 reflow 하여 구조는 깨지 않는다.

## 동작 원리 (Phase 1)

```
PDF ─► [0] 추출(PyMuPDF)        텍스트+좌표+이미지+페이지 렌더 (무손실 ground truth)
    ─► [1] 구조분석(Claude 비전) 의미 블록 트리 JSON (무엇이 무엇인지)
    ─► [2] 대조/정규화           누락 0 검증 + 페이지 넘은 블록 재결합
    ─► [3] 번역(Claude+용어집)   트리의 잎(텍스트)만 교체 → 구조 불변
    ─► [4] 렌더                  HTML 비교 뷰어 + docx 다운로드
```

핵심 설계: **의미 모델(JSON 트리)이 계약**이다. 번역은 잎 텍스트만 바꾸므로 구조가
절대 깨지지 않고, 같은 모델에서 렌더러(HTML/docx)만 갈아끼운다. **대조 게이트**가
원문 텍스트 100% 보존을 보장한다(보존율을 UI에 표시).

## 실행 (백엔드 + 프론트엔드 2개 터미널)

**1) 백엔드 (FastAPI, 포트 8000)**
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows (mac/linux: source .venv/bin/activate)
pip install -r requirements.txt

# 실제 번역/구조분석에 필요 (없으면 자동으로 오프라인 목업 모드로 동작)
set ANTHROPIC_API_KEY=sk-ant-...  # Windows (mac/linux: export ...)

uvicorn main:app --reload
```

**2) 프론트엔드 (Next.js, 포트 3000)**
```bash
cd frontend
npm install
npm run dev
```

브라우저에서 http://127.0.0.1:3000 접속 → PDF 선택 → 원문/번역 언어 선택 → **번역**
→ 번역 패널에서 텍스트를 클릭해 바로 편집 → **편집 저장** / **docx 다운로드**.

Next.js가 `/api/*` 요청을 FastAPI(8000)로 프록시하므로 개발 중 CORS 설정이 필요 없다
(`frontend/next.config.ts`의 `BACKEND_URL`로 백엔드 주소 변경 가능).

- **API 키 없이도** 전체 파이프라인이 동작한다(오프라인 목업: 구조는 폰트/번호 휴리스틱,
  번역은 `[en] …` 태깅 + 용어집 적용). 배선·렌더링·다운로드를 키 없이 확인할 수 있다.
- **API 키가 있으면** Claude 비전이 표/그림/읽기순서를 제대로 해석하고 실제 번역을 수행한다.

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | (자동) | `openai` 또는 `anthropic` (키가 둘 다 있으면 anthropic 우선, OpenAI만 쓰려면 `openai` 지정) |
| `OPENAI_API_KEY` | (없음) | GPT 번역·구조분석 |
| `ANTHROPIC_API_KEY` | (없음) | Claude 번역·구조분석 |
| `ANALYZER_MODE` | `claude` | `heuristic`으로 강제 가능 |
| `ANALYZER_MODEL` / `OPENAI_ANALYZER_MODEL` | provider별 | 구조분석 모델 |
| `TRANSLATOR_MODEL` / `OPENAI_TRANSLATOR_MODEL` | provider별 | 번역 모델 |
| `STORAGE_DIR` | `backend/storage` | 산출물 저장 위치 |

## 구조

```
backend/
  main.py                 FastAPI (업로드/번역/뷰/다운로드) + 정적 프론트 서빙
  models.py               의미 모델 스키마 (계약)
  pipeline/
    extractor.py          [0] PyMuPDF 추출 + 페이지 렌더
    analyzer.py           [1] Claude 비전 / 휴리스틱 구조분석
    reconciler.py         [2] 누락 대조 + 페이지 넘은 블록 재결합
    translator.py         [3] 잎 텍스트만 번역 + 용어집
    process.py            오케스트레이터 (+ 언어별 캐시)
  renderers/
    html_renderer.py      [4] 의미 모델 → HTML
    docx_renderer.py      [4] 의미 모델 → docx
  glossary/ko-en.json     도메인 용어집
  web/index.html          단일 파일 비교 뷰어 SPA
```

## 용어집 추가

`backend/glossary/{src}-{tgt}.json` 에 `{"원문용어": "번역용어"}` 형태로 추가하면
해당 언어쌍 번역 시 자동 적용된다 (현재 `ko-en.json` 시드 제공).

## 알려진 범위 / 다음 단계

- **Phase 1**: 디지털 PDF(텍스트 레이어 존재) 본문·표·문단·번호 보존 + docx/HTML. 그림은
  원본 이미지 그대로 삽입, 캡션만 번역.
- **Phase 2 (예정)**: 그림 속 글자 번역(OCR → 마스킹 → 오버레이), 저신뢰 시 폴백.
- 표/그림/읽기순서의 정밀 복원은 Claude 비전 경로(API 키)에서 품질이 나온다. 휴리스틱은
  배선 검증용.

## 프론트엔드 관련 메모

당초 계획의 Next.js 대신 **무빌드 단일 파일 SPA**(`web/index.html`)를 FastAPI가 직접
서빙한다. 명령 한 줄로 전체 앱이 뜨고 별도 노드 서버가 필요 없다. UX 요구사항(업로드·
진행·좌우 비교·언어 선택·다운로드)은 모두 충족하며, 추후 Next.js로 이전 가능하다.
