# Start Open-DV Platform API Server with runtime configuration
$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    $env:OPENDV_CONFIG = Join-Path $PSScriptRoot "opendv.config.yaml"
    $env:OPENDV_WORKSPACE_ROOT = $PSScriptRoot
    if (-not $env:OPENDV_HOST) {
        $env:OPENDV_HOST = "127.0.0.1"
    }
    if (-not $env:OPENDV_PORT) {
        $env:OPENDV_PORT = "8420"
    }

    Write-Host "Starting Open-DV Platform API on http://$($env:OPENDV_HOST):$($env:OPENDV_PORT) ..." -ForegroundColor Cyan
    Write-Host "Using config: $env:OPENDV_CONFIG" -ForegroundColor DarkGray
    Write-Host "Workspace:    $env:OPENDV_WORKSPACE_ROOT" -ForegroundColor DarkGray

    uv run --package opendv-platform-api uvicorn opendv_platform_api.main:app --host $env:OPENDV_HOST --reload --port $env:OPENDV_PORT @args
}
finally {
    Pop-Location
}

