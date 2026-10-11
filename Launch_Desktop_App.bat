@echo off
title FFVII Tactical Battle Companion - Native Desktop App
cd /d "%~dp0"

REM If compiled binary exists, launch it directly
if exist "dist\FFVII_Companion\FFVII_Companion.exe" (
    start "" "dist\FFVII_Companion\FFVII_Companion.exe" %*
    exit /b 0
)

REM Otherwise, launch via pythonw in virtual environment (no console window)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m ffvii_companion %*
    exit /b 0
)

REM Fallback to standard python if pythonw not present
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m ffvii_companion %*
    exit /b 0
)

echo [ERROR] Could not find compiled executable or virtual environment.
pause
