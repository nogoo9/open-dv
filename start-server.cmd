@echo off
setlocal
pushd "%~dp0"
set "OPENDV_CONFIG=%~dp0opendv.config.yaml"
set "OPENDV_WORKSPACE_ROOT=%~dp0"
if not defined OPENDV_HOST set "OPENDV_HOST=127.0.0.1"
if not defined OPENDV_PORT set "OPENDV_PORT=8420"

echo Starting Open-DV Platform API on http://%OPENDV_HOST%:%OPENDV_PORT% ...
echo Using config: "%OPENDV_CONFIG%"
echo Workspace:    "%OPENDV_WORKSPACE_ROOT%"

uv run --package opendv-platform-api uvicorn opendv_platform_api.main:app --host "%OPENDV_HOST%" --reload --port "%OPENDV_PORT%" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%

