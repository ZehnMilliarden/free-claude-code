@echo off
cd /d "%~dp0"
echo Starting Claude Code Proxy server...
uv run uvicorn server:app --host 127.0.0.1 --port 9999
pause
