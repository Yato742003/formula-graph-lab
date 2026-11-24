# Self-check script for run.ps1 orchestrator (Ponytail verification check)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "[CHECK] 1. Parsing scripts/run.ps1 syntax..." -ForegroundColor Cyan
$errors = $null
$tokens = $null
$runScript = Join-Path $ProjectRoot "scripts\run.ps1"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($runScript, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) {
    throw "run.ps1 syntax error: $($errors[0].Message)"
}
Write-Host "  -> Syntax OK" -ForegroundColor Green

Write-Host "[CHECK] 2. Testing clean stop idempotency..." -ForegroundColor Cyan
& $runScript -Stop
$pidFile = Join-Path $ProjectRoot ".services.pids.json"
$lockFile = Join-Path $ProjectRoot ".vinext\dev\lock.json"
if (Test-Path $pidFile) { throw "PID file was not removed by Stop" }
if (Test-Path $lockFile) { throw "Lock file was not removed by Stop" }
Write-Host "  -> Clean stop OK" -ForegroundColor Green

Write-Host "[CHECK] All orchestrator verification checks passed!" -ForegroundColor Green
