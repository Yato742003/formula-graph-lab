$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$imageTag = 'fgl-performer-pilot-' + [Guid]::NewGuid().ToString('N').Substring(0, 12)
$previousImage = $env:FGL_PERFORMER_SANDBOX_IMAGE
Push-Location $repoRoot
try {
    & docker build -f services/graph-api/Dockerfile.performer.sandbox -t $imageTag services/graph-api
    if ($LASTEXITCODE -ne 0) { throw 'Performer pilot worker image build failed.' }
    $env:FGL_PERFORMER_SANDBOX_IMAGE = (& docker image inspect --format '{{.Id}}' $imageTag).Trim()
    Push-Location (Join-Path $repoRoot 'services/graph-api')
    try {
        & (Join-Path $repoRoot '.venv/Scripts/python.exe') -m app.performer_pilot
        if ($LASTEXITCODE -ne 0) { throw 'Performer synthetic diagnostic did not complete.' }
    } finally { Pop-Location }
} finally {
    $env:FGL_PERFORMER_SANDBOX_IMAGE = $previousImage
    # Remove only this script run's exact temporary tag.
    & docker image rm $imageTag
    Pop-Location
}
