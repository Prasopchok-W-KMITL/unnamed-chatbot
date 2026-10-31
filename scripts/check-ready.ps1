$ErrorActionPreference = 'Stop'
$chatbotRoot = Split-Path -Parent $PSScriptRoot
$chatbotPython = Join-Path $chatbotRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $chatbotPython)) {
    throw 'Create .venv and install requirements-dev.lock first; see README.md.'
}
Push-Location $chatbotRoot
try {
    & $chatbotPython -X utf8 -m app.preflight @args
    $readinessExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $readinessExitCode
