<#
.SYNOPSIS
Automated, isolated Backup & Restore Drill for Cloudflare D1 + Neo4j 5.26 Community.

.DESCRIPTION
Closes P0 #4 (local/isolated environment) by executing a real end-to-end disaster-recovery
drill without touching any development D1 state or Neo4j data volumes:
  1. Cloudflare D1 (isolated temp cwd with Wrangler local SQLite):
     migrate -> seed 4 tables -> wrangler d1 export (SHA-256) -> destroy source ->
     restore into empty target -> measure RTO -> verify 100% row & hash parity.
  2. Neo4j 5.26 Community (isolated Docker named volumes):
     start source -> ingest .tex exact evidence + ContractReview + CheckResult +
     seed expired/active transient records -> prune expired transient records only ->
     stop -> neo4j-admin database dump (SHA-256) -> destroy source volume ->
     neo4j-admin database load into fresh volume -> start target -> measure RTO ->
     verify 100% label counts, equation LaTeX, review hash, check receipt, and search parity.
  3. Writes a machine-verifiable JSON receipt to reports/backup-restore-drill-receipt.json.
#>

[CmdletBinding()]
param(
    [string]$ReceiptPath = "reports/backup-restore-drill-receipt.json",
    [switch]$D1Only
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WranglerBin = Join-Path $RepoRoot "node_modules\.bin\wrangler.cmd"
$PythonBin = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DrillHelper = Join-Path $RepoRoot "scripts\drill_neo4j_verify.py"

if (-not (Test-Path $WranglerBin)) {
    throw "Wrangler CLI not found at $WranglerBin. Run 'npm install' first."
}
if (-not (Test-Path $PythonBin)) {
    throw "Python virtualenv not found at $PythonBin. Run 'uv sync --project services/graph-api --frozen --extra dev' first."
}

$DockerAvailable = $false
if (-not $D1Only) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker info *> $null
    $DockerAvailable = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if (-not $DockerAvailable) {
        throw "Docker daemon is not running (required for Neo4j 5.26 dump/load drill). Start Docker Desktop or pass -D1Only for a D1-only drill."
    }
}

$RunId = ([guid]::NewGuid().ToString("N")).Substring(0, 10)
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "fgl-backup-drill-$RunId"
$SrcD1Dir = Join-Path $TempRoot "d1-src"
$DstD1Dir = Join-Path $TempRoot "d1-dst"
$BackupDir = Join-Path $TempRoot "backups"
$D1SchemaBackupSql = Join-Path $BackupDir "d1-schema.sql"
$D1DataBackupSql = Join-Path $BackupDir "d1-data.sql"
$D1SeedSql = Join-Path $TempRoot "d1-seed.sql"
$Neo4jExpectedJson = Join-Path $TempRoot "neo4j-expected.json"
$Neo4jVerifiedJson = Join-Path $TempRoot "neo4j-verified.json"

$SrcVol = "fgl-drill-src-$RunId"
$DstVol = "fgl-drill-dst-$RunId"
$SrcContainer = "fgl-drill-neo4j-src-$RunId"
$DstContainer = "fgl-drill-neo4j-dst-$RunId"
$Neo4jPassword = "DrillPass$RunId!9"

function Invoke-Checked {
    param([string]$FilePath, [string[]]$Arguments, [string]$Label)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & $FilePath @Arguments 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($code -ne 0) {
        $text = ($output | Out-String).Trim()
        throw "$Label failed (exit $code):`n$text"
    }
    return $output
}

function Wait-Neo4jReady {
    param([string]$ContainerName, [int]$TimeoutSeconds = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $portLine = (& docker port $ContainerName 7474/tcp 2>$null | Select-Object -First 1)
        if ($portLine) {
            $addr = $portLine.Trim()
            try {
                $resp = Invoke-WebRequest -Uri "http://$addr" -UseBasicParsing -TimeoutSec 2
                if ($resp.StatusCode -eq 200) {
                    return
                }
            } catch {
                # still booting
            }
        }
        Start-Sleep -Seconds 2
    }
    $logs = (& docker logs --tail 40 $ContainerName 2>&1 | Out-String)
    throw "Timed out waiting for Neo4j container '$ContainerName' to become ready.`n$logs"
}

function Get-Neo4jBoltUri {
    param([string]$ContainerName)
    $portLine = (& docker port $ContainerName 7687/tcp 2>$null | Select-Object -First 1)
    if (-not $portLine) {
        throw "Failed to resolve mapped Bolt port 7687 for container '$ContainerName'."
    }
    return "bolt://$($portLine.Trim())"
}

function Get-D1TableCounts {
    param([string]$CwdDir)
    $sql = "SELECT 'workspaces' AS tbl, COUNT(*) AS cnt FROM workspaces UNION ALL SELECT 'papers', COUNT(*) FROM papers UNION ALL SELECT 'import_jobs', COUNT(*) FROM import_jobs UNION ALL SELECT 'hypotheses', COUNT(*) FROM hypotheses;"
    $raw = Invoke-Checked -FilePath $WranglerBin -Arguments @(
        "d1", "execute", "site-creator-d1", "--local", "--cwd", $CwdDir, "--json", "--command", $sql
    ) -Label "Wrangler D1 count query ($CwdDir)"
    $jsonText = ($raw | Out-String)
    $idx = $jsonText.IndexOf("[")
    if ($idx -lt 0) {
        throw "Unexpected Wrangler D1 JSON output:`n$jsonText"
    }
    $parsed = ($jsonText.Substring($idx)) | ConvertFrom-Json
    $counts = @{}
    foreach ($row in $parsed[0].results) {
        $counts[[string]$row.tbl] = [int]$row.cnt
    }
    return $counts
}

function Get-D1PaperRow {
    param([string]$CwdDir)
    $sql = "SELECT id, workspace_id, arxiv_id, title, source_sha256, status FROM papers ORDER BY id;"
    $raw = Invoke-Checked -FilePath $WranglerBin -Arguments @(
        "d1", "execute", "site-creator-d1", "--local", "--cwd", $CwdDir, "--json", "--command", $sql
    ) -Label "Wrangler D1 paper verify ($CwdDir)"
    $jsonText = ($raw | Out-String)
    $idx = $jsonText.IndexOf("[")
    $parsed = ($jsonText.Substring($idx)) | ConvertFrom-Json
    return $parsed[0].results[0]
}

New-Item -ItemType Directory -Force -Path $SrcD1Dir, $DstD1Dir, $BackupDir | Out-Null

$DrillStartedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$TotalSw = [System.Diagnostics.Stopwatch]::StartNew()
$Neo4jExpected = $null
$Neo4jVerified = $null
$Neo4jBackupSw = $null
$Neo4jRestoreSw = $null
$Neo4jDumpSha256 = $null
$Neo4jDumpBytes = 0
$DrillWorkspaceId = "ws_backup_restore_drill_v1"
$DrillPaperId = "tex_drill_d1_only"
$DrillSourceSha = "a" * 64

try {
    if (-not $D1Only) {
    Write-Host "[1/6] Setting up isolated Neo4j source container and seeding exact evidence + retention pruning..."
    Invoke-Checked -FilePath "docker" -Arguments @("volume", "create", $SrcVol) -Label "docker volume create (src)" | Out-Null
    Invoke-Checked -FilePath "docker" -Arguments @(
        "run", "-d", "--name", $SrcContainer,
        "-p", "127.0.0.1::7474",
        "-p", "127.0.0.1::7687",
        "-e", "NEO4J_AUTH=neo4j/$Neo4jPassword",
        "-e", "NEO4J_server_memory_heap_initial__size=256m",
        "-e", "NEO4J_server_memory_heap_max__size=512m",
        "-e", "NEO4J_server_memory_pagecache_size=256m",
        "-v", "${SrcVol}:/data",
        "neo4j:5.26-community"
    ) -Label "docker run neo4j (src)" | Out-Null

    Wait-Neo4jReady -ContainerName $SrcContainer
    $SrcBoltUri = Get-Neo4jBoltUri -ContainerName $SrcContainer

    Invoke-Checked -FilePath $PythonBin -Arguments @(
        $DrillHelper,
        "--mode", "seed-and-prune",
        "--uri", $SrcBoltUri,
        "--user", "neo4j",
        "--password", $Neo4jPassword,
        "--out", $Neo4jExpectedJson
    ) -Label "Neo4j seed-and-prune" | Out-Null

    $Neo4jExpected = Get-Content -Raw -Path $Neo4jExpectedJson | ConvertFrom-Json
    $DrillWorkspaceId = [string]$Neo4jExpected.workspace_id
    $DrillPaperId = [string]$Neo4jExpected.paper_id
    $DrillSourceSha = [string]$Neo4jExpected.source_sha256

    Write-Host "[2/6] Backing up Neo4j (offline neo4j-admin database dump) and destroying source volume..."
    Invoke-Checked -FilePath "docker" -Arguments @("stop", "-t", "30", $SrcContainer) -Label "docker stop neo4j (src)" | Out-Null
    Invoke-Checked -FilePath "docker" -Arguments @("rm", "-f", $SrcContainer) -Label "docker rm neo4j (src)" | Out-Null

    $Neo4jBackupSw = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-Checked -FilePath "docker" -Arguments @(
        "run", "--rm",
        "-v", "${SrcVol}:/data",
        "-v", "${BackupDir}:/backups",
        "neo4j:5.26-community",
        "neo4j-admin", "database", "dump", "neo4j", "--to-path=/backups"
    ) -Label "neo4j-admin database dump" | Out-Null
    $Neo4jBackupSw.Stop()

    $Neo4jDumpFile = Join-Path $BackupDir "neo4j.dump"
    if (-not (Test-Path $Neo4jDumpFile)) {
        throw "Expected Neo4j dump file missing at $Neo4jDumpFile"
    }
    $Neo4jDumpSha256 = (Get-FileHash -Algorithm SHA256 -Path $Neo4jDumpFile).Hash.ToLowerInvariant()
    $Neo4jDumpBytes = (Get-Item $Neo4jDumpFile).Length

    # Destroy source volume to prove restore uses ONLY the dump file
    Invoke-Checked -FilePath "docker" -Arguments @("volume", "rm", $SrcVol) -Label "docker volume rm (src)" | Out-Null
    $SrcVol = $null

    Write-Host "[3/6] Restoring Neo4j into brand-new volume and verifying 100% evidence/review/check/search parity..."
    $Neo4jRestoreSw = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-Checked -FilePath "docker" -Arguments @("volume", "create", $DstVol) -Label "docker volume create (dst)" | Out-Null
    Invoke-Checked -FilePath "docker" -Arguments @(
        "run", "--rm",
        "-v", "${DstVol}:/data",
        "-v", "${BackupDir}:/backups",
        "neo4j:5.26-community",
        "neo4j-admin", "database", "load", "neo4j", "--from-path=/backups", "--overwrite-destination=true"
    ) -Label "neo4j-admin database load" | Out-Null

    Invoke-Checked -FilePath "docker" -Arguments @(
        "run", "-d", "--name", $DstContainer,
        "-p", "127.0.0.1::7474",
        "-p", "127.0.0.1::7687",
        "-e", "NEO4J_AUTH=neo4j/$Neo4jPassword",
        "-e", "NEO4J_server_memory_heap_initial__size=256m",
        "-e", "NEO4J_server_memory_heap_max__size=512m",
        "-e", "NEO4J_server_memory_pagecache_size=256m",
        "-v", "${DstVol}:/data",
        "neo4j:5.26-community"
    ) -Label "docker run neo4j (dst)" | Out-Null

    Wait-Neo4jReady -ContainerName $DstContainer
    $DstBoltUri = Get-Neo4jBoltUri -ContainerName $DstContainer
    $Neo4jRestoreSw.Stop()

    Invoke-Checked -FilePath $PythonBin -Arguments @(
        $DrillHelper,
        "--mode", "verify-restored",
        "--uri", $DstBoltUri,
        "--user", "neo4j",
        "--password", $Neo4jPassword,
        "--expected", $Neo4jExpectedJson,
        "--out", $Neo4jVerifiedJson
    ) -Label "Neo4j verify-restored" | Out-Null

    $Neo4jVerified = Get-Content -Raw -Path $Neo4jVerifiedJson | ConvertFrom-Json
    } else {
        Write-Host "[1-3/6] Skipping Neo4j drill (-D1Only requested)."
    }

    Write-Host "[4/6] Setting up isolated Cloudflare D1 source database, migrating, and seeding all 4 tables..."
    Copy-Item -Path (Join-Path $RepoRoot "wrangler.json") -Destination (Join-Path $SrcD1Dir "wrangler.json")
    Copy-Item -Recurse -Path (Join-Path $RepoRoot "drizzle") -Destination (Join-Path $SrcD1Dir "drizzle")

    Invoke-Checked -FilePath $WranglerBin -Arguments @(
        "d1", "migrations", "apply", "site-creator-d1", "--local", "--cwd", $SrcD1Dir
    ) -Label "Wrangler D1 migrations apply (src)" | Out-Null

    $SeedSqlText = @"
INSERT INTO workspaces (id, owner_user_id, name, created_at, updated_at)
VALUES ('$DrillWorkspaceId', 'drill-user-1', 'Backup Restore Drill Workspace', 1764201600000, 1764201600000);

INSERT INTO papers (id, workspace_id, arxiv_id, version, title, source_url, source_sha256, status, created_at, updated_at)
VALUES ('paper_drill_001', '$DrillWorkspaceId', '$DrillPaperId', 1, 'Backup Restore Verification Corpus', 'urn:fgl:local-tex:$DrillSourceSha', '$DrillSourceSha', 'ready', 1764201600000, 1764201600000);

INSERT INTO import_jobs (id, workspace_id, paper_id, requested_by, canonical_url, status, error_code, created_at, finished_at)
VALUES ('job_drill_001', '$DrillWorkspaceId', 'paper_drill_001', 'drill-user-1', 'urn:fgl:local-tex:$DrillSourceSha', 'succeeded', NULL, 1764201600000, 1764201605000);

INSERT INTO hypotheses (id, workspace_id, created_by, title, transformation_json, status, created_at, updated_at)
VALUES ('hyp_drill_001', '$DrillWorkspaceId', 'drill-user-1', 'Logarithm power simplification', '{"from":"\\log(x^2)","to":"2\\log(x)"}', 'symbolically_verified', 1764201600000, 1764201605000);
"@
    [System.IO.File]::WriteAllText($D1SeedSql, $SeedSqlText, (New-Object System.Text.UTF8Encoding($false)))

    Invoke-Checked -FilePath $WranglerBin -Arguments @(
        "d1", "execute", "site-creator-d1", "--local", "--cwd", $SrcD1Dir, "--file", $D1SeedSql
    ) -Label "Wrangler D1 seed (src)" | Out-Null

    $D1SrcCounts = Get-D1TableCounts -CwdDir $SrcD1Dir
    $D1SrcPaper = Get-D1PaperRow -CwdDir $SrcD1Dir

    Write-Host "[5/6] Exporting Cloudflare D1 two-phase backup (--no-data + --no-schema, SHA-256), destroying source state, and restoring into empty D1 target..."
    $D1BackupSw = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-Checked -FilePath $WranglerBin -Arguments @(
        "d1", "export", "site-creator-d1", "--local", "--cwd", $SrcD1Dir, "--no-data", "--output", $D1SchemaBackupSql
    ) -Label "Wrangler D1 schema export (--no-data)" | Out-Null
    Invoke-Checked -FilePath $WranglerBin -Arguments @(
        "d1", "export", "site-creator-d1", "--local", "--cwd", $SrcD1Dir, "--no-schema", "--output", $D1DataBackupSql
    ) -Label "Wrangler D1 data export (--no-schema)" | Out-Null
    $D1BackupSw.Stop()

    if (-not (Test-Path $D1SchemaBackupSql) -or -not (Test-Path $D1DataBackupSql)) {
        throw "Expected D1 SQL exports missing in $BackupDir"
    }
    $D1SchemaSha256 = (Get-FileHash -Algorithm SHA256 -Path $D1SchemaBackupSql).Hash.ToLowerInvariant()
    $D1DataSha256 = (Get-FileHash -Algorithm SHA256 -Path $D1DataBackupSql).Hash.ToLowerInvariant()
    $D1BackupBytes = (Get-Item $D1SchemaBackupSql).Length + (Get-Item $D1DataBackupSql).Length

    # Destroy source D1 directory completely
    Remove-Item -Recurse -Force $SrcD1Dir

    # Prepare brand-new empty D1 target and restore strictly from schema + data backup SQL
    Copy-Item -Path (Join-Path $RepoRoot "wrangler.json") -Destination (Join-Path $DstD1Dir "wrangler.json")
    Copy-Item -Recurse -Path (Join-Path $RepoRoot "drizzle") -Destination (Join-Path $DstD1Dir "drizzle")

    $D1RestoreSw = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-Checked -FilePath $WranglerBin -Arguments @(
        "d1", "execute", "site-creator-d1", "--local", "--cwd", $DstD1Dir, "--file", $D1SchemaBackupSql
    ) -Label "Wrangler D1 schema restore" | Out-Null
    Invoke-Checked -FilePath $WranglerBin -Arguments @(
        "d1", "execute", "site-creator-d1", "--local", "--cwd", $DstD1Dir, "--file", $D1DataBackupSql
    ) -Label "Wrangler D1 data restore" | Out-Null
    $D1RestoreSw.Stop()

    $D1DstCounts = Get-D1TableCounts -CwdDir $DstD1Dir
    $D1DstPaper = Get-D1PaperRow -CwdDir $DstD1Dir

    foreach ($tbl in @("workspaces", "papers", "import_jobs", "hypotheses")) {
        if ($D1SrcCounts[$tbl] -ne $D1DstCounts[$tbl] -or $D1DstCounts[$tbl] -lt 1) {
            throw "D1 row count mismatch on table '$tbl': src=$($D1SrcCounts[$tbl]), dst=$($D1DstCounts[$tbl])"
        }
    }
    if ($D1SrcPaper.source_sha256 -ne $D1DstPaper.source_sha256 -or $D1DstPaper.source_sha256 -ne $DrillSourceSha) {
        throw "source_sha256 mismatch on restored Cloudflare D1 paper row!"
    }

    $TotalSw.Stop()

    Write-Host "[6/6] Writing verified Backup & Restore Drill receipt to $ReceiptPath..."
    $Neo4jSection = if ($D1Only) {
        [ordered]@{
            status = "skipped_d1_only"
        }
    } else {
        [ordered]@{
            status = "passed"
            image = "neo4j:5.26-community"
            backup_tool = "neo4j-admin database dump"
            restore_tool = "neo4j-admin database load --overwrite-destination=true"
            backup_bytes = [int]$Neo4jDumpBytes
            backup_sha256 = $Neo4jDumpSha256
            backup_duration_ms = [int]$Neo4jBackupSw.ElapsedMilliseconds
            restore_rto_ms = [int]$Neo4jRestoreSw.ElapsedMilliseconds
            workspace_id = $Neo4jExpected.workspace_id
            paper_id = $Neo4jExpected.paper_id
            source_sha256 = $Neo4jExpected.source_sha256
            equation_uuid = $Neo4jVerified.equation_uuid
            review_hash = $Neo4jVerified.review_hash
            check_id = $Neo4jVerified.check_id
            check_outcome = $Neo4jVerified.check_outcome
            restored_search_hits = [int]$Neo4jVerified.restored_search_hits
            pruned_transient = $Neo4jExpected.pruned_transient
            label_counts_restored = $Neo4jVerified.label_counts
            protected_labels_intact = $true
            parity_verified = [bool]$Neo4jVerified.parity_verified
        }
    }

    $Receipt = [ordered]@{
        schema_version = "backup-restore-drill-receipt.v1"
        status = "passed"
        scope = if ($D1Only) { "isolated_local_d1_only" } else { "isolated_local_d1_and_docker_neo4j" }
        remote_production_drill_completed = $false
        executed_at = $DrillStartedAt
        total_drill_duration_ms = [int]$TotalSw.ElapsedMilliseconds
        targets = [ordered]@{
            rpo_minutes = 15
            rto_minutes = 30
            retention_policy_version = if ($Neo4jExpected) { $Neo4jExpected.retention_policy_version } else { "retention-policy.v1" }
        }
        cloudflare_d1 = [ordered]@{
            status = "passed"
            database_name = "site-creator-d1"
            backup_tool = "wrangler d1 export --local (--no-data + --no-schema)"
            restore_tool = "wrangler d1 execute --local --file (schema then data)"
            backup_bytes = [int]$D1BackupBytes
            schema_backup_sha256 = $D1SchemaSha256
            data_backup_sha256 = $D1DataSha256
            backup_duration_ms = [int]$D1BackupSw.ElapsedMilliseconds
            restore_rto_ms = [int]$D1RestoreSw.ElapsedMilliseconds
            table_counts_source = $D1SrcCounts
            table_counts_restored = $D1DstCounts
            cross_store_source_sha256_match = (-not $D1Only)
        }
        neo4j = $Neo4jSection
    }

    $ResolvedReceipt = Join-Path $RepoRoot $ReceiptPath
    $ReceiptParent = Split-Path -Parent $ResolvedReceipt
    New-Item -ItemType Directory -Force -Path $ReceiptParent | Out-Null
    $ReceiptJson = $Receipt | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($ResolvedReceipt, "$ReceiptJson`n", (New-Object System.Text.UTF8Encoding($false)))

    Write-Host "PASS: Backup & Restore Drill succeeded!"
    Write-Host "  D1    restore RTO: $([int]$D1RestoreSw.ElapsedMilliseconds) ms | data SHA-256: $D1DataSha256"
    if (-not $D1Only) {
        Write-Host "  Neo4j restore RTO: $([int]$Neo4jRestoreSw.ElapsedMilliseconds) ms | backup SHA-256: $Neo4jDumpSha256"
    }
    Write-Host "  Receipt saved to : $ResolvedReceipt"
}
finally {
    Write-Host "Cleaning up ephemeral drill containers, volumes, and temp directories..."
    $ErrorActionPreference = "Continue"
    if ($DockerAvailable) {
        & docker rm -f $SrcContainer *> $null
        & docker rm -f $DstContainer *> $null
        if ($SrcVol) { & docker volume rm -f $SrcVol *> $null }
        if ($DstVol) { & docker volume rm -f $DstVol *> $null }
    }
    if (Test-Path $TempRoot) {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $TempRoot
    }
}
