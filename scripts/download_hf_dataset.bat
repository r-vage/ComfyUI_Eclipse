@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM Download a complete Hugging Face dataset repository into a local folder.
REM
REM Edit the settings below, then double-click this script. REPO_ID accepts
REM either owner/repository or a Hugging Face dataset URL. TARGET_FOLDER
REM receives the repository contents directly.

set "REPO_ID=wallstoneai/civitai-top-sfw-images-with-metadata"
set "TARGET_FOLDER=D:\AI"

REM Optional: replace the next line with set "HF_TOKEN=hf_...". Leaving it
REM unchanged preserves an existing HF_TOKEN or `hf auth login` credentials.
if not defined HF_TOKEN set "HF_TOKEN="

REM Optional: replace the value with a full path to python.exe. Leave empty to
REM use ECLIPSE_HF_PYTHON, an active environment, ComfyUI, or Python on PATH.
set "PYTHON_EXE=%ECLIPSE_HF_PYTHON%"

REM A /tree/revision URL supplies REVISION automatically.
set "REVISION="

REM Optional command-line overrides make automation possible:
REM download_hf_dataset.bat owner/repository D:\datasets\my_dataset
if not "%~1"=="" set "REPO_ID=%~1"
if not "%~2"=="" set "TARGET_FOLDER=%~2"

if "%REPO_ID%"=="" goto repo_error
if "%TARGET_FOLDER%"=="" goto target_error
if /i "%TARGET_FOLDER%"=="C:\path\to\dataset" goto target_error

set "NORMALIZED_REPO_ID=%REPO_ID%"
for /f "tokens=1 delims=?#" %%A in ("!NORMALIZED_REPO_ID!") do set "NORMALIZED_REPO_ID=%%A"
if "!NORMALIZED_REPO_ID:~-1!"=="/" set "NORMALIZED_REPO_ID=!NORMALIZED_REPO_ID:~0,-1!"

if /i "!NORMALIZED_REPO_ID:~0,32!"=="https://huggingface.co/datasets/" (
    set "NORMALIZED_REPO_ID=!NORMALIZED_REPO_ID:~32!"
) else if /i "!NORMALIZED_REPO_ID:~0,14!"=="hf://datasets/" (
    set "NORMALIZED_REPO_ID=!NORMALIZED_REPO_ID:~14!"
) else if /i "!NORMALIZED_REPO_ID:~0,7!"=="http://" (
    goto repo_url_error
) else if /i "!NORMALIZED_REPO_ID:~0,8!"=="https://" (
    goto repo_url_error
) else if /i "!NORMALIZED_REPO_ID:~0,5!"=="hf://" (
    goto repo_url_error
)

for /f "tokens=1,2,* delims=/" %%A in ("!NORMALIZED_REPO_ID!") do (
    set "REPO_OWNER=%%A"
    set "REPO_NAME=%%B"
    set "REPO_EXTRA=%%C"
)
if not defined REPO_OWNER goto repo_error
if not defined REPO_NAME goto repo_error
if defined REPO_EXTRA (
    if /i "!REPO_EXTRA:~0,5!"=="tree/" (
        if not defined REVISION set "REVISION=!REPO_EXTRA:~5!"
        set "NORMALIZED_REPO_ID=!REPO_OWNER!/!REPO_NAME!"
    ) else (
        goto repo_error
    )
)

if defined PYTHON_EXE goto python_ready
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
if defined PYTHON_EXE goto python_ready
if exist "%~dp0..\..\..\venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0..\..\..\venv\Scripts\python.exe"
if defined PYTHON_EXE goto python_ready
if exist "%~dp0..\..\..\.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0..\..\..\.venv\Scripts\python.exe"
if defined PYTHON_EXE goto python_ready
if exist "%~dp0..\..\..\..\python_embeded\python.exe" set "PYTHON_EXE=%~dp0..\..\..\..\python_embeded\python.exe"
if defined PYTHON_EXE goto python_ready
if exist "%~dp0..\..\..\python_embeded\python.exe" set "PYTHON_EXE=%~dp0..\..\..\python_embeded\python.exe"
if defined PYTHON_EXE goto python_ready
where python.exe >nul 2>&1
if not errorlevel 1 set "PYTHON_EXE=python.exe"
if not defined PYTHON_EXE goto python_error

:python_ready
"%PYTHON_EXE%" -c "import huggingface_hub" >nul 2>&1
if not errorlevel 1 goto dependency_ready

echo The selected Python environment does not contain huggingface_hub.
echo Install command: "%PYTHON_EXE%" -m pip install "huggingface_hub^>=0.30,^<2"
set "INSTALL_ANSWER="
set /p "INSTALL_ANSWER=Install it now? [y/N]: "
if /i not "!INSTALL_ANSWER!"=="y" if /i not "!INSTALL_ANSWER!"=="yes" goto install_cancelled
"%PYTHON_EXE%" -m pip install "huggingface_hub>=0.30,<2"
if errorlevel 1 goto install_error

:dependency_ready
if exist "%TARGET_FOLDER%" if not exist "%TARGET_FOLDER%\" goto target_file_error
if not exist "%TARGET_FOLDER%\" mkdir "%TARGET_FOLDER%"
if errorlevel 1 goto mkdir_error

echo Hugging Face dataset: !NORMALIZED_REPO_ID!
if defined REVISION (
    echo Revision: !REVISION!
) else (
    echo Revision: default branch
)
echo Target folder: %TARGET_FOLDER%
echo.

"%PYTHON_EXE%" "%~dp0download_hf_dataset.py" "!NORMALIZED_REPO_ID!" "%TARGET_FOLDER%" "!REVISION!"
if errorlevel 1 goto download_error

echo Dataset snapshot completed successfully.
goto success

:repo_error
echo ERROR: REPO_ID must be owner/repository or a Hugging Face dataset URL.
goto failure

:repo_url_error
echo ERROR: Only Hugging Face dataset URLs are supported: %REPO_ID%
goto failure

:target_error
echo ERROR: Set TARGET_FOLDER at the top of this script before downloading.
goto failure

:target_file_error
echo ERROR: TARGET_FOLDER exists but is not a directory: %TARGET_FOLDER%
goto failure

:python_error
echo ERROR: No Python executable was found. Set PYTHON_EXE at the top of this script.
goto failure

:install_cancelled
echo ERROR: huggingface_hub is required; installation was cancelled.
goto failure

:install_error
echo ERROR: Could not install huggingface_hub.
goto failure

:mkdir_error
echo ERROR: Could not create TARGET_FOLDER: %TARGET_FOLDER%
goto failure

:download_error
echo.
echo ERROR: Download failed. Check the repository ID, network connection, free disk space,
echo and access to private or gated datasets ^(hf auth login or HF_TOKEN^).
goto failure

:success
if "%~1"=="" pause
endlocal
exit /b 0

:failure
if "%~1"=="" pause
endlocal
exit /b 1
