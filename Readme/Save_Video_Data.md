# Save Video with Generation Data [Eclipse]

`Save Video with Generation Data` is a standalone MP4 output node for an IMAGE
frame batch, optional AUDIO, and an optional Generation Data `PIPE`. It combines
the trim and loop controls of Save Video with Image Save-style workflow metadata,
A1111 generation parameters, feature chips, filename placeholders, model hashes,
and JSON sidecars. The existing Save Video and Save Images nodes are independent
and unchanged.

## Inputs and output

Connect `images` directly; it is required. `audio` and `pipe_opt` are optional.
The node returns the saved frame batch after any enabled trim or loop processing
and displays the resulting MP4 in its resizable video preview.

The default `filename_prefix` remains `video/ComfyUI_Eclipse`. Relative folders
are supported, so `video/%today/%basemodel_%seed` saves below the configured
ComfyUI output directory. Unsafe characters are replaced, traversal is contained,
and absolute prefixes outside the output directory are rejected.

## Feature chips

The chip selection is serialized through hidden socketless boolean widgets.
Defaults are `embed_workflow`, `save_gen_data`, and `trim`.

| Chip | Default | Behavior |
| --- | --- | --- |
| `embed_workflow` | on | Embed the raw ComfyUI `prompt`, `workflow`, and other workflow metadata as MP4 tags. |
| `save_gen_data` | on | When a PIPE is connected, embed an A1111-compatible `parameters` tag. |
| `remove_prompts` | off | Blank positive and negative prompts in `parameters` while retaining settings and hashes. |
| `save_json` | off | Save the workflow beside the MP4 with the same resolved prefix and counter. |
| `loras_to_prompt` | off | Append PIPE LoRA names and weights to the positive prompt in `parameters`. |
| `trim` | on | Show and apply trim/loop controls. Disabling it forces `trim_mode` to `none`. |

Prompt removal takes precedence over LoRA insertion. With `remove_prompts` and
`loras_to_prompt` both enabled, neither prompts nor LoRA tags appear in the prompt
fields, but hashes and all non-prompt generation settings remain.

When `trim` is disabled, `trim_mode` and every loop widget are hidden. When it is
enabled, `trim_mode` is shown. `loop_search_pct`, `loop_metric`, and
`loop_trim_start` appear only for `loop_match` or `loop_match_blend`, while
`loop_blend_frames` appears only for `loop_match_blend`.

Changing chips or trim modes does not resize the node. The video preview expands
or contracts within the existing node height as control widgets are hidden or
shown, preserving the size selected by the user.

## Generation metadata

Connect an `IO Generation Data` output (or another compatible dict/tuple PIPE) to
`pipe_opt`. With `save_gen_data` enabled, the MP4 `parameters` tag includes:

- positive and negative prompts;
- sampler and scheduler;
- steps, CFG, seed, denoise, and CLIP skip;
- encoded frame dimensions;
- model and VAE names;
- short SHA-256 hashes for models, VAEs, LoRAs, and embeddings.

The node reads existing integrity sidecars when available and otherwise uses the
same hash cache/sidecar behavior as Eclipse image metadata. LoRA and embedding
references are discovered from prompts, and PIPE LoRA names are also considered
for hashing.

Without a PIPE, generation metadata and generation-only filename values are
skipped. `embed_workflow` and `save_json` still work because they use ComfyUI's
raw hidden prompt/workflow data. ComfyUI's global metadata-disable option prevents
MP4 tag embedding but does not disable an explicitly selected JSON sidecar.

For Civitai workflows whose raw ComfyUI graph is not understood by the Comfy
parser, disable `embed_workflow` to leave the A1111 `parameters` tag as the
unambiguous generation-data source.

## Filename placeholders

Placeholders are expanded after the optional PIPE is read. Longer names are
resolved first, so `%denoise` and `%clip_skip` cannot be partially consumed by
short date tokens.

| Placeholder | Value |
| --- | --- |
| `%today`, `%date` | Local date as `YYYY-MM-DD` |
| `%time` | Local time as `HHMMSS` |
| `%Y`, `%y` | Four- or two-digit year |
| `%m`, `%M` | Month |
| `%d`, `%D` | Day |
| `%H`, `%S` | Hour and second |
| `%basemodel` | First PIPE model basename without its extension |
| `%model` | First PIPE model value |
| `%seed` | Seed |
| `%sampler_name` | Sampler name |
| `%scheduler` | Scheduler |
| `%steps` | Step count |
| `%cfg` | CFG scale |
| `%denoise` | Denoise, or guidance when denoise is absent |
| `%clip_skip` | CLIP skip |

Date and time placeholders work without a PIPE. Any empty generation placeholder
falls back to its readable name without `%`; for example, `%seed` becomes `seed`.

Examples:

```text
video/%today/%basemodel_%seed
video/%Y-%M-%D/%sampler_name_%scheduler_%steps
```

The resolved prefix is used consistently for the MP4 counter, output filename,
preview response, and `.json` sidecar. Files use the Save Video naming pattern
`<resolved-prefix>_00001_.mp4`.

## Trim and loop modes

- `none`: preserve video and audio lengths.
- `video_to_audio`: shorten the frame batch to the audio duration.
- `audio_to_video`: shorten audio to the video duration.
- `shortest`: shorten both sides to the shorter duration.
- `loop_match`: trim at the tail frame that best matches the start.
- `loop_match_blend`: find the loop point and crossfade the tail toward the start.

Loop matching supports NCC, MSE, luminance MSE, and gradient MSE. Enabling
`loop_trim_start` searches both the head and tail for a tighter matching pair.
