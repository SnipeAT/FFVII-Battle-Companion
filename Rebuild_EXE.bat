@echo off
title Rebuild FFVII Companion Native Desktop Executable
cd /d "%~dp0"

echo =================================================================
echo   REBUILDING FFVII COMPANION NATIVE DESKTOP EXECUTABLE
echo =================================================================
echo.

REM Locate Python in virtual environment
set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set "PYTHON_EXE=python"
    )
)

if "%PYTHON_EXE%"=="" (
    echo [ERROR] Python environment not found.
    echo Could not locate .venv\Scripts\python.exe or system python.
    echo.
    pause
    exit /b 1
)

echo [INFO] Using Python: %PYTHON_EXE%
echo [INFO] Running build_tools\build_exe.py...
echo.

"%PYTHON_EXE%" build_tools\build_exe.py %*
set "BUILD_EXIT_CODE=%ERRORLEVEL%"

echo.
if %BUILD_EXIT_CODE% equ 0 goto :build_success
goto :build_failure

:build_success
echo =================================================================
echo   BUILD SUCCESSFUL!
echo   Executable: dist\FFVII_Companion\FFVII_Companion.exe
echo   Launch via: Launch_Desktop_App.bat
echo =================================================================
echo.
pause
exit /b 0

:build_failure
echo =================================================================
echo   BUILD FAILED! [Exit code: %BUILD_EXIT_CODE%]
echo   Please inspect compiler and packaging errors above.
echo =================================================================
echo.
pause
exit /b %BUILD_EXIT_CODE%
