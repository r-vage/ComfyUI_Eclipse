@echo off
REM Remove extracted default files from Eclipse data folders.
REM Re-extracted automatically on next ComfyUI startup from .defaults\
setlocal

REM Resolve Eclipse repo root (parent of this script's folder)
set "ECLIPSE_DIR=%~dp0.."
pushd "%ECLIPSE_DIR%" >nul
set "ECLIPSE_DIR=%CD%"
popd >nul

echo Clearing Eclipse data folders...
for %%F in (prompts patterns styles templates wildcards config registry) do (
    if exist "%ECLIPSE_DIR%\%%F\" (
        rmdir /S /Q "%ECLIPSE_DIR%\%%F"
        echo   Removed %%F\
    )
)

REM Remove root configs (re-extracted from .defaults\)
for %%C in (config.json docker_config.json) do (
    if exist "%ECLIPSE_DIR%\%%C" (
        del /F /Q "%ECLIPSE_DIR%\%%C"
        echo   Removed %%C
    )
)

REM Remove migration markers so user-folder + SML config migrations re-run on next startup
for %%M in (.migrated .sml_config_migrated) do (
    if exist "%ECLIPSE_DIR%\%%M" (
        del /F /Q "%ECLIPSE_DIR%\%%M"
        echo   Removed %%M
    )
)

echo Done. Files will be re-extracted on next ComfyUI startup.
endlocal
