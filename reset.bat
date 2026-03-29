@echo off
title AgentGolem — Reset Agents
cd /d "%~dp0"

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║      AgentGolem — Reset Agents           ║
echo  ╚══════════════════════════════════════════╝
echo.
echo  This will wipe agent state so they start fresh.
echo  soul.md files are preserved (agent personality).
echo.
echo  Choose reset level:
echo.
echo    1. Full reset  — clear memory, reading progress, logs, heartbeats
echo    2. Soft reset  — clear progress ^& logs, KEEP memory graphs
echo    3. Cancel
echo.
set /p CHOICE="  Your choice [1/2/3]: "

if "%CHOICE%"=="3" goto :cancel
if "%CHOICE%"=="2" goto :soft
if "%CHOICE%"=="1" goto :full
echo  Invalid choice.
pause
goto :eof

:full
echo.
echo  ⚠  FULL RESET — all agent data will be erased (except soul.md)
set /p CONFIRM="  Are you sure? [y/N]: "
if /i not "%CONFIRM%"=="y" goto :cancel

echo.
echo  Resetting agents...

for %%A in (council_1 council_2 council_3 council_4 council_5 council_6) do (
    echo    Resetting %%A...
    del /q "data\%%A\state\runtime_state.json" 2>nul
    del /q "data\%%A\niscalajyoti_reading.json" 2>nul
    del /q "data\%%A\heartbeat.md" 2>nul
    del /q "data\%%A\settings_overrides.yaml" 2>nul
    del /q "data\%%A\logs\audit.jsonl" 2>nul
    del /q "data\%%A\memory\graph.db" 2>nul
    del /q "data\%%A\memory\graph.db-shm" 2>nul
    del /q "data\%%A\memory\graph.db-wal" 2>nul
    for %%F in ("data\%%A\soul_versions\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
    for %%F in ("data\%%A\heartbeat_history\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
    for %%F in ("data\%%A\inbox\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
    for %%F in ("data\%%A\outbox\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
    for %%F in ("data\%%A\approvals\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
)

echo    Resetting shared state...
del /q "data\state\launcher_state.json" 2>nul
del /q "data\logs\activity.jsonl" 2>nul
for %%F in ("data\evolution_proposals\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
for %%F in ("data\memory\snapshots\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul

echo.
echo  ✓ Full reset complete. All 6 agents will start fresh.
echo.
pause
goto :eof

:soft
echo.
echo  Soft reset — keeping memory graphs intact.
echo.
echo  Resetting agents...

for %%A in (council_1 council_2 council_3 council_4 council_5 council_6) do (
    echo    Resetting %%A...
    del /q "data\%%A\state\runtime_state.json" 2>nul
    del /q "data\%%A\niscalajyoti_reading.json" 2>nul
    del /q "data\%%A\heartbeat.md" 2>nul
    del /q "data\%%A\settings_overrides.yaml" 2>nul
    del /q "data\%%A\logs\audit.jsonl" 2>nul
    for %%F in ("data\%%A\soul_versions\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
    for %%F in ("data\%%A\heartbeat_history\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
    for %%F in ("data\%%A\inbox\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
    for %%F in ("data\%%A\outbox\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
    for %%F in ("data\%%A\approvals\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
)

echo    Resetting shared state...
del /q "data\state\launcher_state.json" 2>nul
del /q "data\logs\activity.jsonl" 2>nul
for %%F in ("data\evolution_proposals\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul

echo.
echo  ✓ Soft reset complete. Memory graphs preserved, everything else cleared.
echo.
pause
goto :eof

:cancel
echo.
echo  Cancelled. No changes made.
echo.
pause
goto :eof
