#!/usr/bin/env bash
# Remove extracted default files from Eclipse data folders.
# Re-extracted automatically on next ComfyUI startup from .defaults/

set -euo pipefail

ECLIPSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ""
echo "  ============================================================"
echo "   ComfyUI Eclipse - Reset Data Folders and Re-extract"
echo "  ============================================================"
echo ""
echo "  This will delete all Eclipse data folders (prompts, patterns,"
echo "  styles, templates, wildcards, config, registry) to restore"
echo "  them to the original defaults."
echo ""
echo "  WARNING: Customizations you made in these folders will be LOST."
echo ""

read -rp "  Are you sure you want to proceed? (y/N): " confirm
if [[ "${confirm,,}" != "y" ]]; then
    echo "  Cancelled."
    exit 0
fi

echo ""
read -rp "  Do you also want to reset config files (config.json, docker_config.json)? This will delete your custom API keys, tokens, and ports! (y/N): " confirm_cfg

echo ""
echo "Clearing Eclipse data folders..."
for folder in prompts patterns styles templates wildcards config registry; do
    target="$ECLIPSE_DIR/$folder"
    if [ -d "$target" ]; then
        rm -rf "$target"
        echo "  Removed $folder/"
    fi
done

if [[ "${confirm_cfg,,}" == "y" ]]; then
    # Remove root configs (re-extracted from .defaults/)
    for cfg in config.json docker_config.json; do
        [ -f "$ECLIPSE_DIR/$cfg" ] && rm -f "$ECLIPSE_DIR/$cfg" && echo "  Removed $cfg"
    done
else
    echo "  Skipping config.json and docker_config.json (preserved)"
fi

# Remove migration markers so user-folder + SML config migrations re-run on next startup
for marker in .migrated .sml_config_migrated; do
    [ -f "$ECLIPSE_DIR/$marker" ] && rm -f "$ECLIPSE_DIR/$marker" && echo "  Removed $marker"
done

echo ""
echo "Done. Files will be re-extracted on next ComfyUI startup."
