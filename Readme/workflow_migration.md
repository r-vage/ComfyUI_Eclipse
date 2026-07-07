# Workflow Migration Tool [Eclipse]

The **Workflow Migration Tool** is a custom utility node in ComfyUI Eclipse that automatically upgrades existing workflows from version v2/v23 to Eclipse v4.0.0. It eliminates the need to manually edit workflow `.json` files or run command-line scripts.

## Node Specifications
- **Node Name:** `Workflow Migration Tool [Eclipse]`
- **Display Name:** `Workflow Migration Tool`
- **Category:** `Eclipse > Utility`
- **Output:** A multiline execution log string.

## Parameters

- **`path`** (String):
  The absolute path to either:
  1. A single workflow `.json` file (e.g., `/path/to/my_workflow.json`).
  2. A directory containing multiple workflow files. The node will recursively search the directory and process all `.json` files.

  > [!TIP]
  > Beside any manually exported or downloaded workflow files, ComfyUI's standard location for saved workflows in the browser is `ComfyUI/user/default/workflows/`.

- **`run_migration`** (Boolean):
  - **`dry run`** (Default / False): Simulates the migration. It reads the files and outputs a log showing what legacy nodes were found and how many occurrences would be replaced. No changes are written to disk.
  - **`write changes`** (True): Performs the actual search-and-replace updates on the workflow files.

- **`create_backup`** (Boolean):
  - **`yes`** (Default / True): Automatically creates a `<filename>.json.bak` copy of each modified workflow before writing any updates.
  - **`no`** (False): Directly overwrites files without creating a backup.

## Outputs

- **`logs`** (STRING):
  A text log showing:
  - The mode (Write Mode vs. Dry-run)
  - Number of files checked and found
  - Per-file details: skips, found node IDs, number of replacements, backup files saved.
  - Connect this to the **`Show Text [Eclipse]`** node to view the results directly on the canvas.

## Supported Node Mappings

The tool automatically replaces the following legacy node type IDs:

| Legacy Node Type ID (Find) | Unified Node Type ID (Replace) |
|---|---|
| `"Smart Model Loader v2 [Eclipse]"` | `"Smart Model Loader [Eclipse]"` |
| `"IO Checkpoint Loader v2 [Eclipse]"` | `"IO Checkpoint Loader [Eclipse]"` |
| `"Smart Folder v2 [Eclipse]"` | `"Smart Folder [Eclipse]"` |
| `"Pipe IO Sampler Settings v2.1 [Eclipse]"` | `"Pipe IO Sampler Settings [Eclipse]"` |
| `"Pipe IO Sampler Settings v2.2 [Eclipse]"` | `"Pipe IO Sampler Settings [Eclipse]"` |
| `"Pipe IO Sampler Settings v2.3 [Eclipse]"` | `"Pipe IO Sampler Settings [Eclipse]"` |
| `"Smart Sampler Settings v2 [Eclipse]"` | `"Smart Sampler Settings [Eclipse]"` |
| `"Smart Sampler Settings v3 [Eclipse]"` | `"Smart Sampler Settings [Eclipse]"` |
| `"Save Images v2 [Eclipse]"` | `"Save Images [Eclipse]"` |

## Alternative Migration Methods

If you prefer not to use the custom node within the ComfyUI workspace, you can use the command-line script or edit the files manually.

### Option 2: Command-Line Migration Script

A standalone Python script is provided in the `tools` directory: `tools/migrate_workflow.py`.

#### Usage:
Open a terminal in your `custom_nodes/comfyui_eclipse` folder and run:
```bash
python tools/migrate_workflow.py <path_to_workflow_or_directory>
```

- **Arguments:** Pass the path to a single workflow `.json` file or a directory containing workflows.
- **Backups:** The script automatically creates a `.bak` backup copy of each file before editing.
- **Output:** It prints the found legacy node occurrences and logs the successful migration of files.

---

### Option 3: Manual Search-and-Replace

You can open your saved workflow `.json` file in a text editor (e.g., VS Code, Notepad++, Sublime Text) and manually search and replace the JSON keys for the node types using the following mapping:

| Find | Replace |
|---|---|
| `"Smart Model Loader v2 [Eclipse]"` | `"Smart Model Loader [Eclipse]"` |
| `"IO Checkpoint Loader v2 [Eclipse]"` | `"IO Checkpoint Loader [Eclipse]"` |
| `"Smart Folder v2 [Eclipse]"` | `"Smart Folder [Eclipse]"` |
| `"Pipe IO Sampler Settings v2.1 [Eclipse]"` | `"Pipe IO Sampler Settings [Eclipse]"` |
| `"Pipe IO Sampler Settings v2.2 [Eclipse]"` | `"Pipe IO Sampler Settings [Eclipse]"` |
| `"Pipe IO Sampler Settings v2.3 [Eclipse]"` | `"Pipe IO Sampler Settings [Eclipse]"` |
| `"Smart Sampler Settings v2 [Eclipse]"` | `"Smart Sampler Settings [Eclipse]"` |
| `"Smart Sampler Settings v3 [Eclipse]"` | `"Smart Sampler Settings [Eclipse]"` |
| `"Save Images v2 [Eclipse]"` | `"Save Images [Eclipse]"` |

> [!NOTE]
> All other legacy/deprecated nodes (such as basic loaders, old muted/bypassed logic, and v1 settings nodes) have been completely removed in Eclipse v4.0.0. These must be replaced manually on the ComfyUI canvas with their active equivalents.

