[CmdletBinding()]
param([switch]$IncludeIntegration)

$ErrorActionPreference = 'Stop'
$researchRoot = $PSScriptRoot
$researchPython = Join-Path $researchRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $researchPython)) {
    throw 'Missing .venv Python. Install the project development dependencies first.'
}

function Invoke-ResearchCheck {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Verification failed: $Executable (exit $LASTEXITCODE)"
    }
}

Push-Location (Join-Path $researchRoot 'services\graph-api')
try {
    Invoke-ResearchCheck $researchPython @('-m', 'ruff', 'check', 'app', 'tests')
    Invoke-ResearchCheck $researchPython @(
        '-m', 'pytest', '-m', 'not integration and not live and not sandbox', '-q'
    )
} finally {
    Pop-Location
}

Push-Location $researchRoot
try {
    Invoke-ResearchCheck (Join-Path $researchRoot 'node_modules\.bin\oxlint.cmd') @()
    Invoke-ResearchCheck (Join-Path $researchRoot 'node_modules\.bin\tsc.cmd') @(
        '--noEmit', '--incremental', 'false'
    )
    Invoke-ResearchCheck (Join-Path $researchRoot 'node_modules\.bin\vitest.cmd') @(
        'run', '--config', 'vitest.config.ts'
    )
    if ($IncludeIntegration) {
        # The existing runner creates its own isolated Neo4j Compose project.
        Invoke-ResearchCheck 'pwsh' @(
            '-NoProfile', '-File', (Join-Path $researchRoot 'test-integration.ps1')
        )
    }
} finally {
    Pop-Location
}
