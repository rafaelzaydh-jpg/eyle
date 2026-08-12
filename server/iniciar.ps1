$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Arquivo .env criado. Coloque sua DASHSCOPE_API_KEY nele e execute novamente." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path ".venv")) {
    py -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m pip install "jsonschema>=4.0"
& .\.venv\Scripts\python.exe server.py
