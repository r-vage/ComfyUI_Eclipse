# Exact frame timelines

`Decode and Append Timeline [Eclipse]` uses the native VAE decode, retains the
requested frame range, and writes those pixels to temporary NumPy chunks. Its
`ECLIPSE_FRAME_TIMELINE` output carries chunk ownership, ranges, dimensions,
dtype and FPS. Appending never loads or concatenates earlier scenes.

Connect the base timeline to the loop's carried value, and connect the extension
node's `timeline` input to that carried value. Each extension returns a new
immutable descriptor sharing the earlier chunks. Decode memory still depends on
the size of one generated scene; this node does not introduce temporal tiling.

`MiniMax H3 Audio Plan Step V2` accepts optional `timeline` instead of
`previous_frames`. It validates accumulated counts and FPS, and reads only the
last 22 exact frames when generated continuity is requested. Original IMAGE
workflows remain supported.

`Preview Video` accepts the timeline in its existing source socket. It streams
**all completed scenes**, uses stored FPS and the separately connected audio,
and returns the same timeline. Preview MP4s are viewing copies; generation and
final rendering never decode pixels from them.

`Trim Frame Timeline` selects an exact frame range without copying pixels. It
also returns width, height and frame count. Connect its output directly to
`Render Lyric Captions`, `Save Video`, or `Save Video with Generation Data`.
Caption FPS must match timeline FPS. Caption background frame zero corresponds
to the beginning of the already selected audio excerpt; retain the existing
full-song caption trim offset. Save nodes retain their codec, CRF, preset,
metadata and audio controls. Duration trimming works without reconstruction;
loop matching/blending is not supported for timelines.

For a plan with no extensions, `Frame Timeline Loop Gate` blocks the Easy-Use
body. Connect its `has_extensions` output to a lazy `IF A Else B`, choosing the
loop result for true and the original base timeline for false. This lets the base
preview and final export complete without attempting a nonexistent extension.

Float16, bfloat16, float32 and float64 round-trip exactly. Bfloat16 is stored as
its unchanged 16-bit representation. No intermediate pixel quantization, lossy
compression, precision changes or frame interpolation are introduced.

Chunks live under ComfyUI's temporary directory. Four minutes at 512×896,
24 fps, RGB float32 requires about **29.5 GiB**, plus preview files. Cumulative
preview encoding still grows in work as the video gets longer. Each read mapping
closes after use; interrupted or failed chunk writes remove their incomplete
files. Shared chunks stay alive while any cached timeline, loop state or output
references them, and are removed when their final owner is released. Clearing
execution caches releases ownership only if no other references remain.

This is execution-local storage, not restart recovery. Abrupt process termination
can leave temporary files for normal ComfyUI temp cleanup. Keep sufficient disk
space and restart ComfyUI after updating Eclipse to register the new nodes.

## If disk space runs low

Finishing a run does **not** immediately delete its exact frames. ComfyUI can
keep them cached so you can change captions or export again without repeating
generation. Accumulated preview MP4s also take space until temp cleanup.

Timeline storage errors show the **actual temporary folder**. By default this
is `ComfyUI/temp`; it can differ when a custom temporary directory is configured.
Check that folder's drive, which may differ from the drive holding final exports.

To reclaim space manually:

1. Keep any final videos you want from `output`. Finish or cancel the current
   job, then **stop the ComfyUI backend**, not just the browser tab.
2. In the temporary folder, remove the following only when no longer needed:

   | Temporary item | What deleting it removes |
   | --- | --- |
   | `EclipseFrames_*` folders containing `frames.npy` | Exact cached timeline pixels, usually the largest files. Later caption changes or re-exports require generation again. |
   | `EclipseVideo_temp_*.mp4` | Accumulated viewing previews. Exact frame chunks remain separate. |
   | `EclipseLyrics_*.mp4`, `EclipsePreview_*.mp4` | Temporary caption renders and VIDEO preview copies. Keep the final exported copy from `output` first. |
   | `EclipseLyricsBackground_*` folders | Temporary background-video excerpts left behind by an interrupted process. |

3. Restart ComfyUI before queueing again. Do not delete live frame chunks while
   the backend is running: cached nodes may still point to those files.

Leave `input`, `models`, and final videos in `output` alone. Clearing execution
caches can release exact chunks automatically when their last reference goes
away, but unloading models or merely completing the queue does not guarantee
that the temporary data is gone. These files do not provide restart recovery,
and preview MP4s cannot restore the exact timeline pixels.

For long runs, reserve about 30 GiB per four-minute float32 timeline at
512×896/24 fps, plus previews and export space. You can configure ComfyUI's
`--temp-directory` on a drive with more space before starting another run.
