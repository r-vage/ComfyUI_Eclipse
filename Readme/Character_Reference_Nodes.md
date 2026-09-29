# Character reference and sheet nodes

Eclipse provides prompt preparation, reference routing and panel cropping nodes.
They do not generate images themselves: connect their outputs to compatible
image encoders and samplers. Each image input expects a single image rather than
an image batch.

## Character Reference Prepare and Select

Prepare supports face, body, clothing and rear roles. Connect its `reference`
control output to Select and the preparation sampler's image to Select's
`generated` input.

| Mode | Select output |
| --- | --- |
| `reference` | Original source; preparation generation is skipped. |
| `extract` | Generated isolated reference based on the source. |
| `generate` | Generated reference from text; the source branch is not requested. |
| `disabled` | No reference; source and preparation generation are skipped. |

Prepare loads role/mode instructions and role-specific negatives from
`prompts/character_references.json`. Without an override, description and
instructions append to the positive. Optional `positive_override` and
`negative_override` STRING inputs independently replace those defaults. A
positive override also replaces description/instructions; a connected empty
negative clears the negative. Use String Multiline nodes for editable text.

Prepare does not crop. Use upstream image nodes and a preview. Generative
extraction can alter details; review its output before reusing it.

## Character Reference Pack

Reference Pack combines a required face with optional body, clothing, rear and
assembled anchor references. It assigns consecutive image numbers in this order:
anchor if present, face, body, clothing, rear. Its report identifies the actual
slots; pair its prompt with its own image outputs on the same encoder.

- `anchor_prompt` is the complete positive for generating a full-body review
  character. It is text, not an image.
- `character_lock` contains identity, physique, wardrobe and reference roles,
  without imposing the review pose or scene. It can feed
  [Character Shot Planner](Character_Shot_Planner.md).
- `anchor` accepts an already approved assembled character image.
- `negative` is the general assembly/dataset negative from the prompt file,
  separate from Prepare's per-role negatives.

Face supplies identity; body supplies visible proportions; clothing supplies the
outfit. Rear supplies compatible rear appearance, not a command to face away.
These are model instructions, not guaranteed separation of visual features.
The node does not measure BMI or infer concealed anatomy reliably.

## Character Sheet Pack

Sheet Pack creates stage-specific prompts and up to three image outputs. Connect
positive, negative and the numbered images to the same compatible encoder.

| Stage | First image | Second image | Third image |
| --- | --- | --- | --- |
| `mannequin` Preset | Blank canvas | — | — |
| `mannequin` Reference | Blank canvas or supplied layout | Front body if present | Rear body if both present |
| `wardrobe` | Outfit source | — | — |
| `sheet` | Required mannequin guide | Required face | Optional wardrobe board |
| `dataset` | Required front character | Required face | Optional rear character |

Optional body images compact consecutively: with only rear supplied, it is image
2. Unrelated roles are ignored by each stage. The toggles skip inactive image
branches through lazy evaluation; another output may still request that branch.

`rear_body` is an original physique photograph used only by the mannequin stage.
`rear` is an approved character rear view used only by the dataset stage. A rear
reference does not automatically request rear-facing shots.

### Mannequin and assembly roles

Directly below Stage, mannequin controls are Generation mode, Gender, Build,
Muscularity, Breast size, Chest breadth, Hip width and Buttock size, followed by
dimensions and details. Mode and Gender appear only for the mannequin stage.
Body controls appear only in **Preset** mode; body-reference toggles appear only
in **Reference** mode. All body attributes are independent of Gender.

**Preset** skips all connected image inputs, including layout, and supplies a
blank size-setting canvas. Build defaults to average; Gender, muscularity and
regional attributes default to unspecified. Unspecified adds no instruction.
**Reference** ignores all hidden body presets and uses optional physique images.
Leave `layout` disconnected for a blank canvas, or connect a template for framing. Front physique guides chest/breast proportions,
overall figure and musculature; rear guides hips, glutes and back. The default
requests three clay views without source clothing, hair or identity.

The sheet stage requires the approved guide. Its default assigns body geometry
and portrait framing to that guide, identity and appearance to the face, and
wearable garments to the wardrobe board. Clothes fit the established physique.
Raw front/rear body images are not passed through this stage.

To omit wardrobe, disable `use_wardrobe` and supply a complete written outfit in
`details`. For dataset generation, use individual approved character images;
`use_rear` independently enables compatible rear-view conditioning.

Mannequin defaults ask for relative source geometry, including chest-to-ribcage
scale, shoulder/waist relationships and rear hip/glute proportions. Photographs
and stylized illustrations both supply shape: changing the material to grey
must not normalize the build toward an average physique. This applies equally
to smaller/larger regions and less/more muscular bodies. Clear body-following
contours can guide shape beneath fitted fabric; loose clothing and armor do not
reveal hidden anatomy. Cropped sources control only visible regions.
Explicit written physique details override only the attributes they specify.
In Reference mode without body images or written details, no body type is
prescribed. The dedicated Gender widget offers unspecified, female, male and
androgynous. It supplements both modes and positive overrides without setting
body measurements or muscularity. Both modes keep the fixed full-body front,
full-body rear and upper-body portrait layout; pose variation belongs in the
shot planner.
The mannequin prompt uses continuous prose with reference assignments; the full
reference map remains in `report`. A lone reference is called the supplied image,
while multiple references use `<image1>`, `<image2>`, and so on.

### Prompts, numbering and dimensions

Defaults live in `prompts/character_sheets.json`. Optional positive/negative
STRING overrides independently replace file text. The Pack still appends its
reference map and `details` to the positive. For mannequins, the fixed layout,
Gender and active Preset selections also supplement it; explicit details refine
only named attributes. A connected empty negative clears
it. File edits do not replace connected base text. Mannequin mode templates,
fixed layout, detail-refinement instruction and selection descriptions are
editable in that same file (`mannequin`, `mannequin_preset`, `mannequin_layout`,
`mannequin_details`, `mannequin_attributes`). Requeue reloads edits, including
selection descriptions when both base prompts are overridden.

Keep uppercase role names such as `LAYOUT GUIDE`, `FACE`, `WARDROBE`, `FRONT BODY`
and `REAR BODY` in custom prompts. Pack resolves them to the actual `<imageN>`
labels throughout the task, details and negative. Inspect `positive` and `report`
through Show Any to verify numbering and sizes.

Canvas dimensions must be multiples of 32 from 256 to 2048. Use Qwen encoder
resolution 0 and its corresponding latent output. In the mannequin and sheet
stages, only the first image is fitted to the canvas. Supporting body references
and face/wardrobe images keep their aspect ratios without added canvas padding,
are never enlarged, and are capped at 1024 pixels on the longest side.
Other stages pad references without stretching. Connected dimensions take
precedence over saved widget values.

At sampler CFG 1, the negative prompt has no effect. Prompt instructions do not
guarantee exact identity, body geometry, garment detail or panel layout.

## Character Sheet Split

Split crops one approved front/back/portrait sheet into three independent IMAGE
outputs. It does not detect or repair panel contents.

Mode is the first widget. Auto hides `front_end` and `rear_end`; switching to manual
shows them again with their saved values intact. Gutter stays visible in both
modes. If mode is connected as an input, the boundaries stay available because
the frontend cannot know the incoming mode before execution. Hidden boundary
links and existing workflow values are preserved.

- `mode=auto`: divide the actual input image width into three equal parts on each
  execution. No separate width connection is needed. Saved manual boundaries are
  ignored; widths not divisible by three differ by at most one pixel.
- `mode=manual`: use the adjustable fractional boundaries. This remains the
  default so existing saved crops retain their behavior.
- `front_end`: manual front/rear boundary as a fraction of source width; default 1/3.
- `rear_end`: manual rear/portrait boundary; default 2/3 and greater than `front_end`.
- `gutter`: fraction of source width trimmed from both sides of each crop;
  default 0.004, applied in both modes. Set 0 to retain every pixel of each third.

Preview all outputs; use manual mode to adjust uneven generated panels. A
shifted divider, cropped garment or fourth figure requires correction before
using the result as a reference. Outputs are single images rather than lists,
so splitting alone does not multiply sampler runs.

## Editable files

Eclipse distributes initial sources as
`.defaults/prompts/character_references.json.example` and
`.defaults/prompts/character_sheets.json.example`. Edit the runtime copies under
`prompts/` for personal defaults. File changes are read on the next execution;
no restart is needed for prompt-only edits. Invalid files report errors instead
of silently replacing user content. Both connected overrides allow the relevant
Prepare/Sheet Pack task to work without reading its default file.
