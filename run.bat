@echo off
cd /d "%~dp0"
title ST v5
where uv >nul 2>nul
if %errorlevel%==0 (
  uv run --with fastapi --with uvicorn --with yfinance python server.py
) else (
  python server.py
)
pause
