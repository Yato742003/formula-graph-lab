$ErrorActionPreference = 'Stop'
$phase5Tag = 'fgl-phase5-' + [Guid]::NewGuid().ToString('N').Substring(0, 12)
$previousPhase5Image = $env:TEST_SANDBOX_IMAGE
$previousPhase5Replay = $env:TEST_PHASE5_CLEAN_REPLAY
try {
    & docker build -f "$PSScriptRoot/services/graph-api/Dockerfile.sandbox" -t $phase5Tag "$PSScriptRoot/services/graph-api"
    if ($LASTEXITCODE -ne 0) { throw 'Phase 5 sandbox build failed.' }
    $env:TEST_SANDBOX_IMAGE = (& docker image inspect --format '{{.Id}}' $phase5Tag).Trim()
    $env:TEST_PHASE5_CLEAN_REPLAY = '1'
    & pwsh -NoProfile -File "$PSScriptRoot/test-integration.ps1" -ShowOutput
    if ($LASTEXITCODE -ne 0) { throw 'Phase 5 acceptance failed.' }
} finally {
    $env:TEST_SANDBOX_IMAGE = $previousPhase5Image
    $env:TEST_PHASE5_CLEAN_REPLAY = $previousPhase5Replay
    # Only the ephemeral tag owned by this run; no shared data is pruned.
    & docker image rm $phase5Tag
}
