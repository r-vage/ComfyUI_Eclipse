# MiniMax H3 Audio Timeline Nodes

## Segmented Timeline V2

V2 is an additive planner and conditioning path. It does not replace or migrate
the published H3 nodes documented in the legacy section below.

- **MiniMax H3 Audio Timeline Planner V2** creates a strict
  `MINIMAX_H3_SEGMENT_PLAN` schema-version-1 plan plus a tensor-free analysis
  manifest.
- **MiniMax H3 Audio Plan Step V2** resolves one task into the exact native-rate
  audio slice, optional generated continuity, original source/endpoint images,
  frame indices, crop, prompt owner, and conditioning family.
- **MiniMax H3 Segmented Conditioning V2** builds either native-style FL2VA
  positional keyframes or one persistent Ref2VA active-image reference. It never
  mixes the two payload families.

Recommended graph defaults are FL2VA, `warmup_reset`, a two-second hidden
warmup, `first_only`, and `original_image_reset` at technical splits. Stitch
retained decoded frames directly, trim to the exact planned frame count, and
mux the untouched master audio once at the end. No crossfade should hide a
failed transition.

### Planner V2 inputs and outputs

The master `audio` determines the complete duration. `conditioning_audio` is an
optional native-rate guide source, such as a vocal stem; it must match the
master duration within one 24 FPS frame. Wav2Vec `audio_encoder_output` is used
only to place transitions or eligible single-image technical seams near
activity gaps.

Original `image_batch` entries are timeline states. Blank
`manual_transition_times` distributes them evenly; otherwise provide one
strictly increasing comma-separated second value per image after the first.
Transition frame `T` always belongs to the destination interval.

The new controls are:

- `conditioning_family`: `fl2va_keyframes` or
  `ref2va_active_reference`.
- `segment_strategy`: `hard_cut`, `first_last_bridge`, or `warmup_reset`.
- `warmup_seconds`: hidden generated context before a warm-reset visible range;
  default `2.0`.
- `reset_anchor`: `first_only` or the hidden
  `same_first_last_experimental` endpoint.
- `technical_split_source`: default `original_image_reset`, or optional
  `generated_continuation` at technical seams only.
- `technical_seam_style`: `plain_reset` (default) or the opt-in
  `intentional_camera_cut`. The latter is valid only with
  `original_image_reset`.
- `technical_cut_instruction`: editable multiline direction appended only to an
  eligible intentional technical-cut task. Its default asks for a clearly
  different angle and shot size while retaining identity, wardrobe, scene,
  lighting, and ongoing action; it must not be blank.
- `ref_image_size`: `match` or `max` for Ref2VA reference geometry.
- `max_render_frames`: the upper legal `17k+5` render length from 124 through
  362 frames. Warmup and hidden endpoints consume this capacity; a 48-frame
  warmup leaves about 314 visible frames in a maximum render.

Planner outputs are `plan`, `extension_task_count`, `total_frames`,
`base_keep_frames`, `analysis_manifest`, and `report`. The JSON manifest embeds
no tensors. It records planned image transitions, every image/technical seam,
half-open retained ranges, prompt owners, guide positions, and expected source
ownership for external analysis.

### Prompt ownership

Use **String Multiline List [Eclipse]** unchanged and connect its `string_list`
output to the V2 conditioner. Its published behavior ignores blank lines.
Non-empty lines map to source images in order. If a task's image index has no
matching line, the conditioner uses the first prompt, so one line applies to
every image.

Each H3 task receives one complete prompt. A bridge from A to B uses prompt A;
prompt B begins only in the B-owned task at `T`. Prompts do not change within a
task. The V2 conditioner optionally accepts `segment_plan` and `task_index` so
it can append the technical-cut instruction to the exact eligible task. Both
inputs must be connected to enable this behavior. Workflows that leave them
disconnected keep the previous prompt behavior.

### FL2VA behavior

FL2VA tasks tokenize the active original image as `<Picture 1>`. Tasks with a
real or experimental endpoint tokenize `[source, destination]` in that order.
The source is stretched to the generation canvas, while the endpoint uses
aspect-preserving cover resize and center crop. Positional images are
VAE-encoded into `minimax_keyframes` at the local indices supplied by Plan Step.

- `hard_cut` starts the destination task at `T`, places its exact source guide
  at local frame 0, and retains that frame. A hard cut should therefore contain
  one intentional A-to-B discontinuity; a second unrelated `T` to `T+1` jump is
  a failure.
- `warmup_reset` starts from the destination's original image before `T`, maps
  its hidden audio context to the actual preceding master-audio range, discards
  the warmup, and exposes a generated destination-owned frame at `T`.
- `first_last_bridge` gives the A-owned task an ordered A/B pair and places B at
  `T` as the first discarded endpoint frame. A separately warmed, B-only task
  supplies the visible generated frame at `T`.

The default also regenerates long technical segments from their active original
image. The technical-seam choices have distinct goals:

- `generated_continuation` aims for visual continuity by supplying the preceding
  22 generated frames, but repeated chaining can accumulate generation drift.
- `plain_reset` independently regenerates from the active original image and can
  visibly reset framing at a same-image technical split.
- `intentional_camera_cut` keeps that independent reset, source guide at hidden
  local frame 0, and full warmup crop, then asks H3 to turn the first visible
  generated frame into a deliberate editorial change of angle or shot size. It
  does not transform, crop differently, or paste the source image.

The intentional instruction is never added to task 0, image transitions,
first/last bridges, or generated-continuation tasks. `generated_continuation` is
never used to cross an image transition.
Global frame 0 in the default FL2VA path retains the first original source.
`same_first_last_experimental` adds only a discarded same-source endpoint and
never replaces a real bridge destination.

### Ref2VA behavior and checkpoint boundary

Ref2VA requires matching Ref2VA diffusion weights and supports
`warmup_reset` with `first_only` original-image technical resets. Hard cuts and
first/last bridges require FL2VA. Generated-continuation keyframes and
same-first/last endpoints are also rejected in Ref2VA mode, preventing an
unsupported mixed `minimax_refs` plus `minimax_keyframes` payload.

For every task, the conditioner exposes only the active original image as
non-positional `<Picture 1>`. It preserves aspect ratio and never upscales:
`match` limits the image toward the generation canvas area, while `max` permits
up to a 2048-pixel short edge at higher encoder and sampling cost. The image is
passed to the text/vision tokenizer through `minimax_ref_items`, VAE-encoded
into an immutable `minimax_refs` image block, and remains available throughout
the task. Future timeline images are never references. A concise `<Picture 1>`
role sentence is prepended only when the selected prompt does not already name
that picture.

Ref2VA is intended to preserve identity, texture, or composition semantically;
it does not provide an exact target-frame position. Because it has no positional
opening keyframe, its global frame 0 is generated after the planned hidden
warmup rather than copied from the source.

### Plan Step V2 output contract

The V2 Plan Step returns these positions and names exactly:

1. `audio_slice`
2. `continuity_clip`
3. `has_continuity`
4. `source_image`
5. `has_start_guide`
6. `start_guide_frame_index`
7. `last_image`
8. `has_last_image`
9. `endpoint_frame_index`
10. `render_frames`
11. `crop_start_frames`
12. `keep_frames`
13. `prompt_index`
14. `conditioning_family`
15. `ref_image_size`

It rejects legacy plans, malformed ranges, invalid render lengths, inconsistent
audio slices, mismatched image batches, and accumulated-timeline drift.

### Transition and quality analysis

`tools/analyze-minimax-h3-segmented-timeline-v2.py` accepts a rendered video,
the original timeline images, the V2 manifest, an output directory, and an
optional local Hugging Face CLIP-Vision directory. It writes a JSON report, per-
frame CSV, trace plots, and narrow/wide diagnostic contact sheets.

The analyzer compares every frame with both H3 source normalizations and records
CLIP cosine similarity when supplied, low-resolution NCC, SSIM, RGB difference,
edge similarity, luminance/color, sharpness, entropy, adjacent-frame change, and
repeat similarity. Source traces use a median window, a configurable winning
margin, and a six-frame stable-state requirement. Planned repeated A-to-B-to-A
states are resolved by expected interval; unplanned returns within a B interval
remain failures.

Reason codes cover `BACK_SWITCH`, `OSCILLATION`, `SOURCE_FLASH`,
`TRANSIENT_STATE_FLASH`, `GENERATED_SOURCE_GENERATED_FLASH`, `DOUBLE_JUMP`,
`WEAK_TECHNICAL_CUT`, `EARLY_SWITCH`, `LATE_OR_MISSING_SWITCH`,
`UNEXPECTED_CUT`, `BLACK_WHITE_FLAT_FLASH`, `MOTION_STALL`, and
`QUALITY_COLLAPSE`. `EXPECTED_TECHNICAL_CUT` records the one allowed
`T-1`-to-`T` discontinuity at an intentional seam. A second robust jump at
`T`-to-`T+1` that is also material relative to the planned cut remains
`DOUBLE_JUMP`; ordinary first-frame settling is not a second cut. A requested
cut without a measurable change is `WEAK_TECHNICAL_CUT`. Source flashes,
ownership errors, stalls, and unrelated cuts remain failures. Adjacent
discontinuities use rolling
median plus median absolute deviation limits. Literal source flashes additionally
require strict absolute NCC, SSIM, and RGB-difference thresholds. Every threshold
is stored in the report.

Automated metrics identify candidates and enforce structural failures, but live
H3 acceptance still requires visual inspection. Identity similarity alone cannot
decide whether motion, expression, or a model-chosen transition looks natural.

## Legacy published H3 audio-plan nodes

Eclipse includes three nodes for building frame-exact, external-audio MiniMax H3
timelines:

- **MiniMax H3 Audio Timeline Planner** creates the complete 24 FPS task plan.
- **MiniMax H3 Audio Plan Step** resolves one task into audio, image, continuity,
  hidden-anchor, render-length, and crop values.
- **MiniMax H3 Image Prompt Conditioning** encodes the active destination image
  as visual prompt tokens without adding a temporal keyframe.

The workflow that connects these nodes is distributed separately. This README
documents the shipped Eclipse node contracts and the integration rules a
workflow must preserve.

## Timeline Planner

The planner accepts one or more reference images and uses the master audio to
calculate the exact retained timeline length at 24 FPS.

Important inputs:

- `audio`: authoritative master audio for duration and final output timing.
- `conditioning_audio`: optional H3 guide source at its native sample rate. When
  disconnected, the master audio is used. Its duration may differ from the
  master by no more than one 24 FPS frame.
- `image_batch`: reference images in timeline order.
- `audio_encoder_output`: optional Wav2Vec activity features. These locate gaps
  but never replace either audio input.
- `manual_transition_times`: one comma-separated time in seconds for every image
  after the first. Leave blank for even spacing.
- `transition_mode`: `first_last_bridge` or `boundary_switch`.
- `bridge_span`: `short_window` or `full_interval` when bridges are enabled.
- `bridge_window_seconds`: visible short-window duration before each transition.
- `cropped_lookahead_conditioning`: `audio_only` or experimental `future_image`.
- `max_render_frames`: maximum legal H3 task length on the `17k+5` grid, from
  124 through 362 frames.
- `align_to_activity_gap`, `transition_edge`, `search_window_seconds`,
  `min_gap_duration`, and `resume_hold_duration`: optional activity-aware
  transition controls.

Outputs:

- `plan`: strict version-4 `MINIMAX_H3_AUDIO_PLAN` data for Plan Step.
- `extension_task_count`: number of tasks after task 0.
- `total_frames`: exact retained frame count derived from the master audio.
- `base_keep_frames`: frames retained from task 0.
- `report`: readable transition, ownership, audio, and task-range summary.

H3 task lengths are always valid `17k+5` values. The planner uses the shortest
valid render that fits each task and balances longer spans across the minimum
number of tasks.

## Hidden destination handoff

A positive `short_window` bridge retains motion only through the frame before
the transition. Its exact destination endpoint is rendered at the requested
timestamp as the bridge's first discarded frame.

The following destination task begins ownership at that same output frame but
does not receive old-image continuity:

1. Local frames 0–21 are generated, destination-tokenized lead-in frames.
2. Local frame 22 is the exact destination image used only as a hidden H3
   anchor.
3. All first 23 frames are cropped.
4. Generated local frame 23 becomes the first retained frame at the requested
   transition timestamp.

The literal reference image therefore never enters the retained output.
Destination ownership still begins at the requested transition frame. Earlier
destination influence is valid only inside the configured bridge window.

The 23-frame destination crop must not be confused with ordinary continuation.
Ordinary extension tasks retain exactly 22 generated frames from the previous
task as their continuity clip. Destination handoffs receive no such old-image
clip.

## Plan Step

Plan Step validates the version-4 plan and resolves the selected `task_index`.
It returns:

- the exact native-rate conditioning-audio slice, including boundary padding;
- the previous 22 generated frames only for ordinary continuity tasks;
- the current prompt image;
- the optional exact hidden destination anchor and local frame index;
- the optional cropped lookahead image and frame index;
- the optional hidden bridge-endpoint flag and frame index;
- the legal H3 render length;
- the leading crop and exact retained-frame count.

The legacy output names `visible_transition_image`,
`has_visible_transition`, and `visible_transition_frame_index` remain stable for
version-4 workflow serialization. For a destination handoff, that reference is
now a cropped hidden anchor rather than a visible output frame.

Plan Step rejects older plan versions and validates timeline continuity, image
ownership, guide ranges, audio sample ranges, padding, overlaps, hidden anchors,
bridge pairing, lookahead placement, and final-frame coverage before returning
task values.

## Image Prompt Conditioning

The conditioner tokenizes `visible_transition_image` when that optional input is
present; otherwise it tokenizes `current_image`. It uses only the first RGB frame
of the selected image, resizes it to the requested H3 dimensions, and produces
positive conditioning without adding temporal keyframes.

This keeps bridge and destination tasks destination-tokenized while temporal
placement remains the responsibility of explicit H3 guide nodes. Optional
`base_conditioning` keyframes are preserved when supplied.

## Required guide order

Both base and extension sampling paths should assemble guides in this order:

1. Visual prompt conditioning.
2. Selected conditioning audio.
3. Ordinary generated continuity when applicable.
4. Hidden destination anchor when present.
5. Hidden bridge endpoint when present.
6. Cropped lookahead when present.
7. Guider.

Absent image and continuity guides should use lazy branches so they are not
evaluated. Hidden anchors, bridge endpoints, lookahead, audio padding, grid
padding, and leading continuity must all be removed by the planned crops before
frames are appended to the retained timeline.

## Activity alignment

With multiple images, activity alignment searches within
`search_window_seconds` before and after each manual or evenly spaced target.
`activity_resume` selects sustained activity after a qualifying gap;
`silence_start` selects the beginning of that gap. If no valid gap is found, the
original target is retained.

With one image, the same activity features may place ordinary continuation seams
at eligible sentence gaps. The planner still enforces the minimum retained span
needed for legal H3 tasks and falls back to the maximum permitted span when no
gap qualifies.

## Separate audio roles

The master audio remains authoritative for duration and output timing. A
separate conditioning source, such as a raw vocal stem, supplies H3 guide slices
and may also be used to produce Wav2Vec activity features. Guide slices preserve
that source's native sample rate.

Vocal-stem conditioning can reduce reactions to instruments, but it is not an
absolute lip lock. Separation bleed, visual prompting, and model behavior can
still produce mouth motion during quiet or instrumental passages.

## Compatibility

- Timeline FPS is fixed at 24.
- Current plan kind: `MINIMAX_H3_AUDIO_PLAN`.
- Current plan version: `4`.
- Ordinary generated overlap: 22 frames.
- Destination handoff crop: 23 frames comprising 22 generated lead-in frames
  and one exact hidden anchor.
- Plan Step output positions and legacy transition-output names remain stable.
