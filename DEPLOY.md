# Railway 배포 가이드

이 앱은 **2개 프로세스**다: FastAPI 백엔드 + Next.js 프론트엔드. Railway에서는
**하나의 GitHub 레포 안에 서비스 2개**(monorepo)로 띄운다. 프론트가 공개 도메인을
갖고, `/api/*` 요청을 사설망으로 백엔드에 프록시한다([frontend/next.config.ts](frontend/next.config.ts)의 `BACKEND_URL`).

```
[사용자] ── HTTPS ──► frontend (Next.js, 공개)
                          │  /api/* 프록시 (사설망)
                          ▼
                      backend (FastAPI, 비공개) ──► Volume(/data) : 업로드 PDF·assets·캐시
                                                └─► Anthropic API (ANTHROPIC_API_KEY)
```

이 레포에 미리 넣어 둔 것: 서비스별 [`backend/railway.json`](backend/railway.json) ·
[`frontend/railway.json`](frontend/railway.json), Python 핀 [`backend/.python-version`](backend/.python-version),
환경변수 예시 [`backend/.env.example`](backend/.env.example). 실제 프로젝트 생성·변수 입력은
Railway 대시보드에서 진행한다(아래 순서대로).

---

## 0. 사전 준비
- Railway 계정 + 이 레포가 GitHub(`danielchungg/stunningboxreflow`)에 push 되어 있을 것.
- Anthropic API 키.

## 1. 프로젝트 생성 + 백엔드 서비스
1. Railway → **New Project → Deploy from GitHub repo** → 이 레포 선택.
2. 생성된 서비스 이름을 **`backend`** 로.
3. **Settings → Source → Root Directory = `backend`** (모노레포이므로 필수).
4. **Settings → Build/Deploy → Config-as-code 파일 경로**를 `backend/railway.json` 으로 지정.
   - ⚠️ Railway 설정파일은 Root Directory를 자동으로 따라가지 않는다. 경로를 못 잡으면
     Config 파일 대신 **Deploy → Custom Start Command** 에 직접 입력:
     `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. **Variables** 에 추가:
   - `ANTHROPIC_API_KEY` = `sk-ant-...`
   - `STORAGE_DIR` = `/data/storage`
   - (선택) `ANALYZER_MODEL` / `TRANSLATOR_MODEL` — 비용 절감 시 `claude-haiku-4-5-20251001`
6. **Volume 붙이기** (영구 저장 — 안 하면 재배포·재시작 때 업로드 문서가 전부 사라짐):
   - 서비스 → **Volumes → New Volume**, Mount path = **`/data`**.
   - 위에서 `STORAGE_DIR=/data/storage`로 맞췄으므로 그 하위에 저장된다(앱이 자동 생성).
7. (지금은) 백엔드를 **비공개**로 둔다 — Public Domain을 만들지 않으면 사설망 전용.

## 2. 프론트엔드 서비스
1. 같은 프로젝트에서 **New → GitHub Repo**(같은 레포) 또는 **Add Service → from repo**.
2. 서비스 이름 **`frontend`**.
3. **Root Directory = `frontend`**, Config 파일 경로 = `frontend/railway.json`
   - 못 잡으면 Custom Start Command: `npm run start`, Build Command: `npm run build`.
4. **Variables → `BACKEND_URL`** 설정 (둘 중 하나):
   - **권장(사설망):** `BACKEND_URL=http://${{backend.RAILWAY_PRIVATE_DOMAIN}}:${{backend.PORT}}`
     - `backend`는 1번에서 정한 서비스 이름. 사설 DNS는 `backend.railway.internal`.
   - **간단(공개):** 백엔드에 Public Domain을 만들고 `BACKEND_URL=https://<backend-공개도메인>`
     (포트 없이). 백엔드 CORS는 이미 `*` 허용이라 동작한다.
5. **Networking → Generate Domain** 으로 프론트에 공개 도메인 부여 → 이 주소가 앱 진입점.

## 3. 배포 확인
- 두 서비스가 **Active** 인지 확인.
- 프론트 공개 도메인 접속 → 상단 헤더 + 모드 배지 확인. 배지가 **"Claude 모드"** 면 키가
  제대로 들어간 것(오프라인 목업이면 키 변수 누락).
- 작은 PDF로 업로드 → 번역 → docx 까지 한 번 돌려본다.

---

## 반드시 알아둘 함정

1. **`BACKEND_URL`은 빌드 시점에 박힌다.** Next.js `rewrites()`는 빌드 때 평가되어 라우트에
   값이 구워진다. 따라서 `BACKEND_URL`은 **프론트 첫 빌드 전에** 변수로 설정돼 있어야 하고,
   바꾸면 **재배포(rebuild)** 해야 반영된다.
2. **긴 업로드 요청.** 큰 문서는 페이지마다 Opus 비전 호출이라 수 분이 걸린다. Railway
   엣지/프록시 타임아웃에 걸릴 수 있다. 우선 작은 문서로 검증하고, 큰 문서가 끊기면:
   백엔드에 별도 공개 도메인을 줘 **업로드만 백엔드로 직접** 보내거나, 업로드를 비동기
   잡으로 바꾸는 후속 작업이 필요(현재 동기 처리).
3. **Volume 없으면 데이터 소실.** 업로드 PDF·페이지 렌더·번역 캐시·편집본이 전부 디스크
   기반이고 요청 간 다시 읽는다. Volume 필수(1-6번).
4. **사설망 IPv6.** 2025-10-16 이후 생성 환경은 내부 DNS가 IPv4+IPv6 모두 해석되어 Node
   fetch 프록시가 정상 동작. 그 이전(legacy) 환경은 IPv6만이라 드물게 이슈 가능 → 그땐
   공개 도메인 방식(2-4 간단)으로.
5. **시크릿.** `ANTHROPIC_API_KEY`는 Railway Variables에만. 로컬 `backend/.env`는 gitignore라
   커밋되지 않는다([.env.example](backend/.env.example) 참고).

## 비용
- Railway 사용량 과금(서비스 2개 + Volume) + Anthropic API(문서당 페이지수 × 비전/번역).
  테스트는 작은 PDF + 필요 시 `ANALYZER_MODEL`/`TRANSLATOR_MODEL`을 haiku로 낮춰서.

## 오피스 변환 (LibreOffice)
- HWP·Word 업로드는 LibreOffice headless로 `.docx` 변환 후 처리한다(`pipeline/convert.py`).
  배포 이미지에는 [backend/nixpacks.toml](backend/nixpacks.toml)이 `aptPkgs=["libreoffice"]`로
  설치한다(이미지 ~수백 MB↑).
- **로컬 개발**: LibreOffice를 직접 설치하고 `SOFFICE_BIN`을 실행 파일 경로로 지정한다.
  예) Windows: `SOFFICE_BIN="C:\Program Files\LibreOffice\program\soffice.exe"`.
- 배포 후 컨테이너에서 `soffice --version`이 동작하는지 확인.

## 참고 문서
- Monorepo: https://docs.railway.com/deployments/monorepo
- Config as Code: https://docs.railway.com/reference/config-as-code
- Private Networking: https://docs.railway.com/networking/private-networking
- Volumes: https://docs.railway.com/volumes
