@echo off
title AgentGolem
cd /d "%~dp0"

:: Activate virtual environment and run
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    python run_golem.py
) else (
    echo [ERROR] Virtual environment not found.
    echo Run:  py -3.12 -m venv .venv
    echo Then: .venv\Scripts\activate ^&^& pip install -e ".[dev]"
    pause
)
