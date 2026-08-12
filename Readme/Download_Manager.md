# Download Manager (Beta)

The Eclipse Download Manager is a standalone interface for inspecting and downloading model files from CivitAI and Hugging Face. It does not modify the Smart LM Registry Editor, and Hugging Face downloads remain exclusive to this modal.

## Opening the manager

- New menu: choose **Download Manager (Beta)** from the left toolbar. The launcher sidebar closes immediately while the modal stays open.
- Eclipse menu: choose **Eclipse → Download Manager (Beta)**.
- Classic menu: use the **Download Manager (Beta)** button in the legacy top menu.

On desktop, drag the dialog's lower-right edge to resize it. The manager remains viewport-bounded and keeps its fixed responsive layout on narrow/mobile screens.

## Inspecting provider files

Choose a provider, enter a locator, and select **Get File List**.

CivitAI accepts:

- AIR identifiers, including exact `+fileId` AIRs;
- full SHA-256 values;
- model-version URLs; and
- canonical model download URLs.

AutoV1/2/3, CRC32, and BLAKE3 are not accepted as provider identities. Every downloadable file in the resolved version is shown, but files without an authoritative SHA-256 cannot be selected.

CivitAI sometimes publishes the same REST/download basename for several precision variants. The manager performs the same bounded, identity-checked author-filename lookup as Smart Model Loader and uses the result as the editable local filename suggestion. The original REST filename remains visible and stays in the immutable provider identity. If author metadata is unavailable or fails its file-ID/SHA-256 checks, a precision-aware, collision-safe fallback is used instead.

Hugging Face accepts:

- `owner/repository` IDs;
- repository URLs;
- canonical `/blob/` or `/resolve/` file URLs; and
- an optional revision.

The revision resolves to one immutable 40-character commit before files are listed. LFS and Xet-backed files use their upstream SHA-256. Regular Git files use their Git blob identity; users do not need to supply hashes.

## Choosing destinations

The destination grid is populated from ComfyUI's live registered model folders. It never accepts an arbitrary filesystem path and excludes `custom_nodes`. Repeated registrations of the same root appear once; distinct roots that share a folder name receive numbered labels such as `diffusion_models (1)` and `diffusion_models (2)`.

Use the suggestion as a starting point, then confirm ambiguous model files. Bulk controls apply only to rows that are already selected: select the files first, then choose the bulk category, registered root, or conflict policy. Hover the bulk controls or focus the adjacent help text for the same guidance. You can still override assignments per row. Subfolders must be relative and traversal-free; local filenames must remain basenames and preserve the provider file extension.

Default format policy:

- Safetensors and SFT are accepted by compatible model categories.
- GGUF is accepted only for diffusion-model and text-encoder categories.
- `.pth` is accepted for **Upscale Models**.
- `.pt` is accepted for **Embeddings**.
- `.pth` is not accepted for **Latent Upscale Models** by default.
- `.ckpt`, `.pkl`, `.bin`, and other `.pt`/`.pth` uses require the administrator's **Allow Legacy Model Formats** override.

Unsupported or informational rows remain visible through the grid toggle and show why they cannot be selected.

## Persistent queue

New and imported entries are added in the **ready** state and do not download automatically. Select one or more ready entries in the Queue tab and choose **Start Selected**; the manager then processes the started transfers one at a time. **Remove Selected** deletes ready or finished queue records without deleting downloaded model files. Queued or active transfers must finish or be cancelled before their records can be removed. A cancelled or failed entry with retained partial data must be retried or use **Delete Partial** before its queue record can be removed.

The manager stores its schema-versioned queue atomically in the gitignored `download_manager/` directory. Queue records contain immutable provider identity, destination assignment, expected provider digest, local SHA-256, byte size, conflict policy, progress, timestamps, and sanitized errors. They never contain credentials, authorization headers, or temporary signed URLs.

Interrupted work returns to the active queue after restart. Download Manager cancellation also retains already transferred bytes so **Retry** can resume the explicitly selected provider file. A partial transfer resumes only when the remote server returns consistent range metadata. **Delete Partial**, shown beside Retry when retained data exists, explicitly discards those bytes and makes the next attempt start from zero. Hashing, verification, locking, and atomic promotion are intentionally non-abortable. This resumable-cancellation policy is specific to the standalone Download Manager; Smart Model Loader cancellation keeps its existing cleanup behavior.

Conflict policies are:

- `skip`: keep an existing destination and record whether it matches provider identity;
- `rename`: choose a contained collision-free local filename; and
- `overwrite`: atomically replace the existing file only after provider verification.

Every completed transfer receives a local SHA-256 sidecar. CivitAI downloads also retain AIR and authoritative SHA-256 metadata.

## Download bundles

Queue selections can be exported as download bundles. Bundles preserve immutable provider identity, expected digest, category, registered root index, relative path, local filename, local SHA-256, and conflict policy.

## Security boundary

Provider inspection and queue mutations use bounded JSON requests, same-origin browser checks, and loopback-only global access in ComfyUI multi-user mode. Destinations are resolved from server-owned folder registrations, reject absolute paths, traversal, symlinks, and escapes, and are promoted only after immutable provider verification.

The manager does not make unsafe pickle formats safe. Leave the legacy-format override disabled unless an administrator has independently trusted those artifacts.
