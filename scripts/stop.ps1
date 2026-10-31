$ErrorActionPreference = 'Stop'
$chatbotRoot = Split-Path -Parent $PSScriptRoot
$chatbotRecord = Join-Path $chatbotRoot 'data\runtime\managed-processes.json'
if (-not (Test-Path -LiteralPath $chatbotRecord)) {
    Write-Output 'No recorded background processes. Use Ctrl+C in the terminals you started.'
    exit 0
}
$chatbotManaged = Get-Content -LiteralPath $chatbotRecord -Raw | ConvertFrom-Json
foreach ($chatbotEntry in $chatbotManaged) {
    $chatbotProcess = Get-Process -Id $chatbotEntry.pid -ErrorAction SilentlyContinue
    if (-not $chatbotProcess) { continue }
    # A recycled PID must never stop an unrelated process.
    $chatbotRecordedStart = ([datetime]$chatbotEntry.startedUtc).ToUniversalTime()
    if ($chatbotProcess.StartTime.ToUniversalTime().Ticks -ne $chatbotRecordedStart.Ticks) {
        Write-Output ('Skipped reused PID for ' + $chatbotEntry.role)
        continue
    }
    Stop-Process -Id $chatbotProcess.Id
    Write-Output ('Stopped ' + $chatbotEntry.role)
}
Write-Output 'Docker and Ollama remain running.'
