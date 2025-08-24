@echo off
REM Use a custom data folder for logs and snapshots (adjust the path below).
setlocal
set "BIRDFINGERS_DATA_DIR=%~dp0BirdfingersData"
if not exist "%BIRDFINGERS_DATA_DIR%" mkdir "%BIRDFINGERS_DATA_DIR%"
call "%~dp0birdfingers.bat" %*
endlocal
