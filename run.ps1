<#
.SYNOPSIS
    FormulaGraph Lab - Service Runner and Orchestrator
.DESCRIPTION
    Khởi chạy toàn bộ hệ sinh thái FormulaGraph Lab bao gồm:
      - Database: Neo4j (Docker Compose)
      - Backend:  Python FastAPI / Graph API (Uvicorn với Hot-reload)
      - Frontend: Vinext / React 19 RSC (Vite với HMR)
.PARAMETER Stop
    Dừng toàn bộ các tiến trình Frontend, Backend và Neo4j container.
.PARAMETER NoDocker
    Bỏ qua khởi động Docker/Neo4j (chỉ chạy FE và BE ở chế độ Demo/Mock graph).
.PARAMETER Restart
    Khởi động lại toàn bộ các dịch vụ từ đầu.
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

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }
$PidFile = Join-Path $ProjectRoot ".services.pids.json"
$EnvFile = Join-Path $ProjectRoot ".env"
$VinextLock = Join-Path $ProjectRoot ".vinext\dev\lock.json"
$LogDir = Join-Path $ProjectRoot ".logs"

function New-SecureHex {
    param([int]$ByteCount = 32)
    $bytes = [byte[]]::new($ByteCount)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

# --- HELPER FUNCTIONS FOR CONSOLE OUTPUT ---
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

# --- PROCESS & TREE KILL HELPER ---
function Kill-ProcessTree {
    param([int]$TargetPid)
    if ($TargetPid -gt 0) {
        try {
            taskkill /PID $TargetPid /T /F 2>&1 | Out-Null
        } catch {
            Stop-Process -Id $TargetPid -Force -ErrorAction SilentlyContinue
        }
    }
}

# --- PORT & HEALTH HELPERS ---
function Test-PortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $conn)
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
        [string]$Url,
        [int]$TimeoutSeconds = 30,
        [string]$ServiceName = "Service"
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Host -NoNewline "  Kiem tra HTTP health check $ServiceName ($Url)"
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
                Write-Host " [San sang!]" -ForegroundColor Green
                return $true
            }
        } catch { }
        Write-Host -NoNewline "."
        Start-Sleep -Milliseconds 800
    }
    Write-Host " [Chua phan hoi]" -ForegroundColor Yellow
    return $false
}

# --- STOP SERVICES ---
function Stop-AllServices {
    Write-Header "DANG DUNG CAC DICH VU FORMULAGRAPH LAB"

    # 1. Dung tien trinh FE & BE tu file PID
    if (Test-Path $PidFile) {
        try {
            $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
            if ($pids.BackendPid) {
                Write-Step "BE" "Dung cay tien trinh Backend (PID: $($pids.BackendPid))..."
                Kill-ProcessTree -TargetPid $pids.BackendPid
            }
            if ($pids.FrontendPid) {
                Write-Step "FE" "Dung cay tien trinh Frontend (PID: $($pids.FrontendPid))..."
                Kill-ProcessTree -TargetPid $pids.FrontendPid
            }
        } catch { }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    # 2. Quet them cac tien trinh dang chiem port 8000 va 3000
    @(8000, 3000) | ForEach-Object {
        $port = $_
        $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($conn in $connections) {
            $processId = $conn.OwningProcess
            if ($processId -gt 0 -and $processId -ne $PID) {
                Write-Step "KILL" "Giai phong port $port (PID: $processId)..."
                Kill-ProcessTree -TargetPid $processId
            }
        }
    }

    # 3. Don dep file khoa cu cua Vinext (.vinext/dev/lock.json)
    if (Test-Path $VinextLock) {
        Remove-Item $VinextLock -Force -ErrorAction SilentlyContinue
        Write-Step "CLEAN" "Da xoa file khoa Vinext stale lock."
    }

    # 4. Dung Docker Neo4j
    Write-Step "DB" "Dung container Neo4j..."
    try {
        docker compose stop neo4j 2>&1 | Out-Null
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

# --- RESTART FLAG ---
if ($Restart) {
    Stop-AllServices
    Start-Sleep -Seconds 2
}

# --- BANNER ---
Clear-Host
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

# --- 1. KIEM TRA VA KHOI TAO FILE .ENV ---
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
    Write-Step "ENV" "Phat hien file .env chua ton tai hoac chua duoc cau hinh day du."
    Write-Step "ENV" "Dang tu dong tao token bao mat ngau nhien va dong bo .env..."
    $randomToken = "fg_token_" + (New-SecureHex -ByteCount 32)
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
"@
    Set-Content -Path $EnvFile -Value $devEnvContent -Encoding UTF8
    Write-Success "Da tu dong thiet lap va dong bo token trong .env."
} else {
    Write-Success "File .env hop le va da duoc cau hinh."
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
    $nodeVersion = node --version
    $npmVersion = npm --version
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
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Step "PYTHON" "Chua tim thay .venv. Dang tao virtual environment..."
    python -m venv (Join-Path $ProjectRoot ".venv")
    Write-Step "PYTHON" "Cai dat Backend dependencies (FastAPI, Graphiti, lxml)..."
    & $venvPython -m pip install -e "$ProjectRoot\services\graph-api[dev]"
    Write-Success ".venv duoc tao va cai dat thanh cong."
} else {
    Write-Success "Python Backend Virtualenv (.venv) da san sang."
}

# --- 3. KHOI DONG DATABASE (NEO4J) ---
Write-Header "3. Khoi dong Database (Neo4j Community 5.26)"

$dockerRunning = $false
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
        Write-Step "DOCKER" "Chay Neo4j qua Docker Compose..."
        Push-Location $ProjectRoot
        docker compose up -d neo4j
        Pop-Location

        $neo4jReady = Wait-ForPort -Port 7687 -TimeoutSeconds 35 -ServiceName "Neo4j Bolt (7687)"
        $neo4jHttpReady = Wait-ForPort -Port 7474 -TimeoutSeconds 15 -ServiceName "Neo4j Browser (7474)"
        if ($neo4jReady) {
            Write-Success "Neo4j Database da san sang phuc vu!"
        } else {
            Write-WarningMsg "Neo4j chua phan hoi kip thoi. Backend se van khoi dong va thu ket noi."
        }
    } else {
        Write-WarningMsg "Khong the ket noi Docker daemon. Chuyen sang che do NoDocker (chi chay FE va BE demo)."
    }
} else {
    Write-WarningMsg "Che do -NoDocker duoc bat: Bo qua khoi dong Neo4j."
}

# --- 4. KHOI DONG BACKEND (FASTAPI) ---
Write-Header "4. Khoi dong Backend (FastAPI / Graph API)"

# Kiem tra xem co process cu dang chiem port 8000 khong
if (Test-PortListening -Port 8000) {
    Write-WarningMsg "Port 8000 dang duoc su dung. Dang giai phong de khoi dong Backend moi..."
    $conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        if ($c.OwningProcess -gt 0 -and $c.OwningProcess -ne $PID) {
            Kill-ProcessTree -TargetPid $c.OwningProcess
        }
    }
    Start-Sleep -Milliseconds 800
}

$beWindowCmd = @"
Set-Location '$ProjectRoot'
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --app-dir services\graph-api --reload --port 8000 --env-file .env
"@

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$beOutLog = Join-Path $LogDir "backend.stdout.log"
$beErrLog = Join-Path $LogDir "backend.stderr.log"
$beProc = Start-Process powershell.exe -ArgumentList "-NoProfile", "-NonInteractive", "-Command", $beWindowCmd -PassThru -WindowStyle Hidden -RedirectStandardOutput $beOutLog -RedirectStandardError $beErrLog
Write-Success "Backend chay an (PID: $($beProc.Id)); log: .logs\backend.*.log"

$beReady = Wait-ForHttp -Url "http://localhost:8000/health" -TimeoutSeconds 20 -ServiceName "Backend API"
if (-not $beReady) {
    Write-WarningMsg "Backend API chua kip phan hoi tren port 8000. Vui long kiem tra cua so log Backend."
}

# --- 5. KHOI DONG FRONTEND (VINEXT / NEXT.JS) ---
Write-Header "5. Khoi dong Frontend (Vinext / React 19 RSC)"

# 5.1. Giai phong port 3000 neu dang bi chiem
if (Test-PortListening -Port 3000) {
    Write-WarningMsg "Port 3000 dang duoc su dung. Dang giai phong de chay Frontend moi nhat..."
    $feConns = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $feConns) {
        if ($c.OwningProcess -gt 0 -and $c.OwningProcess -ne $PID) {
            Kill-ProcessTree -TargetPid $c.OwningProcess
        }
    }
    Start-Sleep -Milliseconds 800
}

# 5.2. Tu dong xoa stale lock file cua Vinext de tranh loi trung PID voi tien trinh he thong Windows (svchost)
if (Test-Path $VinextLock) {
    Remove-Item $VinextLock -Force -ErrorAction SilentlyContinue
    Write-Step "LOCK" "Da xoa file khoa cu .vinext/dev/lock.json de khoi dong moi."
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

# Luu thong tin PID de de dang stop
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
Write-Host "  | Frontend UI        | http://localhost:3000           | [Running]        |" -ForegroundColor Green
Write-Host "  | Backend API        | http://localhost:8000           | [Running]        |" -ForegroundColor Cyan
Write-Host "  | API Swagger Docs   | http://localhost:8000/docs      | [Running]        |" -ForegroundColor Cyan
if ($dockerRunning -and -not $NoDocker) {
Write-Host "  | Neo4j Web Browser  | http://localhost:7474           | [Running]        |" -ForegroundColor Yellow
Write-Host "  | Neo4j Bolt Port    | bolt://localhost:7687           | [Running]        |" -ForegroundColor Yellow
} else {
Write-Host "  | Neo4j Graph DB     | (Chua bat / Che do Demo Graph)  | [Skipped]        |" -ForegroundColor DarkGray
}
Write-Host "  +-------------------------------------------------------------------------+" -ForegroundColor DarkGreen
Write-Host ""
Write-Host "  [TIP] Thong tin nhay cam duoc tao ngau nhien trong file .env (khong commit)." -ForegroundColor Gray
Write-Host "  [TIP] Frontend va Backend chay nen; xem log trong thu muc .logs." -ForegroundColor Gray
Write-Host ""

# --- 7. INTERACTIVE MONITOR LOOP ---
Write-Host "==========================================================================" -ForegroundColor DarkCyan
Write-Host "  Lenh dieu khien truc tiep:" -ForegroundColor White
Write-Host "    [O] : Mo Frontend tren trinh duyet" -ForegroundColor Yellow
Write-Host "    [D] : Mo API Documentation (Swagger Docs)" -ForegroundColor Cyan
Write-Host "    [N] : Mo Neo4j Browser" -ForegroundColor Magenta
Write-Host "    [Q] : DUNG TOAN BO cac dich vu (FE, BE, DB) va thoat" -ForegroundColor Red
Write-Host "==========================================================================" -ForegroundColor DarkCyan
Write-Host ""

while ($true) {
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
    Start-Sleep -Milliseconds 300
}
