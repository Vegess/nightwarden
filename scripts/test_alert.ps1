<#
.SYNOPSIS
  Fire a test error_spike event so you can check Discord and email alerts end to end.
.EXAMPLE
  $env:ADMIN_TOKEN = "<ADMIN_TOKEN>"
  ./scripts/test_alert.ps1 -BaseUrl https://<project>.vercel.app
#>
param(
    [string]$BaseUrl = $(if ($env:PUBLIC_URL) { $env:PUBLIC_URL } else { "http://localhost:7860" }),
    [string]$Token = $env:ADMIN_TOKEN,
    [string]$Detail = "manual test from scripts/test_alert.ps1"
)
$ErrorActionPreference = "Stop"
if (-not $Token) { Write-Error "Set ADMIN_TOKEN (or pass -Token)."; exit 1 }

$body = @{
    kind    = "error_spike"
    detail  = $Detail
    metrics = @{ error_rate = 0.5; requests_5m = 40 }
} | ConvertTo-Json -Depth 3

$resp = Invoke-RestMethod -Method Post -Uri "$($BaseUrl.TrimEnd('/'))/api/events" `
    -Headers @{ Authorization = "Bearer $Token" } -ContentType "application/json" -Body $body -TimeoutSec 120

Write-Host "Decision: $($resp.action) by $($resp.decided_by) (confidence $($resp.confidence))"
Write-Host "Summary:  $($resp.summary)"
if ($resp.incident_id) {
    Write-Host "Incident: $($BaseUrl.TrimEnd('/'))/incidents/$($resp.incident_id)"
    Write-Host "Resolve:  POST /api/incidents/$($resp.incident_id)/resolve"
}
Write-Host "Now check the Discord channel and ALERT_EMAIL inbox."
