#!/usr/bin/env bash
# ComfyUI Eclipse - Reset Prompt Files to Defaults

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULTS="$SCRIPT_DIR/.defaults/prompts"

echo ""
echo "  ============================================================"
echo "   ComfyUI Eclipse - Reset Prompt Files to Defaults"
echo "  ============================================================"
echo ""
echo "  This will overwrite ALL prompt files with the latest"
echo "  defaults shipped with this release."
echo ""
echo "  Any customizations you made to prompt files will be LOST."
echo ""

read -rp "  Are you sure? (y/N): " confirm
if [[ "${confirm,,}" != "y" ]]; then
    echo "  Cancelled."
    exit 0
fi

echo ""

if [[ ! -d "$DEFAULTS" ]]; then
    echo "  ERROR: .defaults/prompts folder not found."
    exit 1
fi

count=0
failed=0

while IFS= read -r -d '' example; do
    rel="${example#"$DEFAULTS"/}"
    target="${rel%.example}"
    dest="$SCRIPT_DIR/prompts/$target"

    mkdir -p "$(dirname "$dest")"
    if cp -f "$example" "$dest" 2>/dev/null; then
        ((count++)) || true
    else
        echo "  FAILED: $target"
        ((failed++)) || true
    fi
done < <(find "$DEFAULTS" -name '*.example' -print0 | sort -z) || true

echo ""
echo "  Done. Extracted $count file(s)."
[[ $failed -gt 0 ]] && echo "  Failed: $failed file(s)."
echo ""
