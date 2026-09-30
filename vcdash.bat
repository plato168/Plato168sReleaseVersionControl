@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 管理上一層目錄（含所有子目錄）的應用程式
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set PY=python
where python >nul 2>nul || set PY=py
%PY% vcdash.py scan --report --root "%ROOT%" %*
if errorlevel 1 goto end
start "" "%ROOT%\vcdash-report\index.html"
:end
pause
