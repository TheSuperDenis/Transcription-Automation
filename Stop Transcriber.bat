@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Stop-Transcriber.ps1"
set "TRANSCRIBER_EXIT=%ERRORLEVEL%"
if not "%TRANSCRIBER_EXIT%"=="0" pause
exit /b %TRANSCRIBER_EXIT%
