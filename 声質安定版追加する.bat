@echo off
setlocal
set "PSModulePath="
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0installer-source\scripts\add-stable.ps1" (
  echo Please run this BAT inside the installed folder shown by setup.
  pause
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer-source\scripts\add-stable.ps1" -InstallRoot "%~dp0." %*
if errorlevel 1 (
  echo Stable mode was not added. Your normal installation is retained.
  pause
  exit /b 1
)
pause
