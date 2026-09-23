# Load Audio

Load Audio [Eclipse] selects an excerpt from an uploaded file or incoming AUDIO,
with an in-node player and an optional **Stop (Result Review)** control.

For generated songs, connect the generator's full AUDIO to both your full-song
Save Audio node and Load Audio's optional `audio_in`. If the save node passes
AUDIO through, you can connect that output to `audio_in` instead. Connect Load
Audio's trimmed AUDIO to lip-sync and **every audio duration/planning consumer**
in the video branch. Its second output reports the actual excerpt duration in
seconds. Saving the generator's output keeps the saved song full length.

1. Select or upload a fallback file, then connect the generated audio if desired.
2. Set `start_time` and `duration` in seconds. A duration of `0` means to the end;
   an oversized duration stops at the end. Empty excerpts are rejected.
3. Enable **Stop (Result Review)** and queue. Load Audio executes even without a
   downstream consumer, publishes its preview, and stops downstream execution.
4. Audition the excerpt. Change start/duration and use the precise seek slider
   without queueing. The time display is relative to the excerpt. These edits
   change the preview immediately; they change downstream AUDIO on the next run.
5. Disable the stop and queue to release the selected excerpt to lip-sync.

Valid incoming AUDIO always takes priority over the file selector. When a muted
or bypassed source supplies no audio, the selected file is used while the cable
stays connected. A bypass that supplies valid AUDIO still takes priority. Invalid
incoming AUDIO raises an error instead of silently switching songs.

The source label identifies incoming audio or the selected fallback file.
Incoming previews contain the **full source**, so any excerpt can be auditioned
after one execution. A batch previews its first item; all batch items and channels
are preserved in the output. Browser previews use 16-bit PCM; the downstream
tensor retains its original samples, dtype and sample rate without resampling.

Incoming previews are temporary and are never used as a fallback song. Changing
the input connection clears the old preview. If a preview disappears after a
restart, queue again; the saved fallback selection and trim settings are retained.

Review stops remain active on repeated queues. Unchanged upstream generation can
reuse ComfyUI's cache when you queue again, but randomized seeds, changed inputs,
or upstream cache policies can regenerate the song.
