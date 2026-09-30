# Multi-stage sheet assembly and deterministic panel extraction.

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.character_sheets import (
    GENERATION_MODES,
    IMAGE_INPUTS,
    STAGES,
    active_roles,
    mannequin_options,
    pack_sheet,
    rules_fingerprint,
    split_sheet,
)


class RvImage_CharacterSheetPack(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        image_tips = {
            "layout": "Mannequin Reference mode: leave disconnected to generate from the written three-panel layout; the node supplies a blank size-setting canvas. Optionally connect a pose template for framing only. Sheet stage: the approved generated mannequin guide is required and supplies poses and proportions.",
            "face": "Required for sheet/dataset: one identity portrait. Dataset normally uses the extracted portrait panel. Remains a separate image even when a rear reference is enabled.",
            "wardrobe": "Wardrobe stage: source outfit photograph. Sheet stage: approved wardrobe board. Different board views describe one outfit. Ignored by mannequin/dataset stages.",
            "front_body": "Optional original frontal physique reference, used ONLY in mannequin Reference mode; Preset skips it. Never routed into dressed-character or dataset generation. No extraction or undressing required.",
            "rear_body": "MANNEQUIN Reference mode only (Preset skips it): original rear body photograph for visible physique/proportions. Its clothing and identity are ignored. Different from rear, which accepts the finished dressed character's rear panel for the dataset stage. Ignored by wardrobe/sheet/dataset stages.",
            "front": "Dataset: approved front panel, one image. Supplies established outfit and proportions. A contact sheet or image batch is not appropriate here.",
            "rear": "DATASET stage only: approved rear panel cropped from the finished dressed character sheet, preserving the chosen outfit and rear appearance. Different from rear_body, the original physique photograph. Ignored by mannequin/wardrobe/sheet stages. Does not request every shot to face away.",
        }
        return io.Schema(
            node_id="Character Sheet Pack [Eclipse]",
            display_name="Character Sheet Pack",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Separate mannequin, wardrobe, character-sheet and dataset tasks. Packs at most three references and creates an explicit image-role map. Raw body references are used only in the mannequin stage. Connect positive/negative and all three images to the same Qwen encoder. Defaults: prompts/character_sheets.json.",
            inputs=[
                io.Combo.Input("stage", options=list(STAGES), default="sheet", tooltip="mannequin: choose Preset controls or Reference images; wardrobe: outfit source; sheet: approved mannequin + face + optional wardrobe; dataset: individual front + portrait + optional rear panels. Changing stage also requires updating connected prompt overrides."),
                io.Combo.Input("generation_mode", options=list(GENERATION_MODES), default="Preset", display_name="Generation mode", tooltip="Mannequin only. Preset skips every image input and uses a blank canvas plus selected attributes. Reference uses optional layout/front/rear images and ignores hidden body presets."),
                *[io.Combo.Input(name, options=list(options), default="average" if name == "build" else "unspecified",
                                 display_name=name.replace("_", " ").capitalize(),
                                 tooltip="Mannequin: Gender applies in both modes; body attributes apply only in Preset. All attributes are independent of gender. Unspecified adds no instruction. Explicit details refine only named attributes. Selection names and descriptions come from mannequin_attributes in prompts/character_sheets.json. After adding entries, restart ComfyUI and refresh the browser.")
                  for name, options in mannequin_options().items()],
                io.Int.Input("width", default=1536, min=256, max=2048, step=32, tooltip="Output/reference canvas width. Use a landscape canvas for sheets and a portrait canvas for individual dataset shots. Qwen encoder resolution must be 0."),
                io.Int.Input("height", default=864, min=256, max=2048, step=32, tooltip="Output canvas height. Mannequin/sheet stages: only the first image sets the canvas; supporting body, face and wardrobe references keep their own aspect ratios, are never enlarged, and are capped at 1024 pixels on the longest side. Other stages pad references without stretching. Without a mannequin template, width/height set the blank generation canvas. When using a template, match its aspect ratio for accurate panel splits."),
                io.String.Input("details", multiline=True, default="", tooltip="Additional task specification. Mannequin: physique; wardrobe: missing outfit choices; sheet: identity and outfit; dataset: identity/wardrobe continuity only, without fixed pose instructions. Required when sheet has no wardrobe image."),
                io.Boolean.Input("use_front_body", default=True, tooltip="Mannequin Reference mode only: use the connected frontal body image. Off skips this input branch. Has no effect on other stages."),
                io.Boolean.Input("use_rear_body", default=True, tooltip="Mannequin Reference mode only: use the connected rear body image. Off skips this input branch. No effect on the final sheet's rear panel, which is always generated."),
                io.Boolean.Input("use_wardrobe", default=True, tooltip="Sheet stage only: use the wardrobe board. Off uses the written outfit specification and skips this input branch unless another output separately requests it."),
                io.Boolean.Input("use_rear", default=True, tooltip="Dataset stage only: include the approved rear crop alongside front and face. Off can reduce rear-view influence while retaining front/portrait references. It does not change planner camera choices."),
                *[io.Image.Input(name, optional=True, lazy=True, tooltip=image_tips[name]) for name in IMAGE_INPUTS],
                io.String.Input("positive_override", optional=True, force_input=True, tooltip="Replace the stage's file task text with an editable String Multiline input. The Pack adds the reference-role map, fixed mannequin layout, Gender, active Preset selections and details, then resolves uppercase role names such as FRONT BODY, FACE and WARDROBE to the actual <image1>, <image2>, etc. throughout the final task text. This keeps optional-input numbering correct. Connect positive to Show Any to inspect the resolved prompt. Disconnect this input to load current file text."),
                io.String.Input("negative_override", optional=True, force_input=True, tooltip="Replace this stage's file negative independently. An empty connected string intentionally clears it. File edits do not change connected workflow text."),
            ],
            outputs=[
                io.String.Output("positive", tooltip="Stage prompt plus actual reference mapping. In dataset stage, connect to the shot planner's character_lock; other stages connect directly to their encoder."),
                io.String.Output("negative", tooltip="Negative for this stage's encoder. Sheet negatives allow exactly three panels; dataset negatives prohibit panels. At sampler CFG=1 the negative has no effect; higher CFG enables it."),
                *[io.Image.Output(f"image_{i}", tooltip=f"Connect to the same encoder's image_{i}. Roles depend on stage and enabled inputs; check report. Unused slots return no image.") for i in range(1, 4)],
                io.String.Output("report", tooltip="Lists actual image labels, role mapping, omitted references, target canvas and individual reference dimensions. No images are generated by this node."),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, positive_override=None, negative_override=None, **kwargs):
        return rules_fingerprint() if kwargs.get("stage") == "mannequin" or positive_override is None or negative_override is None else "overrides"

    @classmethod
    def check_lazy_status(cls, stage, use_front_body=True, use_rear_body=True,
                          use_wardrobe=True, use_rear=True, generation_mode="Preset", **kwargs):
        # Missing optional sockets are absent; connected unevaluated inputs are None.
        return [name for name, _ in active_roles(stage, use_front_body, use_rear_body, use_wardrobe, use_rear, generation_mode)
                if name in kwargs and kwargs[name] is None]

    @classmethod
    def execute(cls, stage, width, height, details="", use_front_body=True, use_rear_body=True,
                use_wardrobe=True, use_rear=True, positive_override=None, negative_override=None,
                generation_mode="Preset", gender="unspecified", build="average", muscularity="unspecified",
                breast_size="unspecified", chest_breadth="unspecified", hip_width="unspecified",
                buttock_size="unspecified", **images):
        return io.NodeOutput(*pack_sheet(stage, width, height, details, positive_override, negative_override,
                                        use_front_body, use_rear_body, use_wardrobe, use_rear,
                                        generation_mode=generation_mode, gender=gender, build=build,
                                        muscularity=muscularity, breast_size=breast_size, chest_breadth=chest_breadth,
                                        hip_width=hip_width, buttock_size=buttock_size, **images))


class RvImage_CharacterSheetSplit(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Character Sheet Split [Eclipse]",
            display_name="Character Sheet Split",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Extract front, rear and portrait panels from one approved sheet. Auto splits the actual input width into thirds; manual uses adjustable boundaries. Outputs are three separate single images, never a batch/list. Auto does not detect shifted panel dividers.",
            inputs=[
                io.Image.Input("sheet", tooltip="One approved sheet: front on the left, rear in the center, portrait on the right. This node crops pixels; it does not detect or repair panel contents."),
                io.Float.Input("front_end", default=1 / 3, min=0.01, max=0.98, step=0.001, tooltip="Manual mode only: boundary between front and rear, as a fraction of full sheet width. Ignored in auto mode. 0.333333 is one third. Check previews when adjusting."),
                io.Float.Input("rear_end", default=2 / 3, min=0.02, max=0.99, step=0.001, tooltip="Manual mode only: boundary between rear and portrait as a fraction of sheet width. Ignored in auto mode. Must be greater than front_end. Default is two thirds."),
                io.Float.Input("gutter", default=0.004, min=0, max=0.09, step=0.001, tooltip="Both modes: trim this fraction of full sheet width from each panel's left/right edges to remove divider lines. Set 0 for complete thirds with no pixels discarded; reduce if clothing is near a boundary."),
                # Append to preserve existing serialized widget positions.
                io.Combo.Input("mode", options=["auto", "manual"], default="manual", optional=True, tooltip="auto: split the actual input image width into three equal parts, ignoring front_end/rear_end. No separate width input is needed. Widths not divisible by three produce panels differing by at most one pixel before gutter trimming. manual: use the saved fractional boundaries for uneven layouts. Defaults to manual to preserve existing crops."),
            ],
            outputs=[
                io.Image.Output("front", tooltip="Single front-view crop. Connect to dataset Pack front."),
                io.Image.Output("rear", tooltip="Single rear-view crop. Connect to dataset Pack rear."),
                io.Image.Output("portrait", tooltip="Single identity crop. Connect to dataset Pack face, or use the original face reference if this crop lost identity."),
                io.String.Output("report", tooltip="Pixel boundaries and gutter used. Check previews for clipping; generation may not follow exact thirds."),
            ],
        )

    @classmethod
    def execute(cls, sheet, front_end=1 / 3, rear_end=2 / 3, gutter=0.004, mode="manual"):
        return io.NodeOutput(*split_sheet(sheet, front_end, rear_end, gutter, mode))
