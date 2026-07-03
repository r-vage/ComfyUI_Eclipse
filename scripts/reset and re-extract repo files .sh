#!/usr/bin/env bash
# Remove extracted default files from Eclipse data folders.
# Re-extracted automatically on next ComfyUI startup from .defaults/

ECLIPSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "Clearing Eclipse data folders..."
for folder in prompts patterns styles templates wildcards config registry; do
    target="$ECLIPSE_DIR/$folder"
    if [ -d "$target" ]; then
        rm -rf "$target"
        echo "  Removed $folder/"
    fi
done

# Remove root configs (re-extracted from .defaults/)
for cfg in config.json docker_config.json; do
    [ -f "$ECLIPSE_DIR/$cfg" ] && rm -f "$ECLIPSE_DIR/$cfg" && echo "  Removed $cfg"
done

# Remove migration markers so user-folder + SML config migrations re-run on next startup
for marker in .migrated .sml_config_migrated; do
    [ -f "$ECLIPSE_DIR/$marker" ] && rm -f "$ECLIPSE_DIR/$marker" && echo "  Removed $marker"
done

echo "Done. Files will be re-extracted on next ComfyUI startup."
