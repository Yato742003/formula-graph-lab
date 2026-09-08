<#
.SYNOPSIS
    FormulaGraph Lab - Service Runner and Orchestrator
.DESCRIPTION
    Khoi chay toan bo he sinh thai FormulaGraph Lab bao gom:
      - Database: Neo4j (Docker Compose)
      - Backend:  Python FastAPI / Graph API (Uvicorn voi Hot-reload)
      - Frontend: Vinext / React 19 RSC (Vite voi HMR)
.PARAMETER Stop
    Dung toan bo cac tien trinh Frontend, Backend va Neo4j container.
.PARAMETER NoDocker
    Bo qua khoi dong Docker/Neo4j (chay FE va BE o che do Offline/Demo ma khong bi crash).
.PARAMETER Restart
    Khoi dong lai toan bo cac dich vu tu dau.
.EXAMPLE
    .\run.ps1
.EXAMPLE
    .\run.ps1 -Stop
.EXAMPLE
    .\run.ps1 -NoDocker
#>

[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$NoDocker,
    [switch]$Restart
)

$ErrorActionPreference = "Continue"
$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }
$PidFile = Join-Path $ProjectRoot ".services.pids.json"
$EnvFile = Join-Path $ProjectRoot ".env"
$VinextLock = Join-Path $ProjectRoot ".vinext\dev\lock.json"
$LogDir = Join-Path $ProjectRoot ".logs"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function New-SecureHex {
    param([int]$ByteCount = 32)
    $bytes = [byte[]]::new($ByteCount)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

# --- CONSOLE OUTPUT HELPERS ---
function Write-Header {
    param([string]$Text)
    Write-Host "`n========================================================" -ForegroundColor DarkCyan
    Write-Host "  $Text" -ForegroundColor Cyan
    Write-Host "========================================================" -ForegroundColor DarkCyan
}

function Write-Step {
    param([string]$Step, [string]$Message)
    Write-Host "[$Step] " -ForegroundColor Yellow -NoNewline
    Write-Host $Message -ForegroundColor White
}

function Write-Success {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-WarningMsg {
    param([string]$Message)
    Write-Host "  [!] $Message" -ForegroundColor DarkYellow
}

function Write-ErrorMsg {
    param([string]$Message)
    Write-Host "  [X] $Message" -ForegroundColor Red
}

# --- PROCESS MANAGEMENT HELPERS ---
function Kill-ProcessTree {
    param([int]$TargetPid)
    if ($TargetPid -gt 4) {
        try {
            taskkill /PID $TargetPid /T /F 2>&1 | Out-Null
        } catch {
            Stop-Process -Id $TargetPid -Force -ErrorAction SilentlyContinue
        }
    }
}

function Free-PortProcesses {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        $processId = $conn.OwningProcess
        if ($processId -gt 4 -and $processId -ne $PID) {
            Write-Step "KILL" "Giai phong port $Port (PID: $processId)..."
            Kill-ProcessTree -TargetPid $processId
        }
    }
}

# --- RELIABLE PORT & HEALTH CHECK HELPERS (IPv4 + IPv6 Dual-stack) ---
function Test-PortListening {
    param([int]$Port, [int]$TimeoutMs = 700)
    # 1. Kiem tra qua NetTCPConnection (nhanh va ho tro ca IPv4/IPv6 listen sockets)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        return $true
    }

    # 2. Thu ket noi truc tiep qua ca IPv4 loopback va IPv6 loopback
    $endpoints = @(
        [System.Net.IPAddress]::Loopback,
        [System.Net.IPAddress]::IPv6Loopback
    )
    foreach ($addr in $endpoints) {
        try {
            $client = [System.Net.Sockets.TcpClient]::new($addr.AddressFamily)
            $asyncResult = $client.BeginConnect($addr, $Port, $null, $null)
            if ($asyncResult.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
                $client.EndConnect($asyncResult)
                $client.Close()
                return $true
            }
            $client.Close()
        } catch { }
    }
    return $false
}

function Wait-ForPort {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 35,
        [string]$ServiceName = "Service"
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Host -NoNewline "  Dang cho $ServiceName san sang tren port $Port"
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        if (Test-PortListening -Port $Port) {
            Write-Host " [San sang!]" -ForegroundColor Green
            return $true
        }
        Write-Host -NoNewline "."
        Start-Sleep -Milliseconds 800
    }
    Write-Host " [Het thoi gian cho]" -ForegroundColor Red
    return $false
}

function Wait-ForHttp {
    param(
        [string[]]$Urls,
        [int]$TimeoutSeconds = 25,
        [string]$ServiceName = "Service"
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Host -NoNewline "  Kiem tra HTTP health check $ServiceName"
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        foreach ($url in $Urls) {
            try {
                $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
                if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
                    Write-Host " [San sang!]" -ForegroundColor Green
                    return $true
                }
            } catch { }
        }
        Write-Host -NoNewline "."
        Start-Sleep -Milliseconds 800
    }
    Write-Host " [Chua phan hoi]" -ForegroundColor Yellow
    return $false
}

function Show-RecentLog {
    param([string]$FilePath, [int]$LineCount = 12)
    if (Test-Path $FilePath) {
        Write-Host "`n  --- Chi tiet log gan nhat ($FilePath) ---" -ForegroundColor DarkGray
        Get-Content $FilePath -Tail $LineCount -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host "    $_" -ForegroundColor Gray
        }
        Write-Host "  --------------------------------------------------`n" -ForegroundColor DarkGray
    }
}

# --- STOP ALL SERVICES ---
function Stop-AllServices {
    Write-Header "DANG DUNG CAC DICH VU FORMULAGRAPH LAB"

    # 1. Dung tien trinh FE & BE tu file PID
    if (Test-Path $PidFile) {
        try {
            $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
            if ($pids.BackendPid) {
                Write-Step "BE" "Dung tien trinh Backend (PID: $($pids.BackendPid))..."
                Kill-ProcessTree -TargetPid $pids.BackendPid
            }
            if ($pids.FrontendPid) {
                Write-Step "FE" "Dung tien trinh Frontend (PID: $($pids.FrontendPid))..."
                Kill-ProcessTree -TargetPid $pids.FrontendPid
            }
        } catch { }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    # 2. Giai phong port 8000 va 3000
    Free-PortProcesses -Port 8000
    Free-PortProcesses -Port 3000

    # 3. Don dep file khoa Vinext
    if (Test-Path $VinextLock) {
        Remove-Item $VinextLock -Force -ErrorAction SilentlyContinue
        Write-Step "CLEAN" "Da xoa file khoa Vinext stale lock."
    }

    # 4. Dung Docker Neo4j neu duoc yeu cau
    Write-Step "DB" "Dung container Neo4j..."
    try {
        Push-Location $ProjectRoot
        docker compose stop neo4j 2>&1 | Out-Null
        Pop-Location
        Write-Success "Neo4j container da duoc tam dung an toan."
    } catch {
        Write-WarningMsg "Khong the goi docker compose hoac container chua chay."
    }

    Write-Success "Toan bo cac dich vu da duoc tat sach se."
}

if ($Stop) {
    Stop-AllServices
    exit 0
}

if ($Restart) {
    Stop-AllServices
    Start-Sleep -Seconds 2
}

# --- BANNER ---
Write-Host @"
  ======================================================
     ____                           _       ____                 _
    |  _ \ ___  _ __ _ __ ___  _   _| | __ _|  _ \ __ _ _ __  __| |___
    | |_) / _ \| '__| '_ ` _ \| | | | |/ _` | |_) / _` | '_ \/ _` / __|
    |  __/ (_) | |  | | | | | | |_| | | (_| |  __/ (_| | |_) \__, \__ \
    |_|   \___/|_|  |_| |_| |_|\__,_|_|\__,_|_|   \__,_| .__/|___/|___/
                                                       |_|
                FORMULAGRAPH LAB - LOCAL RUNNER
  ======================================================
"@ -ForegroundColor Cyan

# --- 1. KIEM TRA VA KHOI TAO CAU HINH .ENV ---
Write-Header "1. Kiem tra cau hinh moi truong (.env)"

$envNeedsInit = $false
if (-not (Test-Path $EnvFile)) {
    $envNeedsInit = $true
} else {
    $envContent = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
    if ([string]::IsNullOrWhiteSpace($envContent) -or ($envContent -match "replace-with-")) {
        $envNeedsInit = $true
    }
}

if ($envNeedsInit) {
    Write-Step "ENV" "Khoi tao file .env moi voi cac token bao mat ngau nhien..."
    $randomToken = "fg_token_" + (New-SecureHex -ByteCount 32)
    $cursorSecret = "fg_cursor_" + (New-SecureHex -ByteCount 32)
    $neo4jPassword = "fg_neo4j_" + (New-SecureHex -ByteCount 24)
    $devEnvContent = @"
# Web application
GRAPH_API_URL=http://localhost:8000
GRAPH_API_SERVICE_TOKEN=$randomToken

# Python Graph API
APP_ENV=development
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=$neo4jPassword
OPENAI_API_KEY=
SERVICE_TOKEN=$randomToken
SEARCH_CURSOR_SECRET=$cursorSecret
"@
    Set-Content -Path $EnvFile -Value $devEnvContent -Encoding UTF8
    Write-Success "Da tao file .env hoan chinh."
} else {
    # Kiem tra bo sung cac bien con thieu
    $envContent = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
    if ($envContent -and -not ($envContent -match "SEARCH_CURSOR_SECRET")) {
        $cursorSecret = "fg_cursor_" + (New-SecureHex -ByteCount 32)
        Add-Content -Path $EnvFile -Value "`nSEARCH_CURSOR_SECRET=$cursorSecret" -Encoding UTF8
        Write-Step "ENV" "Bo sung SEARCH_CURSOR_SECRET vao file .env."
    }
    Write-Success "File .env da san sang."
}

# Doc bien moi truong tu file .env vao session hien tai
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $idx = $line.IndexOf("=")
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()
        [System.Environment]::SetEnvironmentVariable($key, $val, [System.EnvironmentVariableTarget]::Process)
    }
}

# --- 2. KIEM TRA TIEN ICH HE THONG ---
Write-Header "2. Kiem tra cac cong cu phan mem (Prerequisites)"

# Kiem tra Node & npm
try {
    $nodeVersion = node --version 2>&1
    $npmVersion = npm --version 2>&1
    Write-Success "Node.js $nodeVersion | npm $npmVersion"
} catch {
    Write-ErrorMsg "Node.js hoac npm chua duoc cai dat tren he thong!"
    exit 1
}

# Kiem tra node_modules
if (-not (Test-Path (Join-Path $ProjectRoot "node_modules"))) {
    Write-Step "NPM" "Chua co thu muc node_modules. Dang chay 'npm install'..."
    Push-Location $ProjectRoot
    npm install
    Pop-Location
    Write-Success "npm install hoan tat."
} else {
    Write-Success "Frontend dependencies (node_modules) da san sang."
}

# Kiem tra Python va .venv
if (-not (Test-Path $VenvPython)) {
    Write-Step "PYTHON" "Chua tim thay .venv. Dang tao virtual environment..."
    python -m venv (Join-Path $ProjectRoot ".venv")
    Write-Step "PYTHON" "Cai dat Backend dependencies (FastAPI, Graphiti, lxml)..."
    & $VenvPython -m pip install -e "$ProjectRoot\services\graph-api[dev]"
    Write-Success ".venv duoc tao va cai dat thanh cong."
} else {
    Write-Success "Python Backend Virtualenv (.venv) da san sang."
}

# --- 3. KHOI DONG DATABASE (NEO4J) ---
Write-Header "3. Khoi dong Database (Neo4j Community 5.26)"

$dockerRunning = $false
$neo4jReady = $false

if (-not $NoDocker) {
    # Kiem tra Docker Daemon
    try {
        $null = docker info 2>&1
        if ($LASTEXITCODE -eq 0) {
            $dockerRunning = $true
        }
    } catch { }

    if (-not $dockerRunning) {
        Write-WarningMsg "Docker Desktop daemon hien chua chay."
        $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
        if (Test-Path $dockerExe) {
            Write-Step "DOCKER" "Dang tu dong mo Docker Desktop..."
            Start-Process $dockerExe -WindowStyle Hidden
            Write-Host -NoNewline "  Dang cho Docker khoi dong"
            $maxWait = 40
            $waited = 0
            while ($waited -lt $maxWait) {
                Start-Sleep -Seconds 2
                $waited += 2
                Write-Host -NoNewline "."
                try {
                    $null = docker info 2>&1
                    if ($LASTEXITCODE -eq 0) {
                        $dockerRunning = $true
                        Write-Host " [Docker da chay!]" -ForegroundColor Green
                        break
                    }
                } catch { }
            }
            if (-not $dockerRunning) {
                Write-Host ""
                Write-WarningMsg "Docker chua khoi dong kip trong $maxWait giay."
            }
        }
    }

    if ($dockerRunning) {
        Write-Step "DOCKER" "Kiem tra container Neo4j qua Docker Compose..."
        Push-Location $ProjectRoot
        docker compose up -d neo4j 2>&1 | Out-Null
        Pop-Location

        $neo4jReady = Wait-ForPort -Port 7687 -TimeoutSeconds 35 -ServiceName "Neo4j Bolt (7687)"
        $null = Wait-ForPort -Port 7474 -TimeoutSeconds 10 -ServiceName "Neo4j Browser (7474)"
        if ($neo4jReady) {
            Write-Success "Neo4j Database da san sang phuc vu!"
        } else {
            Write-WarningMsg "Neo4j chua phan hoi port 7687 kip thoi. Backend se khoi dong o che do phu hop."
        }
    } else {
        Write-WarningMsg "Khong ket noi duoc Docker. Chuyen sang che do Mock/Offline Graph (khong gay crash Backend)."
    }
} else {
    Write-WarningMsg "Che do -NoDocker duoc bat: Bo qua khoi dong Neo4j."
}

# --- 4. KHOI DONG BACKEND (FASTAPI) ---
Write-Header "4. Khoi dong Backend (FastAPI / Graph API)"

# Giai phong port 8000 neu bi chiem
Free-PortProcesses -Port 8000
Start-Sleep -Milliseconds 600

# Neu Neo4j khong chay, tat bien NEO4J_URI de FastAPI khong bi crash luc startup
$neo4jEnvOverride = ""
if (-not $neo4jReady) {
    Write-Step "CONFIG" "Neo4j chua san sang. Thiet lap Backend chay offline (tranh loi ket noi gay tat app)..."
    $neo4jEnvOverride = "`$env:NEO4J_URI = ''"
}

$beWindowCmd = @"
Set-Location '$ProjectRoot'
$neo4jEnvOverride
& '$VenvPython' -m uvicorn app.main:app --app-dir "$ProjectRoot\services\graph-api" --reload --host 127.0.0.1 --port 8000 --env-file "$ProjectRoot\.env"
"@

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$beOutLog = Join-Path $LogDir "backend.stdout.log"
$beErrLog = Join-Path $LogDir "backend.stderr.log"

$beProc = Start-Process powershell.exe -ArgumentList "-NoProfile", "-NonInteractive", "-Command", $beWindowCmd -PassThru -WindowStyle Hidden -RedirectStandardOutput $beOutLog -RedirectStandardError $beErrLog
Write-Success "Backend chay an (PID: $($beProc.Id)); log: .logs\backend.*.log"

$beHealthUrls = @("http://127.0.0.1:8000/health", "http://localhost:8000/health")
$beReady = Wait-ForHttp -Urls $beHealthUrls -TimeoutSeconds 25 -ServiceName "Backend API"
if (-not $beReady) {
    Write-ErrorMsg "Backend API chua phan hoi tren port 8000."
    Show-RecentLog -FilePath $beErrLog -LineCount 15
} else {
    Write-Success "Backend API da san sang phuc vu tai http://127.0.0.1:8000"
}

# --- 5. KHOI DONG FRONTEND (VINEXT / REACT 19) ---
Write-Header "5. Khoi dong Frontend (Vinext / React 19 RSC)"

# Giai phong port 3000 neu dang bi chiem
Free-PortProcesses -Port 3000
Start-Sleep -Milliseconds 600

# Xoa stale lock file cua Vinext de tranh loi trung PID
if (Test-Path $VinextLock) {
    Remove-Item $VinextLock -Force -ErrorAction SilentlyContinue
    Write-Step "LOCK" "Da xoa file khoa cu .vinext/dev/lock.json."
}

$feWindowCmd = @"
Set-Location '$ProjectRoot'
if (Test-Path '.vinext\dev\lock.json') { Remove-Item '.vinext\dev\lock.json' -Force -ErrorAction SilentlyContinue }
npm run dev
"@

$feOutLog = Join-Path $LogDir "frontend.stdout.log"
$feErrLog = Join-Path $LogDir "frontend.stderr.log"

$feProc = Start-Process powershell.exe -ArgumentList "-NoProfile", "-NonInteractive", "-Command", $feWindowCmd -PassThru -WindowStyle Hidden -RedirectStandardOutput $feOutLog -RedirectStandardError $feErrLog
Write-Success "Frontend chay an (PID: $($feProc.Id)); log: .logs\frontend.*.log"

$feReady = Wait-ForPort -Port 3000 -TimeoutSeconds 45 -ServiceName "Frontend UI (Port 3000)"
if (-not $feReady) {
    Write-ErrorMsg "Frontend UI chua mo port 3000."
    Show-RecentLog -FilePath $feErrLog -LineCount 10
    Show-RecentLog -FilePath $feOutLog -LineCount 10
} else {
    Write-Success "Frontend UI da san sang tai http://localhost:3000"
}

# Luu thong tin PID de Stop
$pidData = @{
    BackendPid = $beProc.Id
    FrontendPid = $feProc.Id
    StartedAt = (Get-Date).ToString("o")
} | ConvertTo-Json
Set-Content -Path $PidFile -Value $pidData -Encoding UTF8

# --- 6. DASHBOARD TRUY CAP ---
Write-Header "TONG HOP CAC DICH VU FORMULAGRAPH LAB"

Write-Host "  +-------------------------------------------------------------------------+" -ForegroundColor DarkGreen
Write-Host "  | SERVICE            | URL / DIA CHI                   | TRANG THAI       |" -ForegroundColor DarkGreen
Write-Host "  +-------------------------------------------------------------------------+" -ForegroundColor DarkGreen
if ($feReady) {
Write-Host "  | Frontend UI        | http://localhost:3000           | [Running]        |" -ForegroundColor Green
} else {
Write-Host "  | Frontend UI        | http://localhost:3000           | [Failed/Starting]|" -ForegroundColor Red
}
if ($beReady) {
Write-Host "  | Backend API        | http://localhost:8000           | [Running]        |" -ForegroundColor Cyan
Write-Host "  | API Swagger Docs   | http://localhost:8000/docs      | [Running]        |" -ForegroundColor Cyan
} else {
Write-Host "  | Backend API        | http://localhost:8000           | [Failed/Starting]|" -ForegroundColor Red
}
if ($neo4jReady) {
Write-Host "  | Neo4j Web Browser  | http://localhost:7474           | [Running]        |" -ForegroundColor Yellow
Write-Host "  | Neo4j Bolt Port    | bolt://localhost:7687           | [Running]        |" -ForegroundColor Yellow
} else {
Write-Host "  | Neo4j Graph DB     | (Chua bat / Che do Offline FE)  | [Skipped]        |" -ForegroundColor DarkGray
}
Write-Host "  +-------------------------------------------------------------------------+" -ForegroundColor DarkGreen
Write-Host ""
Write-Host "  [TIP] Thong tin nhay cam duoc tao ngau nhien trong file .env (khong commit)." -ForegroundColor Gray
Write-Host "  [TIP] Frontend va Backend chay nen; xem log trong thu muc .logs." -ForegroundColor Gray
Write-Host ""

# --- 7. RESILIENT MONITOR LOOP ---
Write-Host "==========================================================================" -ForegroundColor DarkCyan
Write-Host "  He thong dang giam sat cac tien trinh nen (Nhan Ctrl+C de dung):" -ForegroundColor White
Write-Host "    [O] : Mo Frontend tren trinh duyet" -ForegroundColor Yellow
Write-Host "    [D] : Mo API Documentation (Swagger Docs)" -ForegroundColor Cyan
Write-Host "    [N] : Mo Neo4j Browser" -ForegroundColor Magenta
Write-Host "    [Q] : DUNG TOAN BO cac dich vu (FE, BE, DB) va thoat" -ForegroundColor Red
Write-Host "==========================================================================" -ForegroundColor DarkCyan
Write-Host ""

# Kiem tra xem console co ho tro doc phim tu ban phim khong (tranh crash trong IDE/Non-interactive shell)
$canReadKey = $false
try {
    $canReadKey = [System.Console]::KeyAvailable -ne $null
} catch {
    $canReadKey = $false
}

$lastHealthCheck = [System.Diagnostics.Stopwatch]::StartNew()

while ($true) {
    # 1. Xu ly ban phim neu co ho tro console tuong tac
    if ($canReadKey) {
        try {
            if ([System.Console]::KeyAvailable) {
                $key = [System.Console]::ReadKey($true).Key
                switch ($key) {
                    "O" {
                        Write-Host "Dang mo Frontend tren trinh duyet..." -ForegroundColor Green
                        Start-Process "http://localhost:3000"
                    }
                    "D" {
                        Write-Host "Dang mo Swagger API Docs..." -ForegroundColor Cyan
                        Start-Process "http://localhost:8000/docs"
                    }
                    "N" {
                        Write-Host "Dang mo Neo4j Browser..." -ForegroundColor Yellow
                        Start-Process "http://localhost:7474"
                    }
                    "Q" {
                        Write-Host "`nBan da yeu cau dung tat ca dich vu..." -ForegroundColor Red
                        Stop-AllServices
                        exit 0
                    }
                }
            }
        } catch {
            $canReadKey = $false
        }
    }

    # 2. Dinh ky kiem tra xem process Backend va Frontend co bi crash bat thuong khong
    if ($lastHealthCheck.Elapsed.TotalSeconds -gt 5) {
        $lastHealthCheck.Restart()
        if ($beProc -and $beProc.HasExited) {
            Write-ErrorMsg "Backend API dot ngot dung! (ExitCode: $($beProc.ExitCode))"
            Show-RecentLog -FilePath $beErrLog -LineCount 10
        }
        if ($feProc -and $feProc.HasExited) {
            Write-ErrorMsg "Frontend UI dot ngot dung! (ExitCode: $($feProc.ExitCode))"
            Show-RecentLog -FilePath $feErrLog -LineCount 10
        }
    }

    Start-Sleep -Milliseconds 400
}
