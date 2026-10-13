@echo off
title FFVII Tactical Companion - LAN / iPad Access Mode
cd /d "%~dp0"

echo =================================================================
echo   FFVII TACTICAL COMPANION - LAN / IPAD ACCESS MODE
echo =================================================================
echo.

REM Retrieve IPv4 address
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    set "LOCAL_IP=%%a"
    goto :found_ip
)

:found_ip
set "LOCAL_IP=%LOCAL_IP: =%"

echo [INFO] Binding server to all network interfaces (0.0.0.0:59314)...
if not "%LOCAL_IP%"=="" (
    echo [INFO] On your iPad or tablet, open Safari/Chrome and navigate to:
    echo.
    echo        http://%LOCAL_IP%:59314
    echo.
    echo   If another program uses port 59314, the companion takes the next free
    echo   port and shows it in its window title: use that port instead.
    echo.
    echo   Tip: Tap the Share button in Safari and choose "Add to Home Screen"
    echo        to run the companion as a full-screen, borderless tablet app.
) else (
    echo [INFO] On your iPad, open Safari and navigate to http://^<YOUR_PC_IP^>:59314
)
echo.
echo =================================================================
echo Starting companion...
echo.

REM The EXE next to this script (release download), or the compiled binary in dist\ (project folder)
if exist "FFVII_Companion.exe" (
    start "" "FFVII_Companion.exe" --host 0.0.0.0 %*
    exit /b 0
)
if exist "dist\FFVII_Companion\FFVII_Companion.exe" (
    start "" "dist\FFVII_Companion\FFVII_Companion.exe" --host 0.0.0.0 %*
    exit /b 0
)

REM Otherwise, launch via pythonw in virtual environment
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m ffvii_companion --host 0.0.0.0 %*
    exit /b 0
)

REM Fallback to standard python
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m ffvii_companion --host 0.0.0.0 %*
    exit /b 0
)

echo [ERROR] Could not find compiled executable or virtual environment.
pause
