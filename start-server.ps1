# Start Open-DV Platform API Server with runtime configuration
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$env:OPENDV_CONFIG = "$scriptDir\opendv.config.yaml"
$env:OPENDV_WORKSPACE_ROOT = "$scriptDir"

Write-Host "Starting Open-DV Platform API on http://127.0.0.1:8420 ..." -ForegroundColor Cyan
Write-Host "Using config: $env:OPENDV_CONFIG" -ForegroundColor DarkGray
Write-Host "Workspace:    $env:OPENDV_WORKSPACE_ROOT" -ForegroundColor DarkGray

uv run --package opendv-platform-api uvicorn opendv_platform_api.main:app --reload --port 8420 @args

