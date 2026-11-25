# Start Open-DV V2 Platform API & Web Workbench Server
$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    if (-not $env:OPENDV_HOST) {
        $env:OPENDV_HOST = "127.0.0.1"
    }
    if (-not $env:OPENDV_PORT) {
        $env:OPENDV_PORT = "8420"
    }

    Write-Host "Starting Open-DV V2 Server on http://$($env:OPENDV_HOST):$($env:OPENDV_PORT) ..." -ForegroundColor Cyan

    uv run opendv serve --host $env:OPENDV_HOST --port $env:OPENDV_PORT @args
}
finally {
    Pop-Location
}
