$ErrorActionPreference = 'Stop'
$chatbotRoot = Split-Path -Parent $PSScriptRoot
$chatbotPython = Join-Path $chatbotRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $chatbotPython)) {
    throw 'Create .venv and install requirements-dev.lock first; see README.md.'
}
if (-not (Test-Path -LiteralPath (Join-Path $chatbotRoot '.env'))) {
    throw 'Run .venv\Scripts\python.exe -m app.cli init first.'
}
Push-Location $chatbotRoot
try {
    & $chatbotPython -X utf8 -u scripts/warmup.py
    if ($LASTEXITCODE -ne 0) { throw 'Local model warmup failed; bot not started.' }
    & $chatbotPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
} finally {
    Pop-Location
}
