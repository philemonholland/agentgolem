@echo off
title AgentGolem Memory Graph Visualiser
cd /d "%~dp0"
echo Starting Memory Graph Visualiser...
.venv\Scripts\python.exe tools\memory_visualizer.py %*
if errorlevel 1 (
    echo.
    echo Error starting visualiser. Make sure .venv is set up.
    pause
)
