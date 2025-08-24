@echo off
REM Birdfingers launcher. Usage:
REM   birdfingers.bat          -> opens on port 8765
REM   birdfingers.bat 8888     -> opens on port 8888
setlocal
set PY="%~dp0python.exe"
set APP="%~dp0birdfingers_pkgmgr.py"
if not exist %PY% (echo python.exe not found. && exit /b 1)
if not exist %APP% (echo birdfingers_pkgmgr.py not found. && exit /b 1)
set PORT=%1
if "%PORT%"=="" set PORT=8765
%PY% %APP% --port %PORT%
endlocal
