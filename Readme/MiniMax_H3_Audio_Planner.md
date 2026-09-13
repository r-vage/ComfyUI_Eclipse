# MiniMax H3 Audio Timeline Nodes

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

