@echo off
setlocal
pushd "%~dp0"
if not defined OPENDV_HOST set "OPENDV_HOST=127.0.0.1"
if not defined OPENDV_PORT set "OPENDV_PORT=8420"

echo Starting Open-DV V2 Server on http://%OPENDV_HOST%:%OPENDV_PORT% ...

uv run opendv serve --host "%OPENDV_HOST%" --port "%OPENDV_PORT%" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
