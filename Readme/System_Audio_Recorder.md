# System Audio Recorder

`System Audio Recorder [Eclipse]` captures the sound currently playing through a
Linux or Windows output device and releases it into the workflow as ComfyUI
`AUDIO` after you stop recording.

## Basic use

1. Add the recorder and connect its `audio` output to an audio preview, save, or
   video node.
2. Choose **Default** or a detected output device. Use **Refresh Devices** after
   plugging in or changing audio hardware.
3. Choose `wav`, `wav + mp3`, or `mp3`, then set the MP3 bitrate when applicable.
4. Leave **strip start silence** enabled to remove quiet lead-in, or disable it to
   keep the complete capture. The default `-70 dBFS` threshold can be adjusted
   from `-96` to `-20 dBFS`.
5. Press **Start**. Capture begins before Eclipse submits the workflow. Execution
   waits at this node while recording continues.
6. Press **Stop**. Eclipse finalizes the requested files without cancelling the
   queue, then passes the captured 48 kHz stereo audio to downstream nodes.
7. Change the prefix, format, bitrate, trim toggle, or threshold and press
   **Update Output** to publish another comparison from the same capture. Eclipse
   prepares the new files before automatically submitting the workflow again.

Only one Eclipse system-audio recording can be active at a time. There is no
recording-duration limit; audio is streamed to a private WAV staging file rather
than accumulated in memory.

## Inputs and outputs

- `filename_prefix` is relative to ComfyUI's output directory. Eclipse appends a
  five-digit collision-safe counter and the selected extension.
- `format` controls which files are published. `wav_path` or `mp3_path` is empty
  when that format was not requested.
- `mp3_bitrate` supports 128, 192, 256, and 320 kbps and is disabled in WAV-only
  mode.
- `strip_start_silence` checks independent 10 ms RMS windows on both channels.
  When either channel reaches the selected threshold, Eclipse keeps 50 ms before
  that window and removes only the earlier lead-in. The complete recording is
  retained when stripping is disabled or no window reaches the threshold.
- `audio` contains the latest selected PCM range, not decoded lossy MP3 audio.
- `duration` is the latest processed duration in seconds.

The complete capture remains in a private staging area while the node exists, so
**Update Output** continues to work after a browser reload in the same ComfyUI
process. Each changed processing configuration creates new collision-safe files;
earlier published comparisons are preserved. Retrying an unchanged configuration
after a queue failure reuses its prepared result instead of making duplicates.

MP3 encoding uses PyAV's bundled `libmp3lame` support and does not launch an
external `ffmpeg` executable. If processing fails, no partial result is kept and
the private source remains available for another **Update Output** attempt.

## Platform notes

- Linux uses PulseAudio/PipeWire monitor capture through SoundCard.
- Windows uses WASAPI loopback capture through SoundCard.
- macOS is not supported because SoundCard does not provide native CoreAudio
  system-output loopback. A separate virtual audio device would require a
  different capture workflow.

Deleting the node, replacing its recording, session eviction, or normal ComfyUI
shutdown removes the private source but preserves every published file. An
interruption or failed prompt submission aborts an active capture; after Stop,
downstream failures leave the source available for reprocessing.
