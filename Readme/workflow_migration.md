# Workflow Migration Tool

Use this tool to update old Eclipse node names or replace supported deprecated nodes with ComfyUI's built-in nodes. Existing workflows still load with the deprecated Eclipse nodes, so conversion is optional.

Supported versioned Eclipse IDs now migrate automatically when opening a workflow JSON or importing a generated image, including nodes inside subgraphs. Older Seed and Seed 32-bit layouts retain their seed values and bit depth, and older Image Selector layouts retain manual confirmation. Legacy Smart Model Loader and IO Checkpoint Loader IDs are handled by the current Smart Model Loader pack.

Sampler-pipe v2.1/v2.2/v2.3 retain their original Legacy schemas and output order. Older Smart Sampler Settings layouts with separate upscale steps and denoise load as Smart Sampler Settings (Legacy), preserving those channels and the seed. Do not rename these sampler pipes to the current unversioned node: their output sockets differ.

Automatic import changes the loaded workflow in memory; the source JSON or image stays unchanged. Save the loaded workflow to keep the updated format. The tool below remains useful for batch migration and optional conversions to built-in nodes.

Image Crop by Mask now uses `mask_blur` in place of `mask_expand` and `divisible_by` in place of `padding`. Widget positions and saved values stay the same. Workflow import also updates connected input names, named widget values and promoted subgraph controls. For saved API prompts, run the migration tool before submitting them to ComfyUI.

The former expansion value now controls Gaussian blur in output pixels: `0` disables blur. Blur softens only the output mask; `context_expand` controls framing and zoom. Existing nonzero expansion values therefore become blur radii.

## From ComfyUI

1. Add **Workflow Migration Tool** from **Eclipse / Tools**.
2. Set **path** to a saved workflow `.json` file or a folder. Folders are scanned recursively.
3. Leave **run_migration** on **dry run** first. Connect **logs** to ComfyUI's **Preview as Text** to see the proposed changes and any nodes needing review.
4. Switch to **write changes** when ready. Keep **create_backup** enabled.
5. Reopen the saved workflow to see the replacements. An already-open canvas is not updated automatically.

Backups are saved beside each workflow as `.json.bak`. If a backup already exists, the tool uses `.json.bak.1`, `.json.bak.2`, and so on, keeping earlier backups intact.

## Built-in replacements

| Eclipse node | ComfyUI replacement |
|---|---|
| Integer | Int |
| Boolean | Boolean |
| String | Text |
| String Multiline | Text (Multiline) |
| Show Text | Preview as Text |

Saved values, node positions, custom titles and output connections are retained. Int uses **fixed** after generation, so migration does not start incrementing or randomizing its value.

For **String Multiline**, a connected input is connected to the built-in Text value input. The incoming text replaces the saved local text; it is no longer joined with that text as a prefix. If both old inputs are connected, the node is left unchanged and listed for review because the replacement has only one input.

**String Multiline List** and **Show Text Stop** are not converted. **Universal Block Swap** stays available under Legacy and is reported for manual review; it has no direct built-in equivalent.

Preview as Text uses ComfyUI's own display behavior. Lists, non-ASCII text and saved preview displays can differ from Eclipse's Show Text. Review workflows that use the preview's text output as data.

Use a current ComfyUI installation that includes these built-in nodes.

## Older workflows and custom mappings

The existing Eclipse and RvTools mapping files remain supported. The default **mapping_file** setting loads:

- `tools/migration_eclipse.txt`
- `tools/migration_rvtoolsv1.txt`
- `tools/migration_rvtoolsv2.txt`

You can enter other mapping file paths, separated by commas. Each non-comment line uses `old node ID|new node ID`. Built-in conversions need the Eclipse mapping file and the migration tool, since changing the node name alone is insufficient.

The tool edits node records in saved workflows, subgraph definitions and API prompt exports. Matching words inside prompts, notes and custom titles are left alone. Invalid JSON and unrelated JSON documents are not rewritten. Older node-name mappings are listed in [migration_mapping.txt](../tools/migration_mapping.txt).

## Command line

From the Eclipse folder:

```bash
python tools/migrate_workflow.py /path/to/workflow.json
```

You can also pass a folder or an optional comma-separated mapping file list. The command-line tool writes changes and always creates backups; use the ComfyUI node for a dry run.
