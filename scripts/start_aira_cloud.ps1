# Old name of start_monosage_cloud.ps1, kept so existing shortcuts keep working.
param(
    [int]$Port = 8780,
    [string]$DataDir = "",
    [switch]$SeedDemo,
    [switch]$OpenBrowser,
    [string]$ImportLocal = ""
)
& (Join-Path $PSScriptRoot "start_monosage_cloud.ps1") @PSBoundParameters
