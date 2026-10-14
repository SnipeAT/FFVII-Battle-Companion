@echo off
title FFVII Tactical Companion - LAN Mode (phones, tablets, other PCs)
cd /d "%~dp0"

echo =================================================================
echo   FFVII TACTICAL COMPANION - LAN MODE (PHONES, TABLETS, OTHER PCS)
echo =================================================================
echo.

REM This PC's address on the network: the interface of the default route with the lowest metric.
REM ipconfig can list virtual adapters (WSL, Hyper-V, VirtualBox) before the Wi-Fi or Ethernet one.
set "LOCAL_IP="
set "BEST_METRIC="
for /f "tokens=1-5" %%a in ('route print -4 0.0.0.0') do (
    if "%%a %%b"=="0.0.0.0 0.0.0.0" if not "%%e"=="" call :pick_ip %%d %%e
)
if defined LOCAL_IP goto :found_ip

REM No default route (a network without a router): the first IPv4 address ipconfig lists
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    set "LOCAL_IP=%%a"
    goto :found_ip
)

:found_ip
if defined LOCAL_IP set "LOCAL_IP=%LOCAL_IP: =%"

echo [INFO] Binding server to all network interfaces (0.0.0.0:59314)...
if not "%LOCAL_IP%"=="" (
    echo [INFO] On a phone, tablet or PC on the same network, open a browser and go to:
    echo.
    echo        http://%LOCAL_IP%:59314
    echo.
    echo   If another program uses port 59314, the companion takes the next free
    echo   port and shows it in its window title: use that port instead.
    echo.
    echo   Tip: on an iPhone or iPad, tap Share in Safari and choose "Add to Home Screen"
    echo        to run the companion full-screen, without the browser bars.
) else (
    echo [INFO] On a phone, tablet or PC on the same network, open http://^<YOUR_PC_IP^>:59314
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
exit /b 1

:pick_ip
REM %1 the interface address and %2 the metric of a default route: keeps the lowest metric
if not defined BEST_METRIC goto :keep_ip
if %2 GEQ %BEST_METRIC% goto :eof
:keep_ip
set "LOCAL_IP=%1"
set "BEST_METRIC=%2"
goto :eof
