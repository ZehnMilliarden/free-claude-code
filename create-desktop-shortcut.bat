@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create-desktop-shortcut.ps1"
if exist "%USERPROFILE%\Desktop\Claude Code Client.lnk" (
    echo Shortcut created on desktop: "Claude Code Client.lnk"
) else (
    echo Failed to create shortcut.
)
