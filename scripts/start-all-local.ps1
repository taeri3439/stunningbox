$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot 'backend/.venv/Scripts/python.exe'
$logDirectory = Join-Path $projectRoot '_local-logs'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create backend/.venv and install both requirements files first.' }
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$env:DEV_NO_AUTH = '1'
$env:NEXT_PUBLIC_DEV_NO_AUTH = '1'
$services = @(
    @{ Name = 'backend'; Port = 8000; Directory = 'backend'; Command = $pythonPath; Arguments = @('-m', 'uvicorn', 'main:app', '--host', '0.0.0.0', '--port', '8000') },
    @{ Name = 'frontend'; Port = 3000; Directory = 'frontend'; Command = (Get-Command node.exe).Source; Arguments = @('node_modules/next/dist/bin/next', 'dev', '--hostname', '127.0.0.1', '--port', '3000') },
    @{ Name = 'pdf-layout-lab'; Port = 8011; Directory = 'pdf-layout-lab'; Command = $pythonPath; Arguments = @('-m', 'uvicorn', 'app:app', '--host', '0.0.0.0', '--port', '8011') }
)
foreach ($service in $services) {
    if (Get-NetTCPConnection -State Listen -LocalPort $service.Port -ErrorAction SilentlyContinue) {
        Write-Host "$($service.Name): port $($service.Port) already in use; skipped."
        continue
    }
    $process = Start-Process -FilePath $service.Command -ArgumentList $service.Arguments -WorkingDirectory (Join-Path $projectRoot $service.Directory) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "$($service.Name).out.log") -RedirectStandardError (Join-Path $logDirectory "$($service.Name).err.log") -PassThru
    Write-Host "$($service.Name): PID $($process.Id), http://127.0.0.1:$($service.Port)"
}
Write-Host "Logs: $logDirectory"
