<#
.SYNOPSIS
  Fire a test error_spike event so you can check Discord and email alerts end to end.
  With -Delivery, skip triage and send one test page straight to the alert sinks.
.EXAMPLE
  $env:ADMIN_TOKEN = "<ADMIN_TOKEN>"
  ./scripts/test_alert.ps1 -BaseUrl https://<project>.vercel.app
.EXAMPLE
  ./scripts/test_alert.ps1 -BaseUrl https://<project>.vercel.app -Delivery
#>
param(
    [string]$BaseUrl = $(if ($env:PUBLIC_URL) { $env:PUBLIC_URL } else { "http://localhost:7860" }),
    [string]$Token = $env:ADMIN_TOKEN,
    [string]$Detail = "manual test from scripts/test_alert.ps1",
    [switch]$Delivery
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8  # summaries contain dashes and arrows
if (-not $Token) { Write-Error "Set ADMIN_TOKEN (or pass -Token)."; exit 1 }
$base = $BaseUrl.TrimEnd('/')
$headers = @{ Authorization = "Bearer $Token" }

if ($Delivery) {
    $r = Invoke-RestMethod -Method Post -Uri "$base/api/alerts/test" -Headers $headers -TimeoutSec 60
    $discord = if ($r.webhook) { "sent" } else { "failed" }
    $email = if (-not $r.email_configured) { "not configured" } elseif ($r.email) { "sent" } else { "failed" }
    Write-Host "Discord: $discord  Email: $email"
    exit 0
}

$body = @{
    kind    = "error_spike"
    detail  = $Detail
    metrics = @{ error_rate = 0.5; requests_5m = 40 }
} | ConvertTo-Json -Depth 3

$resp = Invoke-RestMethod -Method Post -Uri "$base/api/events" `
    -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec 120

Write-Host "Decision: $($resp.action) by $($resp.decided_by) (confidence $($resp.confidence))"
Write-Host "Summary:  $($resp.summary)"
if ($resp.incident_id) {
    Write-Host "Incident: $base/incidents/$($resp.incident_id)"
    Write-Host "Resolve:  POST /api/incidents/$($resp.incident_id)/resolve"
}
Write-Host "Now check the Discord channel and ALERT_EMAIL inbox."
