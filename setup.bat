@echo off
setlocal
rem Do not inherit PowerShell 7 modules into Windows PowerShell 5.1.
set "PSModulePath="
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
if errorlevel 1 (
  echo.
  echo Setup stopped. Read the message above. Run setup.bat again to resume.
  pause
)
