@echo off
title AgentGolem Benchmarks
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo [ERROR] Virtual environment not found.
    echo Run:  py -3.12 -m venv .venv
    echo Then: .venv\Scripts\activate ^&^& pip install -e ".[dev]"
    pause
    exit /b 1
)

echo.
if "%~1"=="" (
    echo Running benchmark suite directory...
    python -m agentgolem.benchmarks benchmarks --output data\benchmarks\latest_run.json --interpret
) else (
    echo Running benchmark target: %~1
    python -m agentgolem.benchmarks %* --interpret
)

if errorlevel 1 (
    echo.
    echo [Benchmark run failed]
    pause
    exit /b 1
)

echo.
echo [Benchmark run complete]
pause
