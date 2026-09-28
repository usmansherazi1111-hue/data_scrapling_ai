# Starts Scrapling Studio (if needed) and a public https link via a Cloudflare quick tunnel.
# The tunnel is requested here (with a long timeout) because cloudflared's own request can time out on slow networks.
$ErrorActionPreference = 'Stop'
$app = $PSScriptRoot
$work = Join-Path $app 'data\tunnel'
New-Item -ItemType Directory -Force $work | Out-Null

# cloudflared: on PATH, or in bin\ next to the app (or one folder up).
$cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cf) { $cf = @("$app\bin\cloudflared.exe", "$app\..\bin\cloudflared.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1 }
if (-not $cf) {
    Write-Host 'cloudflared not found. Install it with:  winget install Cloudflare.cloudflared' -ForegroundColor Yellow
    Write-Host "or download cloudflared-windows-amd64.exe from https://github.com/cloudflare/cloudflared/releases and save it as $app\bin\cloudflared.exe"
    Read-Host 'Press Enter to exit'; exit 1
}

if ((Select-String -Path "$app\.env" -Pattern '^APP_PASSWORD=\S' -Quiet -ErrorAction SilentlyContinue) -ne $true) {
    Write-Host 'Set APP_PASSWORD in .env before sharing publicly.' -ForegroundColor Yellow
    Read-Host 'Press Enter to exit'; exit 1
}

if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
    Write-Host 'Starting Scrapling Studio...'
    Start-Process -FilePath "$app\.venv\Scripts\python.exe" -ArgumentList 'main.py' -WorkingDirectory $app -WindowStyle Minimized
    Start-Sleep -Seconds 6
}

Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
$r = $null
foreach ($i in 1..5) {
    try { $r = Invoke-RestMethod -Method Post -Uri 'https://api.trycloudflare.com/tunnel' -ContentType 'application/json' -TimeoutSec 60; if ($r.success) { break } } catch { Write-Host "Tunnel request failed ($i/5), retrying..."; Start-Sleep -Seconds 3 }
}
if (-not $r.success) { Write-Host 'Could not get a tunnel from Cloudflare. Try again in a few minutes.'; Read-Host 'Press Enter to exit'; exit 1 }

$t = $r.result
@{ AccountTag = $t.account_tag; TunnelSecret = $t.secret; TunnelID = $t.id } | ConvertTo-Json | Set-Content "$work\creds.json" -Encoding ascii
@"
tunnel: $($t.id)
credentials-file: $work\creds.json
protocol: http2
no-autoupdate: true
ingress:
  - service: http://127.0.0.1:8000
"@ | Set-Content "$work\config.yml" -Encoding ascii

Write-Host ''
Write-Host "  Public link:  https://$($t.hostname)" -ForegroundColor Green
Write-Host '  Password:     APP_PASSWORD in .env'
Write-Host '  Keep this window open. Closing it stops sharing.'
Write-Host ''
& $cf tunnel --config "$work\config.yml" run
