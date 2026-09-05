# Hugging Face Dataset Snapshot Downloader

Eclipse includes separate Linux and Windows utilities for downloading a complete
Hugging Face dataset repository when the repository does not provide one ZIP
archive. They use Hugging Face's snapshot downloader to fetch all repository
files while preserving folders such as `images/` and metadata files such as
`prompts.json`.

## Configure

Open the script for your operating system:

- Linux: `scripts/download_hf_dataset.sh`
- Windows: `scripts/download_hf_dataset.bat`

Edit the settings near the top. `HF_TOKEN` and `PYTHON_EXE` are optional:

```bash
REPO_ID=wallstoneai/civitai-top-sfw-images-with-metadata
TARGET_FOLDER=/your/dataset/folder
HF_TOKEN="hf_your_token"
PYTHON_EXE="/full/path/to/python"
ECLIPSE_HF_MAX_WORKERS=4
```

The equivalent Windows settings use batch syntax:

```bat
set "REPO_ID=wallstoneai/civitai-top-sfw-images-with-metadata"
set "TARGET_FOLDER=D:\datasets\civitai-sfw"
set "HF_TOKEN=hf_your_token"
set "PYTHON_EXE=D:\full\path\to\python.exe"
set "ECLIPSE_HF_MAX_WORKERS=4"
```

`REPO_ID` also accepts a full dataset URL, including a URL ending in
`/tree/main`. A tree URL selects that revision; otherwise the repository's
default branch is used.

The target is exact: repository contents are placed directly inside
`TARGET_FOLDER`. Hugging Face also creates `.cache/huggingface/` metadata there
so later runs can efficiently skip unchanged files and retrieve missing or
updated files. The scripts do not delete unrelated existing files.

## Run

Linux:

```bash
chmod +x scripts/download_hf_dataset.sh
./scripts/download_hf_dataset.sh
```

Windows users can double-click `download_hf_dataset.bat` or run it from Command
Prompt. Both scripts also accept temporary command-line overrides:

```bash
./scripts/download_hf_dataset.sh owner/repository "/data/datasets/my set"
```

```bat
scripts\download_hf_dataset.bat owner/repository "D:\datasets\my set"
```

Set `PYTHON_EXE` to an exact interpreter when desired. Leaving it empty makes
the utilities try `ECLIPSE_HF_PYTHON`, the active virtual environment, common
ComfyUI Python layouts, and then Python from `PATH`.

`huggingface_hub` is an Eclipse dependency. If it is missing from the selected
Python environment, the script shows the constrained pip command and asks before
installing it. Declining leaves the environment unchanged.

## Authentication and retries

Public datasets need no token, but authentication gives public downloads the
account's API rate-limit allowance instead of the shared unauthenticated
allowance. The scripts can use the editable `HF_TOKEN` setting, an inherited
`HF_TOKEN` environment variable, or credentials saved by `hf auth login` in the
same Python environment. The token is never printed or added to command-line
arguments. A token pasted into the script is stored there as plain text, so do
not commit or share that customized copy; an environment variable or saved
login is safer for a shared checkout.

If Hugging Face responds with HTTP 429, the downloader reads the server's
`Retry-After` or rate-limit reset value, waits for that window, and retries up
to six times. Existing files and snapshot metadata remain in place throughout,
so the retry continues into the same target. The scripts also limit concurrent
file downloads to four by default, which is friendlier to repositories with
many loose files.

Set `ECLIPSE_HF_MAX_WORKERS` near the top of either script to change its worker
count; no separate shell command is needed. Advanced users can still override
the retry limit through the environment before running the script:

```bash
export ECLIPSE_HF_MAX_RETRIES=10
```

Use `set ECLIPSE_HF_MAX_RETRIES=10` in Windows Command Prompt. Retries may be
set from 0 through 20; the in-script worker setting may be set from 1 through
32.

Running a script again is safe. Hugging Face uses its target metadata and cache
to reuse existing content instead of downloading the entire repository again.
If a run fails, correct the reported repository, access, disk-space, or network
problem and rerun it.
