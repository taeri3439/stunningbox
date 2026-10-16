param([string]$ListenHost = '127.0.0.1')

Set-Location -LiteralPath $PSScriptRoot
$labPython = Join-Path $PSScriptRoot '../backend/.venv/Scripts/python.exe'
if (Test-Path -LiteralPath $labPython) {
    & $labPython -m uvicorn app:app --host $ListenHost --port 8011
} else {
    python -m uvicorn app:app --host $ListenHost --port 8011
}
