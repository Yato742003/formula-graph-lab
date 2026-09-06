$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$composeFile = Join-Path $projectRoot 'docker-compose.test.yml'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runProject = 'fgl-test-' + [Guid]::NewGuid().ToString('N').Substring(0, 12)
$priorPassword = $env:TEST_NEO4J_PASSWORD
$priorUri = $env:TEST_NEO4J_URI
$secretBytes = New-Object byte[] 32
$generator = [Security.Cryptography.RandomNumberGenerator]::Create()
$generator.GetBytes($secretBytes)
$generator.Dispose()
$env:TEST_NEO4J_PASSWORD = [Convert]::ToHexString($secretBytes).ToLowerInvariant()
$testExit = 1
try {
    & docker compose -p $runProject -f $composeFile up -d --wait --wait-timeout 120
    if ($LASTEXITCODE -ne 0) {
        & docker compose -p $runProject -f $composeFile ps -a
        & docker compose -p $runProject -f $composeFile logs --no-color neo4j
        throw 'The isolated Neo4j test database did not become healthy.'
    }
    $testAddress = (& docker compose -p $runProject -f $composeFile port neo4j 7687).Trim()
    if ($LASTEXITCODE -ne 0 -or $testAddress -notmatch '^127\.0\.0\.1:\d+$') {
        throw 'Could not resolve the isolated database port.'
    }
    $env:TEST_NEO4J_URI = 'bolt://' + $testAddress
    Push-Location (Join-Path $projectRoot 'services\graph-api')
    try {
        & $pythonExe -m pytest -m integration --tb=short
        $testExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    # This random Compose project is owned by this run and uses only tmpfs storage.
    & docker compose -p $runProject -f $composeFile down
    $env:TEST_NEO4J_PASSWORD = $priorPassword
    $env:TEST_NEO4J_URI = $priorUri
    [Array]::Clear($secretBytes, 0, $secretBytes.Length)
}
exit $testExit
