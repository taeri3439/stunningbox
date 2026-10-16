# Phase 1 local stack: backend :8000 + frontend :3000 (DEV_NO_AUTH, no Supabase required).
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"

Write-Host "Backend:  http://127.0.0.1:8000  (health: /api/health)"
Write-Host "Frontend: http://localhost:3000/app"
Write-Host "Stop with Ctrl+C in each window."

Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$Backend'; `$env:DEV_NO_AUTH='1'; .\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000"
)
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$Frontend'; npm run dev"
)
