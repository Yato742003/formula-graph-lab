param(
    [Parameter(Mandatory = $true)]
    [string]$Bundle
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$bundlePath = (Resolve-Path -LiteralPath $Bundle -ErrorAction Stop).Path
$bundleItem = Get-Item -LiteralPath $bundlePath
if ($bundleItem.PSIsContainer) { throw 'Replay bundle must be a file.' }

$imageTag = 'fgl-replay-' + [Guid]::NewGuid().ToString('N').Substring(0, 12)
$exitCode = 1
Push-Location $projectRoot
try {
    & docker build -f services/graph-api/Dockerfile -t $imageTag services/graph-api
    if ($LASTEXITCODE -ne 0) { throw 'Replay image build failed.' }

    & docker run --rm --network none --read-only --cap-drop ALL `
        --security-opt no-new-privileges --memory 256m --cpus 1 --pids-limit 64 `
        --user 65534:65534 --mount "type=bind,source=$bundlePath,target=/bundle.json,readonly" `
        $imageTag python -m app.replay_bundle_cli /bundle.json
    $exitCode = $LASTEXITCODE
} finally {
    & docker image rm $imageTag *> $null
    Pop-Location
}
exit $exitCode
