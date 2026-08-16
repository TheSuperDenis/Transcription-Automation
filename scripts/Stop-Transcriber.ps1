[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $ProjectRoot 'compose.yaml'

try {
    Set-Location -LiteralPath $ProjectRoot

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker was not found.'
    }

    Write-Host '[Transcriber] Stopping local containers...' -ForegroundColor Cyan
    & docker compose --file $ComposeFile --profile cpu --profile gpu down --remove-orphans
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE."
    }

    Write-Host '[Transcriber] Stopped. Transcripts and downloaded models were preserved.' -ForegroundColor Green
}
catch {
    Write-Host "[Transcriber] ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
