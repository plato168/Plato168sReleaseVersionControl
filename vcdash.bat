@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% vcdash.py scan --report %*
if errorlevel 1 goto end
start "" "vcdash-report\index.html"
:end
pause
