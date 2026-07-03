@echo off
setlocal enabledelayedexpansion

echo.
echo  ============================================================
echo   ComfyUI Eclipse - Reset Prompt Files to Defaults
echo  ============================================================
echo.
echo  This will overwrite ALL prompt files with the latest
echo  defaults shipped with this release.
echo.
echo  Any customizations you made to prompt files will be LOST.
echo.

set /p "CONFIRM=Are you sure? (y/N): "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled.
    pause
    exit /b 0
)

echo.

set "DEFAULTS=%~dp0.defaults\prompts"
set "REPO=%~dp0prompts"
set COUNT=0
set SKIPPED=0

if not exist "%DEFAULTS%" (
    echo ERROR: .defaults\prompts folder not found.
    pause
    exit /b 1
)

for /r "%DEFAULTS%" %%F in (*.example) do (
    set "EXAMPLE=%%F"
    set "REL=!EXAMPLE:%DEFAULTS%\=!"
    set "TARGET=!REL:.example=!"
    set "DEST=%REPO%\!TARGET!"

    for %%D in ("!DEST!") do (
        if not exist "%%~dpD" mkdir "%%~dpD"
    )
    copy /y "%%F" "!DEST!" >nul 2>&1
    if !errorlevel! equ 0 (
        set /a COUNT+=1
    ) else (
        echo  FAILED: !TARGET!
        set /a SKIPPED+=1
    )
)

echo.
echo  Done. Extracted %COUNT% file(s).
if %SKIPPED% gtr 0 echo  Failed: %SKIPPED% file(s).
echo.
pause
