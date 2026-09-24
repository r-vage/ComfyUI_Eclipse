# Load Audio

Load Audio [Eclipse] selects an excerpt from an uploaded file or incoming AUDIO,
with an in-node player and an optional **Stop (Result Review)** control.

For generated songs, connect the generator's full AUDIO to both your full-song
Save Audio node and Load Audio's optional `audio_in`. If the save node passes
AUDIO through, you can connect that output to `audio_in` instead. Connect Load
Audio's trimmed AUDIO to lip-sync and **every audio duration/planning consumer**
in the video branch. Its second output reports the actual excerpt duration in
seconds. Saving the generator's output keeps the saved song full length.

1. Choose **Audio source**: **Auto**, **Selected file**, or **Incoming audio**.
   Select or upload a file when using the file source, and connect generated
   audio to `audio_in` if desired.
2. Set `start_time` and `duration` in seconds. A duration of `0` means to the end;
   an oversized duration stops at the end. Empty excerpts are rejected.
3. Enable **Stop (Result Review)** and queue. Load Audio executes even without a
   downstream consumer, publishes its preview, and stops downstream execution.
4. Audition the excerpt. Change start/duration and use the precise seek slider
   without queueing. The time display is relative to the excerpt. These edits
   change the preview immediately; they change downstream AUDIO on the next run.
5. Queue unchanged again to continue from the reviewed excerpt, or disable the
   stop and queue to release it to lip-sync.

**Audio source** changes the player without queueing:

- **Auto** prefers incoming AUDIO. Muting the connected source restores the file
  controls and file preview while the cable stays connected. Bypassed sources
  use incoming audio when an AUDIO path remains; otherwise they fall back to the
  file. This also follows reroutes and Eclipse Set/Get connections.
- **Selected file** immediately previews the chosen file even with `audio_in`
  connected. On the next queue, Load Audio uses the file without requesting its
  upstream input. Other output nodes can still request that upstream branch.
- **Incoming audio** requires a usable input and reports when it is unavailable.
  It never silently selects the file.

The file dropdown and upload button are hidden while using incoming audio.
Switch to **Selected file** to choose or play a file at any time. Source and trim
changes affect downstream AUDIO on the next queue. New generated audio needs one
execution before its preview exists; previously generated audio can be auditioned
immediately. Custom routers whose output depends on execution may still need a
queue for automatic fallback; use **Selected file** to override them immediately.
Invalid incoming AUDIO raises an error instead of silently switching songs.

The source label identifies incoming audio or the selected fallback file.
Incoming previews contain the **full source**, so any excerpt can be auditioned
after one execution. A batch previews its first item; all batch items and channels
are preserved in the output. Browser previews use 16-bit PCM; the downstream
tensor retains its original samples, dtype and sample rate without resampling.

Incoming previews are temporary and are never used as a fallback song. Changing
the input connection clears the old preview. If a preview disappears after a
restart, queue again; the saved fallback selection and trim settings are retained.

An unchanged review queue can continue using ComfyUI's cache. Changed inputs
require review again; randomized seeds or upstream cache policies can regenerate
the song.
