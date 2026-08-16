@echo off
setlocal enabledelayedexpansion

REM Resolve Eclipse repo root (parent of this script's folder)
set "ECLIPSE_DIR=%~dp0.."
pushd "%ECLIPSE_DIR%" >nul
set "ECLIPSE_DIR=%CD%"
popd >nul

echo.
echo  ============================================================
echo   ComfyUI Eclipse - Reset Data Folders and Re-extract
echo  ============================================================
echo.
echo  This will delete Eclipse-owned data folders (prompts, patterns,
echo  styles, wildcards) to restore
echo  them to the original defaults.
echo.
echo  WARNING: Customizations you made in these folders will be LOST.
echo.

set /p "CONFIRM=Are you sure you want to proceed? (y/N): "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled.
    pause
    exit /b 0
)

echo.
set "RESET_CONFIGS=n"
set /p "CONFIRM_CFG=Do you also want to reset Eclipse config.json? This will delete your custom Eclipse settings! (y/N): "
if /i "%CONFIRM_CFG%"=="y" set "RESET_CONFIGS=y"

echo.
echo Clearing Eclipse data folders...
for %%F in (prompts patterns styles wildcards) do (
    if exist "%ECLIPSE_DIR%\%%F\" (
        rmdir /S /Q "%ECLIPSE_DIR%\%%F"
        echo   Removed %%F\
    )
)

if "%RESET_CONFIGS%"=="y" (
    REM Remove root configs (re-extracted from .defaults\)
    for %%C in (config.json) do (
        if exist "%ECLIPSE_DIR%\%%C" (
            del /F /Q "%ECLIPSE_DIR%\%%C"
            echo   Removed %%C
        )
    )
) else (
    echo   Skipping config.json (preserved)
)

REM Remove the Eclipse user-folder migration marker so migration re-runs on next startup
for %%M in (.migrated) do (
    if exist "%ECLIPSE_DIR%\%%M" (
        del /F /Q "%ECLIPSE_DIR%\%%M"
        echo   Removed %%M
    )
)

echo.
echo Done. Files will be re-extracted on next ComfyUI startup.
pause
endlocal
