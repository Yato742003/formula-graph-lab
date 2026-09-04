<#
.SYNOPSIS
    FormulaGraph Lab - Stop All Services
.DESCRIPTION
    Tắt toàn bộ dịch vụ FE, BE và DB nhanh chóng.
#>
$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }
& (Join-Path $ProjectRoot "run.ps1") -Stop
