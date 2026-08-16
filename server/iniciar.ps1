$ErrorActionPreference = "Stop"

# Sempre trabalha na pasta real do Adapter. Antes, abrir o .bat a partir de
# outra pasta fazia .env/.venv/requirements serem procurados no lugar errado.
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$EnvFile = Join-Path $Root ".env"
$Example = Join-Path $Root ".env.example"
$LegacyExample = Join-Path $Root "env.example"

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $Example) {
        Copy-Item $Example $EnvFile
    } elseif (Test-Path $LegacyExample) {
        Copy-Item $LegacyExample $EnvFile
    } else {
        throw "Nao encontrei .env.example para criar o .env."
    }
    Write-Host "Arquivo .env criado em: $EnvFile" -ForegroundColor Yellow
    Write-Host "Configure UPSTREAM_BASE_URL, UPSTREAM_API_KEY e DEFAULT_MODEL para a API remota desejada. Depois execute iniciar.bat novamente." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path ".venv")) {
    py -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "Adapter: http://127.0.0.1:8080  |  Diagnostico ativo: http://127.0.0.1:8080/ready" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe server.py
