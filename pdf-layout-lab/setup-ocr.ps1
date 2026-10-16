param([string[]]$Languages = @('kor','eng'))
$modelDirectory = Join-Path $PSScriptRoot 'models/tessdata'
New-Item -ItemType Directory -Force -Path $modelDirectory | Out-Null
foreach ($language in $Languages) {
    if ($language -notmatch '^[a-z_]+$') { throw 'Invalid OCR language code' }
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/$language.traineddata" -OutFile (Join-Path $modelDirectory "$language.traineddata")
}
