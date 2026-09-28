$ErrorActionPreference = 'Stop'
$sandboxRoot = $PSScriptRoot
$sandboxTag = 'fgl-sandbox-test-' + [Guid]::NewGuid().ToString('N').Substring(0, 12)
$previousImage = $env:TEST_SANDBOX_IMAGE
$previousProbe = $env:TEST_SANDBOX_PROBE_IMAGE
$previousCleanReplay = $env:TEST_CLEAN_REPLAY
Push-Location $sandboxRoot
try {
    & docker build -f services/graph-api/Dockerfile.sandbox -t $sandboxTag services/graph-api
    if ($LASTEXITCODE -ne 0) { throw 'Sandbox image build failed.' }
    $env:TEST_SANDBOX_IMAGE = (& docker image inspect --format '{{.Id}}' $sandboxTag).Trim()
    & docker build -f services/graph-api/tests/Dockerfile.sandbox-probe --build-arg "WORKER_IMAGE=$sandboxTag" -t "$sandboxTag-probe" services/graph-api
    if ($LASTEXITCODE -ne 0) { throw 'Probe image build failed.' }
    $env:TEST_SANDBOX_PROBE_IMAGE = (& docker image inspect --format '{{.Id}}' "$sandboxTag-probe").Trim()
    Push-Location (Join-Path $sandboxRoot 'services/graph-api')
    try {
        & (Join-Path $sandboxRoot '.venv/Scripts/python.exe') -m pytest -m sandbox --tb=short
        if ($LASTEXITCODE -ne 0) { throw 'Sandbox release gate failed.' }
        $env:TEST_CLEAN_REPLAY = '1'
        & (Join-Path $sandboxRoot '.venv/Scripts/python.exe') -m pytest `
            'tests/test_research_compiler.py::test_source_authorized_compiler_bundle_replays_and_binds_provenance' `
            --tb=short
        if ($LASTEXITCODE -ne 0) { throw 'Clean-container replay gate failed.' }
    } finally { Pop-Location }
} finally {
    $env:TEST_SANDBOX_IMAGE = $previousImage
    $env:TEST_SANDBOX_PROBE_IMAGE = $previousProbe
    $env:TEST_CLEAN_REPLAY = $previousCleanReplay
    # Only ephemeral tags created by this run; never prune shared image data.
    & docker image rm "$sandboxTag-probe" $sandboxTag
    Pop-Location
}
