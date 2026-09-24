# Lyric captions

**Render Lyric Captions [Eclipse]** accepts full-song `audio`, socket-only
`lyrics`, required FLOAT sockets `trim_start` and `duration`, optional full-song
`vocals`, and optional socket-only `corrected_timing`.
Lyrics may be plain text, YuE2 `{"style": "…", "lyrics": "…"}`, MiniMax
`{"caption": "…", "lyrics": "…"}`, or either splitter's public lyrics output.
Recognized section/production headings are removed; original wording, punctuation,
line boundaries, repeated sections, and unknown parenthetical asides are retained.

The outputs are **video, srt, report, timing_json, cleaned_lyrics**, in that order.
The VIDEO is file-backed with captions burned into the original soundtrack.
Timing JSON always describes the full song; SRT describes the rendered clip.
Connect VIDEO directly to **Save Video [Eclipse]**, or use **Preview Video
[Eclipse] → Save Video [Eclipse]** for review. Preview and Save accept IMAGE or
VIDEO through their existing `images / video` socket.

VIDEO inputs keep their own soundtrack, frame rate, and duration; the FPS,
optional AUDIO, and frame trim/loop controls are for IMAGE input. A connected
separate AUDIO is ignored when VIDEO is selected. Saving honors CRF/preset and
streams file-backed sources without expanding the complete video into tensors. Outputs pass through
VIDEO for VIDEO input and processed IMAGE frames for IMAGE input. Native Save
Video is also compatible. **Avoid GetVideoComponents for long videos:** four
minutes at 720×1280/24 fps require about 59 GiB for float32 IMAGE frames alone.

The renderer has one optional **background image / video** socket (`background`):

- Unconnected: use `background_color`.
- One IMAGE: hold it throughout the clip.
- An IMAGE batch or list of images/batches: play every frame in order at caption `fps`.
- One VIDEO: stream its timestamps, sampled at caption `fps`.

Background playback starts at the rendered clip's beginning, even when the song
is trimmed. Short backgrounds hold their last frame; frames beyond the rendered
duration are discarded. The supplied AUDIO remains the soundtrack; background
VIDEO audio is ignored. Width and height are explicit, even pixel dimensions.
Images of different sizes resize individually, and conversion/encoding proceeds
incrementally without concatenating the sequence or decoding an entire VIDEO
into tensors. Existing input tensors remain owned by their upstream nodes.

An image list executes the caption node once and produces one VIDEO. Other inputs
(audio, vocals, lyrics, corrections and settings) must each contain one value.
Empty/malformed images, mixed IMAGE/VIDEO lists and multiple VIDEO objects are
rejected.

## Muting captions

Use **Any Multi-Switch Mixed [Eclipse]** in `Eclipse > Router`:

1. Connect Render Lyric Captions `video` to the switch's first input, `any_1`.
2. Connect the background IMAGE to `any_2`.
3. Connect the switch output to Save Video [Eclipse] `images / video`.
4. Connect the trimmed master AUDIO to Save Video's `audio` input.

With the renderer active, Save uses the captioned VIDEO and its embedded
soundtrack. Mute the renderer to save the IMAGE with the connected AUDIO.
A single background image is held for the audio's full duration. Keep the
fallback audio's start and duration aligned with the renderer's trim settings.

Use **Mute**, rather than Bypass, for this fallback. The switch's sockets accept
independent types; the destination must support whichever type is selected.
Preview Video [Eclipse] also accepts both types and ignores separate AUDIO for
VIDEO input. For a still image held across the complete song, connect the switch
directly to Save Video.

## Transcribing audio without supplied lyrics

**Transcribe Audio [Eclipse]** in `Eclipse > Audio` recognizes words from speech
or singing. It uses the same local Whisper large-v3 files and dependencies as
the renderer. No additional model or package is needed for transcription.

Connect the complete recording to `audio`. Optionally connect isolated vocals
to `vocals`; they must share the recording's time zero and duration, within
50 ms. Select a language explicitly when known, or use `Auto`. Outputs are
**transcript, timing_json, srt, report**, in that order. Text follows recognized
order, including recognized repetitions; it does not require reference lyrics.
The report includes language detection, recognition scores, discarded segments,
and unresolved timing. Singing recognition can omit or invent words, so review
the result before using it for captions or lip-sync prompts.

Connect `transcript` through **Show Text [Stop]** to the renderer's `lyrics`,
and `timing_json` to `corrected_timing`. Supply the same full audio to the
renderer, then use its existing trim controls. This reuses recognition timing
without a second alignment pass. JSON and transcription SRT use full-audio
seconds; the renderer's SRT uses the selected clip's seconds. Word timestamps
support all four caption modes. Segments without usable word timing retain
observed phrase bounds when available; missing/conflicting timings remain
unresolved. Combined sentences are never divided into guessed timings.

For a review stop, enable **Stop (Result Review)** on Show Text, queue once,
read the transcript, then queue unchanged again to continue. To edit wording,
copy the transcript into **String Multiline**, connect it to `lyrics`, and
disconnect `corrected_timing` so the renderer realigns the edited words. To
adjust timing only, copy the current `timing_json` into String Multiline and
connect it to `corrected_timing`, retaining matching transcript text. Editing
both requires updating word character offsets too. Keep the same source audio.
After saving text and timing separately, mute unused transcription preview
nodes if you want the corrected run to skip transcription entirely.

The recognition model unloads after completion, failure or cancellation.
ComfyUI can reuse the transcription node's outputs when only downstream caption
appearance or trim changes. This node can also supply text and subtitles to
other audio/video setups; it does not generate lip movement itself.

## Setup

Install Eclipse's `requirements.txt` in the ComfyUI environment. Alignment uses
`stable-ts==2.19.1` and `faster-whisper>=1.2.1,<2`; glyph validation uses fonttools.
These imports are lazy: corrected timing works without loading an alignment
model. No model is downloaded automatically at execution or schema discovery.

Place the following official CTranslate2 model files in
`ComfyUI/models/whisper/large-v3/`: `model.bin`, `config.json`, `tokenizer.json`,
`vocabulary.json`, and `preprocessor_config.json`.
Use [Systran/faster-whisper-large-v3, pinned revision
edaa852ec7e145841d8ffdb056a99866b5f0a478](https://huggingface.co/Systran/faster-whisper-large-v3/tree/edaa852ec7e145841d8ffdb056a99866b5f0a478).
The node verifies all five files against that revision before loading. The
model.bin SHA-256 is
`69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1`.
A different conversion must be qualified before changing this provenance policy.
Choose `cpu` (int8), `cuda` (float16), or `auto`. The model unloads after inference.

Fonts come from `ComfyUI/models/fonts/` (`.ttf` and `.otf`, including uppercase
extensions), shared with Text Image with FX. Empty font folders receive Eclipse's
bundled defaults. Captions initially select Quicksand Bold when installed,
falling back to the existing font discovery. Other nodes keep their font defaults.
Select a font covering the song's script. Missing fonts/glyphs raise an actionable
error before encoding. Pillow must include RAQM for shaping and RTL layout.
Arabic and CJK require suitable fonts; Roboto does not cover every script.

## Language and timing

New caption nodes select English (`en`) and automatic device selection.
`language=Auto` examines vocal candidates across the recording, with distributed
later windows when speech VAD misses most singing. The report includes detected
language, confidence, sample offsets, VAD coverage, and uncertainty. Silence has
no English fallback. Manual overrides use Whisper's supported language codes.
Auto chooses one predominant language and never translates the supplied lyrics.
Mixed-language singing needs manual qualification.

An ASR pass finds vocal-text matches across the **full song**. The matcher scores
continuous candidate windows for each supplied line, then selects the best
non-overlapping sequence across all lines. Earlier imperfect repetitions can
remain matched alongside later cleaner repetitions. A single long transcript
match cannot consume the first chorus and strand the rest of the lyrics.
Candidate scoring rewards supported original text and penalizes unrelated
recognized characters. Recognized windows that conflict with lyric order or
belong to another occurrence are reported separately, with candidate timestamps.
Short missed
sections bounded by neighboring matches receive a local recognition retry with
the same matching threshold. Suspect opening words and unresolved edge words
also receive bounded retries, with at most 12 local passes of at most 30 seconds.
Repairs require recognized text evidence and preserve lyric order; they do not
force missing lyrics into instrumental gaps. A tail repair cannot move an
already timed opening word earlier. Caption endings retain the recognized
lyric-window end rather than cutting off at an earlier word-suppression result;
word highlights still use only valid forced timings. If an accepted phrase has
no usable forced word timings after retries, its recognized start/end remain as
line timing. The report lists these in `alignment.phrase_only_lines`, and timing
JSON marks them with `timing_source: "recognized phrase"`.

If lines remain unmatched after word-window retries, a separate full-song pass
runs with **word timestamp extraction disabled**. It matches the supplied line
against complete recognized segments and uses the decoder's sentence timestamps.
This avoids losing a whole sentence because its ASR word timestamps were missing,
zero-length, or inaccurate. Existing supported lines keep their timing and order;
at most 0.5 seconds of decoder overlap is clipped against each neighboring line.
The sentence check keeps the 65% lyric-character coverage requirement and requires
75% recognized-character precision. It never divides a segment into guessed word
or line times. The report's `alignment.sentence_boundary_check` records whether
this check ran, which lines were checked and which were recovered; recovered
windows identify `boundary_source: "sentence timestamps"`.

Sentence timestamps can include silence and remain approximate. They are a
recovery path, not a guarantee that all supplied lyrics were found. The original
words are then aligned inside the recovered sentence window. If that refinement
fails, the full line still appears with unresolved words left unhighlighted.

Both `whole-line` and `active-word` modes display the full original line,
including unresolved words. In `active-word`, only words with valid timings
inside the line are highlighted; unknown word timing never removes text. A
phrase-only line remains visible with no highlighting. This fallback requires
an accepted recognition window; it cannot restore a phrase that recognition
never matched. Both modes share the same cached alignment.

Original lyric
characters are matched in sequence, including repeated sections; recognized
text is never substituted for the supplied wording. Stable-ts then aligns the
original words within those observed vocal windows. Each continuous match is
scored separately: distant coincidental characters cannot reject an otherwise
well-supported local window, and disconnected fragments cannot combine to meet
the coverage threshold. Unsupported or overlapping matches remain explicitly
unaligned instead of forcing lyrics into instrumental passages. ASR on music can still miss or hallucinate
vocals even after separation and needs review.
Sustained notes, backing vocals, repetitions, dense production, very long intros,
and incorrect lyrics can still produce inaccurate or incomplete results.
Zero-duration/missing words have null timing and explicit unaligned spans. Lines
with some valid word timing start at the first timed word and remain visible
through the observed lyric-window ending, preserving sung tails;
only timed words receive highlighting. Completely unaligned lines are excluded
from rendering/SRT until corrected. The report lists partial lines and their
unresolved words separately from completely unaligned lines. No missing word
timestamps are interpolated. Always inspect the report and review a singing
alignment before publishing.

The renderer's `caption_duration` is the sum of caption-visible intervals, not
the video length or the timestamp of the last caption. It counts supported line
intervals, excluding floating fade tails and overlaps. `duration` is the output
length. Skipped-line numbers refer to the cleaned lyrics, including blank lines.
The report's `alignment.unanchored_lines` lists rejected lyric windows with line
numbers, original text, reasons, and match scores. `alignment.word_alignment_failures`
separately lists accepted windows with missing or partial forced word timings.
`alignment.text_match_coverage` is the fraction of nonempty lines with accepted
windows, **not a timestamp accuracy score**. Unmatched lyrics remain unresolved;
ASR evidence alone does not establish that they are absent from the recording.

The report identifies the algorithm revision, analysis source, and alignment
cache-hit status. Auto language uncertainty lists low aggregate score separately
from disagreement between samples. Zero VAD coverage means detection failed on
the analyzed audio, not that the recording contains no vocals.

An independent process-local LRU cache retains at most eight alignments and
16 MiB of serialized timing/report data. Audio content/sample rate, cleaned
lyrics, language, device, model identity and algorithm revision determine its
key. Font, colors, glow, transparency, layout, background and trim changes reuse it. Returned data
are independent copies. Exceptions and interruptions are not cached; model
resources unload after inference. Restarting ComfyUI clears this cache.

## Appearance

New nodes default to **Floating words**, Quicksand Bold at **80 px**,
**720 × 1280 at 24 fps**, white text, yellow active-word highlights,
**#616161** outlines at **3 px**, a black background, **40 px** margins, and
zero timing adjustment. Fixed modes default to bottom-center positioning.

**Caption transparency (%)** is available in every mode. **0%** keeps the full
caption effect, **50%** halves its opacity, and **100%** hides it. Text, outlines,
highlights and glow fade together, multiplied by floating animation fades.
Background image/video/color and the supplied soundtrack remain unchanged.

**Enable glow** is initially off and available in all four modes. Enabling it
reveals the same controls as Text Image with FX:

| Control | Default | Range |
| --- | --- | --- |
| `glow_intensity` | 5 | 2–20 buildup steps |
| `glow_range` | 25 px | 1–500 px expansion |
| `glow_blur` | 15 | 0–500, scaled by each expansion step |
| `glow_inner_color` | #2ec0ff | Color picker |
| `glow_outer_color` | #006eff | Color picker |

The expanding, blurred two-color glow screen-blends behind the text. Its full
bounds count toward margins, fitting and floating placement; reduce glow or
margins if the effect cannot fit. Glow is prepared once per active caption shape,
reused through highlights and fades, and released when that caption ends.

## Floating captions

Select **Floating words** (`floating-words`) or **Floating lines**
(`floating-lines`) in `mode`. The existing `whole-line` and `active-word` modes
remain available. Floating words combines quickly sung words into short phrases;
slower words can appear individually. Floating lines moves the complete original
line. Both use the selected font, text color, outline and background.

Untimed text attaches to neighboring timed phrases without changing wording or
punctuation. When only sentence timing is available, the entire sentence appears
as one item. Entirely unresolved lines remain omitted. The word limit controls
automatic grouping; an indivisible timed span, attached untimed text or a
sentence-only item stays intact even when longer than that limit. Floating modes
do not invent word timings or alter the exported timing JSON.

Animation controls appear only in floating modes:

| Control | Default | Meaning |
| --- | --- | --- |
| `circle_radius` | 30% | Radius of the centered circle containing item centers, measured against the shorter canvas dimension. |
| `float_distance` | 1.5% | Maximum gentle drift, measured against the shorter canvas dimension. |
| `fade_in` | 0.5 s | Incoming opacity ramp. |
| `fade_out` | 0.5 s | Outgoing opacity ramp. |
| `min_display` | 0.7 s | Minimum readable time at full animation opacity, excluding fades; also the fast-word grouping target. |
| `max_words` | 4 | Automatic phrase word limit; visible only in Floating words. |
| `max_simultaneous` | 5 | Maximum visible items, including outgoing fades. |
| `seed` | 42 | Repeatable positions and drift directions. |

Each item starts at its audio timestamp. Fade-in, the minimum readable hold and
fade-out are separate parts of its lifetime: 0.5-second fades and `min_display`
of 0.9 seconds give a short phrase 1.9 seconds on screen when slots are available.
Long sung phrases stay until their observed ending before fading out. A following
word or line does not cut off an older caption while slots remain. The
`max_simultaneous` setting is a ceiling, not a target count; if a new onset needs
a slot, the older item's hold and fades shorten without delaying that onset.
Captions can linger into a pause for the configured hold/fade; they never extend
the exported clip. Caption transparency still applies throughout.
Candidate positions stay near
the preceding item and favor less overlap, including their movement paths.
Dense text or a small circle can still overlap. Text, outlines and glow stay within
`margin_x`/`margin_y`; long items shrink to fit those margins. The circle controls
text centers, so long lines may extend outside it. `position` applies to the
fixed modes, and `highlight_color` applies to `active-word`.

The complete song is scheduled before trimming. A trimmed clip retains the same
phrase groups, positions, fades and movement phase as that interval of the full
song, including an outgoing fade already underway at the trim boundary. Changing
mode, seed or other visual controls reuses alignment. SRT remains clip-relative
whole-line text, and timing JSON remains full-song source timing. Rendering keeps
only currently visible text rasters and encodes frames incrementally to a file.

## Isolated vocals

Connect the installed **AudioSeparation** node's **Vocals (slot 3)** output to
`vocals`. Both AudioSeparation and the renderer must receive the complete song
from the same audio selector. Use linear fades, 10-second chunks, and overlap
0.1. This node uses torchaudio's
[official Hybrid Demucs bundle](https://docs.pytorch.org/audio/stable/generated/torchaudio.pipelines.HDEMUCS_HIGH_MUSDB_PLUS.html).
No standalone Demucs package or wav2vec2 backend is needed.

The installed torchaudio runtime caches its official checkpoint under
`torch.hub.get_dir()/torchaudio/models/hdemucs_high_trained.pt`. The locally
qualified artifact exactly matched the official HTTPS download (334,697,255
bytes; SHA-256 `a004b2790d73ffeaa535db458a1a79b539dfdbafbccc31f275d07e632ebd7816`).

Connected vocals are used for detection and alignment only. Rendering always
uses the original `audio`. Without vocals, the report explicitly identifies
analysis of the original mix. Stems must start at song time zero and represent
the complete song; duration differences over 50 ms are rejected. AUDIO carries
no origin metadata, so callers must ensure the zero origin. Resampling preserves
time; no automatic shift or stretch is performed. Separation is not guaranteed
to improve matching on every recording.

## Corrected JSON

Connect the exported, reviewed JSON from a STRING node to `corrected_timing`.
This bypasses inference and model verification. The lazy `vocals` input also
skips Demucs unless another executing branch needs its output. Schema version 1 uses seconds
from the **full song**, even when rendering an excerpt:

```json
{
  "version": 1,
  "duration": 10.0,
  "language": "en",
  "lines": [
    {
      "text": "Hello world!",
      "start": 2.0,
      "end": 4.0,
      "words": [
        {"text": "Hello", "char_start": 0, "char_end": 5, "start": 2.0, "end": 2.8},
        {"text": "world!", "char_start": 6, "char_end": 12, "start": 3.0, "end": 4.0}
      ]
    }
  ]
}
```

Character offsets count Python Unicode characters; each word's text must match
its exact slice of the line. Timings must be finite, ordered, non-overlapping,
and within duration. Use `start: null, end: null` for unaligned lines or words.
Keep blank lines as empty text with null timing. Corrected alignment input must
match cleaned lyric lines and full audio duration. Line-only corrections may use
`words: []` and render as whole-line captions.

## Trimming and review

Supply full-song audio and connect both `trim_start` and `duration` FLOAT sockets
in seconds. They have no widgets or default values: missing connections are
rejected. `trim_start` trims audio and captions together; explicitly connected
`duration=0` renders the remainder, and both connected values at zero render the full song.
A start at or beyond the end is rejected. The end is clamped to available samples.
Offsets use the actual selected audio sample, so SRT stays clip-relative even
when the requested start falls between samples. Positive `timing_adjustment`
delays captions without changing the soundtrack or full-song timing JSON.

Alignment always uses the full song. The exported video, audio and SRT use the
selected trim, while timing JSON retains full-song timings, including lines
beyond the exported excerpt.

For review, connect the rendered VIDEO to Preview Video, enable `stop_review`,
and connect its output to Save Video. The first queue publishes the preview and
pauses; queue unchanged again to save. Changed content needs review again.
The renderer itself has no preview or review controls.

## Qualification and limits

In the earlier `continuous-windows-v2` qualification on the saved 234.76-second
recording, using the exact 32-line splitter output,
full-mix alignment produced 22 renderable lines (two partial), versus 20 (two
partial) with Demucs. Accepted-window coverage was 68.75% versus 62.5%; caption
intervals totaled 77.02 versus 71.16 seconds. VAD coverage increased from zero
to 25.14%. Both sources left the five checked instrumental gaps uncaptained.
Separation improved vocal detection but did not improve lyric matching here.

On the 291.6-second recording, the original 28-line generation prompt produced
12 renderable lines for either source (one partial on mix, none on vocals).
That check lacked the generated splitter arrangement, so it cannot be compared
directly to the earlier 36-line reconstructed-arrangement check.
VAD coverage rose from zero to 33.33%; neither source placed captions at 63–78 s.

These are coverage and gap checks, not human-labeled timing accuracy. Singing,
repeated sections, sustained notes, backing vocals, ASR omissions and changed
arrangements still require review or corrected JSON. Rendering has been checked
with English, Arabic and Chinese; multilingual singing accuracy is unqualified.


A later run supplied the exact 36-line arrangement for the 291.6-second recording.
Bounded retries recovered both previously missed lines near 200–210 seconds,
producing 36 renderable lines with two partial words. The early “I claim…” onset
moved from 121.78 to 124.98 seconds; the line ending in “bear” now continues to
194.64 seconds rather than 193.02. These are measured alignment changes, not a
human-labeled accuracy guarantee. A 240-second export contains 34 of those lines;
the final two remain in full-song timing JSON but fall outside the exported clip.


With `ordered-phrases-v4`, the reported 234.76-second / 32-line run improves from
12 timed lines to 21 with Demucs vocals (65.625% accepted-line coverage; two
partial lines). The first chorus now starts near 56.42 seconds, and its repeat
near 143.62 seconds. Original-mix analysis produces 22 timed lines (68.75%; one
partial). Five checked gaps remain free of captions. Eleven supplied lines
remain unresolved on vocals: weak recognition and conflicts between supplied
lyric order and observed candidates are reported explicitly. For example,
recognition places “A bittersweet reminder” after “Lonely is the heart”, while
the supplied lyrics place it before. This is model evidence to review, not proof
that a supplied line was not sung. The longer 291.6-second recording retains all
36 timed lines and the previously recovered 200–210-second passage.


The subsequent `sentence-boundaries-v5` check recovers the shorter recording's
“A whisper of a love that once burned bright” at 90.00–93.36 seconds. Demucs
analysis now provides 22/32 timed lines (68.75% accepted-line coverage; two partial
lines), compared with 21 before the independent sentence pass. Ten lines remain
unresolved, including recognition failures and supplied-order conflicts. The
291.6-second recording retains all 36 timed lines and needs no sentence fallback.
For a fresh automatic run, disconnect `corrected_timing`: pasted JSON deliberately
bypasses every recognition and recovery pass, including this sentence check.
