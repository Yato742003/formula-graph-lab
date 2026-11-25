[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$crateDir = Join-Path $root 'crates\formula-layout-wasm'
$outDir = Join-Path $root 'public\wasm'
$outFile = Join-Path $outDir 'formula-layout.wasm'

Push-Location $crateDir
try {
  cargo test --quiet
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  cargo build --target wasm32-unknown-unknown --release
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
  Pop-Location
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$built = Join-Path $crateDir 'target\wasm32-unknown-unknown\release\formula_layout_wasm.wasm'
Copy-Item -Path $built -Destination $outFile -Force
$sizeKb = [Math]::Round((Get-Item $outFile).Length / 1KB, 2)
Write-Host "Built $outFile ($sizeKb KB)"
