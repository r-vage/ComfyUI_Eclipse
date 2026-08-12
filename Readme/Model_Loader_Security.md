# Diffusion Model Loader Security

Eclipse 4.3.1 applies one security and lifecycle policy to Smart Model Loader,
Model Loader, Model Loader Pipe, the VAE loaders, loader templates, integrity
operations, and CivitAI acquisition. Existing node IDs, sockets, pipe keys,
workflow fields, templates, and endpoint URLs remain compatible.

## Local file policy

Every selected checkpoint, diffusion model, GGUF, CLIP, VAE, audio VAE, and
LoRA is resolved through its declared ComfyUI folder role. The resolved target
must be a readable regular file inside that role. Absolute paths, traversal,
symlinked files or directories, and unsupported extensions are rejected before
memory cleanup or backend deserialization.

Safetensors (`.safetensors` and `.sft`) and GGUF are permitted by default.
Pickle-capable `.ckpt`, `.pt`, `.pth`, and `.bin` files are denied by default.
An administrator can enable them locally with **Eclipse → Generic → Allow
Legacy Model Formats**. The override applies immediately, is stored only in
the private Eclipse configuration, and is never serialized into workflows or
templates. Enabling it means accepting the deserialization risk of every
legacy artifact selected by a workflow.

## Integrity modes

Integrity mode applies to the primary checkpoint, UNet, Nunchaku, or GGUF file
selected by `model_type`, matching the node's single AIR/SHA editor. External
CLIP, VAE, audio VAE, and LoRA files remain subject to unconditional path,
format, containment, and symlink checks.

- `off` keeps primary-model integrity hashing disabled, while path and format
  enforcement remains active for every component.
- `sidecar` computes a local SHA-256 baseline for the primary model. A local
  baseline detects later changes but does not establish who published the file.
- `verify` compares the primary model against trusted SHA-256 metadata when it
  is available and aborts before loading if the digest differs. When trusted
  metadata is absent, Eclipse records a local baseline and continues loading;
  that baseline can detect later changes but does not prove model provenance.

Hash metadata is versioned and records the file size, nanosecond modification
time, reference type, folder role, and relative path. Hashing checks stable
file statistics before and after the read. Legacy text sidecars remain
readable and are migrated only after their digest has been verified. Expected
metadata and templates are updated with locked, flushed, atomically replaced
JSON files; malformed existing JSON is left untouched for diagnosis.

## CivitAI acquisition

CivitAI installation requires an upstream SHA-256 and consistent AIR model,
version, and file identities. Eclipse downloads through an exact file-ID URL,
validates every DNS result and redirect as a public address, limits redirects
and response sizes, checks disk space, and accepts a resumed transfer only
when the server proves the requested byte range. Bytes are staged, flushed,
fsynced, hashed, and atomically promoted. An existing destination is reused
only when its digest matches the CivitAI SHA-256.

The selected download target role is also a file-category boundary. Model
roles exclude VAE, encoder, LoRA, training-data, workflow, and configuration
files; auxiliary roles accept only their matching dedicated component or a
primary model whose AIR identifies the same auxiliary family. Precision and
GGUF quantization requests are exact and fail with compatible choices instead
of falling back to another file. The node's download button can cancel only
the active network-transfer phase; the abort window closes before hashing,
verification, maintenance locking, or promotion begins, and cancellation
removes the partial transfer.

Do not treat a SHA-256 calculated only from a local file as proof that the file
came from CivitAI or another publisher. Provenance requires independently
obtained expected metadata.

## Endpoint and maintenance boundaries

Mutation endpoints retain their existing URLs but accept only bounded JSON
objects. Cross-origin browser mutations are rejected. In ComfyUI multi-user
mode, global mutations are restricted to loopback clients. Hashing, downloads,
model scans, and template persistence run outside the aiohttp event loop.

Deletion and promotion require an idle prompt queue and a process-wide
maintenance lock. Template deletion resolves only the exact selected model,
rejects symlinks and paths outside its role, and tombstones the template,
model, and sidecars as a transaction before removal. Verified replacement
promotion likewise tombstones the old target and rolls it back if promotion
fails.

## Threat model and residual boundaries

| Threat | Enforced control | Residual boundary |
| --- | --- | --- |
| Malicious pickle model | Legacy formats default-denied | Local administrator may explicitly enable them |
| Traversal or symlink escape | Canonical role containment and symlink rejection | Security of configured ComfyUI model roots |
| Malformed workflow or template | Type, enum, bounds, feature, JSON, and path validation | Backend-specific semantic compatibility |
| Forged or incomplete CivitAI file | Required SHA-256, exact AIR/file identity, staged verification | Trust in CivitAI metadata and TLS/public DNS |
| SSRF through redirects | Public-address validation on each request target | Host-network policy outside Eclipse |
| Concurrent load and deletion | Active-load counter, idle queue requirement, maintenance lock | Other extensions mutating model files directly |
| Partial or corrupt persistence | Locked atomic JSON writes with fsync | Filesystem and hardware guarantees |
| Accidental broad deletion | Exact template-selected target and transactional tombstones | Manual filesystem maintenance outside Eclipse |
| Event-loop or resource exhaustion | Worker offload plus request, header, redirect, file-size, and disk bounds | Deliberately permitted very large local model hashing |
| Endpoint misuse | Same-origin checks and multi-user loopback restriction | Non-browser local processes already trusted by the host |

## Opt-in compatibility ledger

The deterministic test suite covers policy, identity, persistence, concurrency,
and controlled backend contracts without loading real model weights. The "Not
run" entries below apply only to the separate real-model qualification column;
they do not mean that the deterministic contract tests were skipped. Real-model
qualification is opt-in because it can require network access, large downloads,
GPU memory, optional Python runtime packages, and production model weights.
Eclipse vendors the required ComfyUI adapter code for Nunchaku and GGUF, so
separate ComfyUI-Nunchaku and ComfyUI-GGUF custom-node installations are not
required.

| Path | Automated contract coverage | Real-model qualification in 4.3.1 |
| --- | --- | --- |
| Standard checkpoint / UNet | Yes | Not run |
| Nunchaku Flux / Qwen / ZImage | Validation and vendored adapter paths | Not run with real weights; requires the optional `nunchaku` Python package and a compatible NVIDIA GPU |
| GGUF | Validation and vendored adapter paths | Not run with real weights; requires the `gguf` Python package declared by Eclipse |
| External CLIP / VAE / audio VAE | Resolution and loader paths | Not run |
| LoRA / sampling / BlockSwap | Shared adapter and policy paths | Not run with production weights |
| CivitAI network download | Controlled HTTP/identity/resume/digest tests | No live CivitAI download run |
| Linux / Windows / macOS | Platform-neutral path and persistence tests | Linux-only local validation; no cross-platform qualification |

Operators qualifying a real configuration should record the ComfyUI version,
Eclipse version, operating system, device, model identity and SHA-256, optional
backend versions, load path, and observed result. A skipped entry is not a
failure, but it must not be represented as qualified.
