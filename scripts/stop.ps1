<#
.SYNOPSIS
    FormulaGraph Lab - Stop All Services
.DESCRIPTION
    Tắt toàn bộ dịch vụ FE, BE và DB nhanh chóng.
#>
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Join-Path (Get-Location).Path "scripts" }
& (Join-Path $ScriptDir "run.ps1") -Stop
