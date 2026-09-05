#!/usr/bin/env bash
# Download a complete Hugging Face dataset repository into a local folder.
#
# Edit the settings below, then run this script. REPO_ID accepts either
# owner/repository or a Hugging Face dataset URL. TARGET_FOLDER receives the
# repository contents directly.

REPO_ID="wallstoneai/civitai-top-sfw-images-with-metadata"
TARGET_FOLDER="/mnt/data/AI/civitai-top-sfw-images-with-metadata"

# Optional: paste an hf_... token between the quotes, or leave this unchanged to
# use an existing HF_TOKEN environment variable or `hf auth login` credentials.
HF_TOKEN="${HF_TOKEN:-}"

# Optional: set the full Python interpreter path between the quotes. Leave empty
# to use ECLIPSE_HF_PYTHON, an active environment, ComfyUI, or Python on PATH.
PYTHON_EXE="${ECLIPSE_HF_PYTHON:-}"

# Number of repository files downloaded concurrently (1 through 32).
ECLIPSE_HF_MAX_WORKERS=4

# A /tree/<revision> URL supplies REVISION automatically.
REVISION=""

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -ge 1 ]]; then
    REPO_ID="$1"
fi
if [[ $# -ge 2 ]]; then
    TARGET_FOLDER="$2"
fi

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

resolve_python() {
    if [[ -n "$PYTHON_EXE" ]]; then
        if [[ -x "$PYTHON_EXE" ]] || command -v "$PYTHON_EXE" >/dev/null 2>&1; then
            return 0
        fi
        fail "Configured Python executable was not found: $PYTHON_EXE"
    fi

    if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
        PYTHON_EXE="$VIRTUAL_ENV/bin/python"
        return 0
    fi

    local candidate
    for candidate in \
        "$SCRIPT_DIR/../../../comfy_env/bin/python" \
        "$SCRIPT_DIR/../../../venv/bin/python" \
        "$SCRIPT_DIR/../../../.venv/bin/python" \
        "$SCRIPT_DIR/../../../../comfy_env/bin/python"; do
        if [[ -x "$candidate" ]]; then
            PYTHON_EXE="$candidate"
            return 0
        fi
    done

    if command -v python3 >/dev/null 2>&1; then
        PYTHON_EXE="python3"
        return 0
    fi
    if command -v python >/dev/null 2>&1; then
        PYTHON_EXE="python"
        return 0
    fi
    fail "No Python executable was found. Set PYTHON_EXE at the top of this script."
}

normalize_repo_id() {
    local value="$1"
    value="${value%%\?*}"
    value="${value%%\#*}"
    value="${value%/}"

    case "$value" in
        https://huggingface.co/datasets/*)
            value="${value#https://huggingface.co/datasets/}"
            ;;
        hf://datasets/*)
            value="${value#hf://datasets/}"
            ;;
        http://*|https://*|hf://*)
            fail "Only Hugging Face dataset URLs are supported: $1"
            ;;
    esac

    if [[ "$value" == */tree/* ]]; then
        local url_revision="${value#*/tree/}"
        value="${value%%/tree/*}"
        if [[ -z "$REVISION" ]]; then
            REVISION="$url_revision"
        fi
    fi

    if [[ ! "$value" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
        fail "Repository must be owner/repository or a Hugging Face dataset URL: $1"
    fi
    NORMALIZED_REPO_ID="$value"
}

if [[ -z "${REPO_ID//[[:space:]]/}" ]]; then
    fail "REPO_ID is empty. Edit it at the top of this script."
fi
if [[ -z "${TARGET_FOLDER//[[:space:]]/}" || "$TARGET_FOLDER" == "/path/to/dataset" ]]; then
    fail "Set TARGET_FOLDER at the top of this script before downloading."
fi
if [[ "$TARGET_FOLDER" == "~" ]]; then
    TARGET_FOLDER="$HOME"
elif [[ "$TARGET_FOLDER" == "~/"* ]]; then
    TARGET_FOLDER="$HOME/${TARGET_FOLDER:2}"
fi

normalize_repo_id "$REPO_ID"
resolve_python
if [[ -n "$HF_TOKEN" ]]; then
    export HF_TOKEN
fi
export ECLIPSE_HF_MAX_WORKERS

if ! "$PYTHON_EXE" -c "import huggingface_hub" >/dev/null 2>&1; then
    echo "The selected Python environment does not contain huggingface_hub."
    echo "Install command: $PYTHON_EXE -m pip install 'huggingface_hub>=0.30,<2'"
    read -r -p "Install it now? [y/N]: " install_answer
    case "$install_answer" in
        y|Y|yes|YES|Yes)
            "$PYTHON_EXE" -m pip install "huggingface_hub>=0.30,<2" || \
                fail "Could not install huggingface_hub."
            ;;
        *)
            fail "huggingface_hub is required; installation was cancelled."
            ;;
    esac
fi

if [[ -e "$TARGET_FOLDER" && ! -d "$TARGET_FOLDER" ]]; then
    fail "TARGET_FOLDER exists but is not a directory: $TARGET_FOLDER"
fi
mkdir -p "$TARGET_FOLDER" || fail "Could not create TARGET_FOLDER: $TARGET_FOLDER"

echo "Hugging Face dataset: $NORMALIZED_REPO_ID"
echo "Revision: ${REVISION:-default branch}"
echo "Target folder: $TARGET_FOLDER"
echo

if ! "$PYTHON_EXE" "$SCRIPT_DIR/download_hf_dataset.py" \
    "$NORMALIZED_REPO_ID" "$TARGET_FOLDER" "$REVISION"; then
    echo >&2
    echo "Download failed. Check the repository ID, network connection, free disk space," >&2
    echo "and access to private or gated datasets (hf auth login or HF_TOKEN)." >&2
    exit 1
fi

echo "Dataset snapshot completed successfully."
