@echo off
setlocal
cd /d "%~dp0"
set OPENDV_CONFIG=%~dp0opendv.config.yaml
set OPENDV_WORKSPACE_ROOT=%~dp0

echo Starting Open-DV Platform API on http://127.0.0.1:8420 ...
echo Using config: %OPENDV_CONFIG%
echo Workspace:    %OPENDV_WORKSPACE_ROOT%

uv run --package opendv-platform-api uvicorn opendv_platform_api.main:app --reload --port 8420 %*

