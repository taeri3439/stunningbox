# Phase 1 — 로컬 실행 (POC)

## 준비 (최초 1회)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

cd ..\frontend
npm install
```

`backend/.env`: `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, `DEV_NO_AUTH=1`  
`frontend/.env.local`: `DEV_NO_AUTH=1`, `BACKEND_URL=http://127.0.0.1:8000`

## 실행

```powershell
.\scripts\start-local.ps1
```

또는 터미널 2개:

```powershell
# 1) backend
cd backend
$env:DEV_NO_AUTH="1"
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

# 2) frontend
cd frontend
npm run dev
``` 

브라우저: http://localhost:3000/app → PDF 업로드 → 번역 진행.

**로그인:** Supabase 키가 없을 때(`DEV_NO_AUTH=1`)는 **로그인 없이** `/app`만 사용합니다.  
랜ding의 「무료로 시작」·`/login` 은 자동으로 `/app`으로 이동합니다.  
실제 이메일 로그인이 필요하면 Supabase 설정 후 `DEV_NO_AUTH=0` 으로 전환하세요.

## Supabase (Phase 1 이후 / 로그인 필요 시)

1. [supabase.com](https://supabase.com) 새 프로젝트
2. SQL Editor → `backend/db/documents.sql` 실행
3. API 키를 `frontend/.env.local` + `backend/.env`에 입력
4. `DEV_NO_AUTH=0` (또는 삭제) 후 서버 재시작 → `/login`에서 회원가입

## 확인

- http://127.0.0.1:8000/api/health → `"mode":"openai"` (또는 anthropic)
- 업로드 후 `/p/{doc_id}` 또는 `/c/{doc_id}`에서 상태 polling
