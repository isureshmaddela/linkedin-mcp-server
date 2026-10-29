# Old name of start_monosage.ps1, kept so existing shortcuts keep working.
param(
    [int]$Port = 8765,
    [switch]$OpenBrowser,
    [string]$AccountUrl = ""
)
& (Join-Path $PSScriptRoot "start_monosage.ps1") @PSBoundParameters
