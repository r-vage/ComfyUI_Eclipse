@echo off
REM ============================================================
REM Windows Junctions for ComfyUI
REM ============================================================
REM Edit COMFY and MODELS below to match your install paths.
REM
REM Uses directory junctions (mklink /J) instead of symlinks:
REM   - Works without admin rights and without Developer Mode
REM   - Functionally equivalent for same-volume folders
REM   - WARNING: junctions only work on the same drive letter.
REM     If your custom_nodes and models are on different drives,
REM     enable Developer Mode and replace /J with /D below.
REM
REM 1. Copies files from node folders into the shared model folders
REM    (merges new files, skips existing ones via robocopy /XC /XN /XO)
REM 2. Removes the now-empty original directories
REM 3. Creates junctions pointing back to the shared locations
REM
REM Safe to re-run -- skips folders that are already junctions.
REM ============================================================

setlocal EnableDelayedExpansion

set "COMFY=D:\AI\ComfyUI"
set "NODES=%COMFY%\custom_nodes"
set "MODELS=D:\AI\models"

if not exist "%NODES%\" (
    echo ERROR: custom_nodes folder not found: %NODES%
    echo Edit COMFY and MODELS at the top of this script.
    exit /b 1
)

echo === Merging files into shared folders ===

call :merge_and_remove "%NODES%\ComfyUI-Impact-Pack\wildcards"        "%MODELS%\wildcards"                  "Impact-Pack wildcards"
call :merge_and_remove "%NODES%\ComfyUI-Raffle\lists"                  "%MODELS%\wildcards"                  "Raffle lists"
call :merge_and_remove "%NODES%\ComfyUI-WD14-Tagger\models"            "%MODELS%\LLM"                        "WD14 Tagger models"
call :merge_and_remove "%NODES%\comfyui_controlnet_aux\ckpts"          "%MODELS%\controlnet_ckpts"            "controlnet_aux ckpts"
call :merge_and_remove "%NODES%\ComfyUI-Frame-Interpolation\ckpts"     "%MODELS%\Frame_Interpolation\ckpts"   "Frame-Interpolation ckpts"

call :merge_and_remove "%NODES%\ComfyUI_LayerStyle\RMBG-1.4"           "%MODELS%\rembg\RMBG-1.4"             "LayerStyle RMBG-1.4"
call :merge_and_remove "%NODES%\ComfyUI-Video-Matting\ckpts"           "%MODELS%\rembg\RMBG-1.4"             "Video-Matting ckpts"
call :merge_and_remove "%NODES%\ComfyUI-BRIA_AI-RMBG\RMBG-1.4"         "%MODELS%\rembg\RMBG-1.4"             "BRIA RMBG-1.4"

call :merge_and_remove "%COMFY%\comfy_extras\fonts"                    "%MODELS%\fonts"                       "comfy_extras fonts"
call :merge_and_remove "%NODES%\ComfyUI_Comfyroll_CustomNodes\fonts"   "%MODELS%\fonts"                       "Comfyroll fonts"
call :merge_and_remove "%NODES%\Comfyui-ergouzi-Nodes\fonts"           "%MODELS%\fonts"                       "ergouzi fonts"
call :merge_and_remove "%NODES%\ComfyUI_LayerStyle\font"               "%MODELS%\fonts"                       "LayerStyle font"
call :merge_and_remove "%NODES%\ComfyUI_LayerStyle_Advance\font"       "%MODELS%\fonts"                       "LayerStyle_Advance font"
call :merge_and_remove "%NODES%\ComfyUI_essentials\fonts"              "%MODELS%\fonts"                       "essentials fonts"
call :merge_and_remove "%NODES%\ComfyUI_essentials_mb\fonts"           "%MODELS%\fonts"                       "essentials_mb fonts"
call :merge_and_remove "%NODES%\ComfyUI-KJNodes\fonts"                 "%MODELS%\fonts"                       "KJNodes fonts"

call :merge_and_remove "%NODES%\ComfyUI_essentials\luts"               "%MODELS%\luts"                        "essentials luts"
call :merge_and_remove "%NODES%\ComfyUI_essentials_mb\luts"            "%MODELS%\luts"                        "essentials_mb luts"
call :merge_and_remove "%NODES%\ComfyUI_LayerStyle\lut"                "%MODELS%\luts"                        "LayerStyle lut"

echo.
echo === Creating junctions ===

call :make_link "%MODELS%\wildcards"                  "%NODES%\ComfyUI-Impact-Pack\wildcards"      "Impact-Pack wildcards"
call :make_link "%MODELS%\wildcards"                  "%NODES%\ComfyUI-Raffle\lists"               "Raffle lists"
call :make_link "%MODELS%\LLM"                        "%NODES%\ComfyUI-WD14-Tagger\models"         "WD14 Tagger models"
call :make_link "%MODELS%\controlnet_ckpts"           "%NODES%\comfyui_controlnet_aux\ckpts"       "controlnet_aux ckpts"
call :make_link "%MODELS%\Frame_Interpolation\ckpts"  "%NODES%\ComfyUI-Frame-Interpolation\ckpts"  "Frame-Interpolation ckpts"

call :make_link "%MODELS%\rembg\RMBG-1.4"             "%NODES%\ComfyUI_LayerStyle\RMBG-1.4"        "LayerStyle RMBG"
call :make_link "%MODELS%\rembg\RMBG-1.4"             "%NODES%\ComfyUI-Video-Matting\ckpts"        "Video-Matting ckpts"
call :make_link "%MODELS%\rembg\RMBG-1.4"             "%NODES%\ComfyUI-BRIA_AI-RMBG\RMBG-1.4"      "BRIA RMBG"

call :make_link "%MODELS%\fonts"                      "%NODES%\ComfyUI_Comfyroll_CustomNodes\fonts" "Comfyroll fonts"
call :make_link "%MODELS%\fonts"                      "%NODES%\Comfyui-ergouzi-Nodes\fonts"         "ergouzi fonts"
call :make_link "%MODELS%\fonts"                      "%NODES%\ComfyUI_LayerStyle\font"             "LayerStyle font"
call :make_link "%MODELS%\fonts"                      "%NODES%\ComfyUI_LayerStyle_Advance\font"     "LayerStyle_Advance font"
call :make_link "%MODELS%\fonts"                      "%NODES%\ComfyUI_essentials\fonts"            "essentials fonts"
call :make_link "%MODELS%\fonts"                      "%NODES%\ComfyUI_essentials_mb\fonts"         "essentials_mb fonts"
call :make_link "%MODELS%\fonts"                      "%NODES%\ComfyUI-KJNodes\fonts"               "KJNodes fonts"

call :make_link "%MODELS%\luts"                       "%NODES%\ComfyUI_essentials\luts"             "essentials luts"
call :make_link "%MODELS%\luts"                       "%NODES%\ComfyUI_essentials_mb\luts"          "essentials_mb luts"
call :make_link "%MODELS%\luts"                       "%NODES%\ComfyUI_LayerStyle\lut"              "LayerStyle lut"

echo.
echo === Done ===
endlocal
exit /b 0


REM ------------------------------------------------------------------
REM :merge_and_remove SRC DST LABEL
REM   - Skips if source doesn't exist or is already a reparse point
REM   - robocopy /XC /XN /XO = copy missing files only, never overwrite
REM   - Removes the original directory after merge
REM ------------------------------------------------------------------
:merge_and_remove
set "SRC=%~1"
set "DST=%~2"
set "LABEL=%~3"

if not exist "%SRC%\" goto :eof

REM Skip if already a junction/symlink
fsutil reparsepoint query "%SRC%" >nul 2>&1
if not errorlevel 1 goto :eof

if not exist "%DST%\" mkdir "%DST%" >nul 2>&1

robocopy "%SRC%" "%DST%" /E /XC /XN /XO /NFL /NDL /NJH /NJS /NC /NS /NP >nul
echo   Merged %LABEL% -^> %DST%

rmdir /S /Q "%SRC%" >nul 2>&1
echo   Removed %LABEL%
goto :eof


REM ------------------------------------------------------------------
REM :make_link TARGET LINK LABEL
REM   - Skips if parent node folder doesn't exist (node not installed)
REM   - Skips if junction already exists at LINK
REM   - Errors if a real directory still exists at LINK
REM ------------------------------------------------------------------
:make_link
set "TARGET=%~1"
set "LINK=%~2"
set "LABEL=%~3"

for %%P in ("%LINK%") do set "PARENT=%%~dpP"
if not exist "%PARENT%" (
    echo   Skipped %LABEL% ^(node not installed^)
    goto :eof
)

REM Already a junction? -> done
fsutil reparsepoint query "%LINK%" >nul 2>&1
if not errorlevel 1 (
    echo   Skipped %LABEL% ^(already linked^)
    goto :eof
)

if exist "%LINK%\" (
    echo   ERROR: %LINK% still exists as a directory -- remove it first
    goto :eof
)

mklink /J "%LINK%" "%TARGET%" >nul
if errorlevel 1 (
    echo   FAILED %LABEL%
) else (
    echo   Linked %LABEL%
)
goto :eof
