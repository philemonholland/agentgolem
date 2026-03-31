@echo off
chcp 65001 >nul 2>&1
title AgentGolem - Reset Agents
cd /d "%~dp0"

echo.
echo  +==========================================+
echo  ^|      AgentGolem - Reset Agents           ^|
echo  +==========================================+
echo.
echo  This will wipe agent state so they start fresh.
echo  soul.md files are preserved (agent personality).
echo.
echo  Choose reset level:
echo.
echo    1. Full reset  - clear memory, reading progress, logs, heartbeats
echo    2. Soft reset  - clear progress ^& logs, KEEP memory graphs
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
echo  [!] FULL RESET - all agent data will be erased (except soul.md)
set /p CONFIRM="  Are you sure? [y/N]: "
if /i not "%CONFIRM%"=="y" goto :cancel

echo.
echo  Resetting agents...

for %%A in (council_1 council_2 council_3 council_4 council_5 council_6 council_7) do (
    echo    Resetting %%A...
    del /q "data\%%A\state\runtime_state.json" 2>nul
    del /q "data\%%A\niscalajyoti_reading.json" 2>nul
    del /q "data\%%A\council7_foundation.json" 2>nul
    del /q "data\%%A\vow_foundation.json" 2>nul
    del /q "data\%%A\session_state.json" 2>nul
    del /q "data\%%A\heartbeat.md" 2>nul
    del /q "data\%%A\temperament.json" 2>nul
    del /q "data\%%A\internal_state.json" 2>nul
    del /q "data\%%A\emotional_dynamics.json" 2>nul
    del /q "data\%%A\self_model.json" 2>nul
    del /q "data\%%A\relationships.json" 2>nul
    del /q "data\%%A\developmental.json" 2>nul
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
del /q "data\memory\mycelium.db" 2>nul
del /q "data\memory\mycelium.db-shm" 2>nul
del /q "data\memory\mycelium.db-wal" 2>nul
for %%F in ("data\evolution_proposals\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
for %%F in ("data\memory\snapshots\*") do if not "%%~nxF"==".gitkeep" del /q "%%F" 2>nul
for %%F in ("data\nj_chapter_digests\*") do del /q "%%F" 2>nul

echo.
echo  [OK] Full reset complete. All 7 agents will start fresh.
echo.
pause
goto :eof

:soft
echo.
echo  Soft reset - keeping memory graphs intact.
echo.
echo  Resetting agents...

for %%A in (council_1 council_2 council_3 council_4 council_5 council_6 council_7) do (
    echo    Resetting %%A...
    del /q "data\%%A\state\runtime_state.json" 2>nul
    del /q "data\%%A\niscalajyoti_reading.json" 2>nul
    del /q "data\%%A\council7_foundation.json" 2>nul
    del /q "data\%%A\vow_foundation.json" 2>nul
    del /q "data\%%A\session_state.json" 2>nul
    del /q "data\%%A\heartbeat.md" 2>nul
    del /q "data\%%A\temperament.json" 2>nul
    del /q "data\%%A\internal_state.json" 2>nul
    del /q "data\%%A\emotional_dynamics.json" 2>nul
    del /q "data\%%A\self_model.json" 2>nul
    del /q "data\%%A\relationships.json" 2>nul
    del /q "data\%%A\developmental.json" 2>nul
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
echo  [OK] Soft reset complete. Memory graphs preserved, everything else cleared for all 7 agents.
echo.
pause
goto :eof

:cancel
echo.
echo  Cancelled. No changes made.
echo.
pause
goto :eof
