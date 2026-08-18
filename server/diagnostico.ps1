$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Base = "http://127.0.0.1:8080"
Write-Host "=== Eyle Adapter: health ===" -ForegroundColor Cyan
try {
    Invoke-RestMethod "$Base/health" | ConvertTo-Json -Depth 8
} catch {
    Write-Host "Adapter nao respondeu em $Base" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

Write-Host "`n=== Eyle Adapter: local readiness ===" -ForegroundColor Cyan
try {
    Invoke-RestMethod "$Base/ready" | ConvertTo-Json -Depth 8
} catch {
    Write-Host "Adapter esta online, mas a configuracao local nao esta pronta." -ForegroundColor Red
    if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message -ForegroundColor Yellow }
    else { Write-Host $_.Exception.Message -ForegroundColor Yellow }
    exit 2
}

Write-Host "`nA conectividade remota e comprovada somente por uma chamada real /chat/completions." -ForegroundColor DarkGray
