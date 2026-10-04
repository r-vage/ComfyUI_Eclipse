# Role-separated reference preparation and lazy selection for character datasets.

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.character_references import (
    MODES,
    ROLES,
    pack_references,
    prepare_reference,
    rules_fingerprint,
    select_reference,
)
from ..core.node_debug import debug_node

_MISSING = object()
_LOG_PREFIX = "CharacterReferences"


class RvImage_CharacterReferencePrepare(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Character Reference Prepare [Eclipse]",
            display_name="Character Reference Prepare",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Choose how one face/body/clothing/rear reference is prepared. This node supplies prompts and source data; connected encoder/sampler nodes generate the image. File defaults come from prompts/character_references.json. Connected STRING overrides replace those defaults. Connect reference to Character Reference Select to apply the chosen mode.",
            inputs=[
                io.Combo.Input("role", options=list(ROLES), default="face", tooltip="Select the file prompt category: face for identity/hair, body for proportions, clothing for wardrobe, rear for compatible back details. Connect Select's image to the matching Pack input. Changing role does not rewrite a connected positive/negative override."),
                io.Combo.Input("mode", options=list(MODES), default="reference", tooltip="reference passes the source through; extract uses the source to generate an isolated reference; generate creates one from text without using the source; disabled omits it. Select skips its sampler branch in reference/disabled modes. When using overrides, update their text yourself after changing mode. Clothing defaults can also complete missing outfit pieces."),
                io.String.Input("description", multiline=True, default="", tooltip="Details for this preparation task, such as facial features, body proportions or outfit items. Appends to the file positive; does not replace it or affect the negative. Ignored and hidden when positive_override is connected. Reference/disabled modes do not generate from this text."),
                io.String.Input("instructions", multiline=True, default="", tooltip="Additional preparation instructions appended after description, such as what to preserve or exclude. Ignored and hidden when positive_override is connected. Does not change the negative or the Pack's assembly rules."),
                io.Image.Input("image", optional=True, lazy=True, tooltip="One source image, required for reference/extract. Generate/disabled skip it. Uses the full input image; crop/resize and preview upstream if needed. Image batches are not supported."),
                io.String.Input("positive_override", optional=True, force_input=True, tooltip="Connect String Multiline to replace the entire preparation positive, including file text, description and instructions. Passed exactly as supplied; connected empty text clears it. Edit this text in the workflow: file edits will not update it. Disconnect to restore role/mode defaults. Keep it appropriate to the selected task."),
                io.String.Input("negative_override", optional=True, force_input=True, tooltip="Connect String Multiline to replace this role's preparation negative independently of the positive. Connected empty text means no negative prompt. Disconnect to use the file default. This does not replace the Pack's assembly/dataset negative."),
            ],
            outputs=[
                io.Custom("ECLIPSE_CHARACTER_REFERENCE").Output("reference", tooltip="Role, mode and source data for Character Reference Select. This is a control object, not an image or prompt."),
                io.String.Output("positive", tooltip="Preparation prompt for the connected image encoder in extract/generate modes. Comes from the file plus details, or exactly from positive_override. Reference/disabled modes bypass preparation generation through Select."),
                io.String.Output("negative", tooltip="Preparation negative for the same encoder/sampler branch. Uses this role's file negative unless negative_override is connected."),
                io.Image.Output("source", tooltip="Full source image for the extraction encoder; present in reference/extract modes. No image in generate/disabled modes. Use Select's image output for the final Pack input."),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, positive_override=None, negative_override=None, **kwargs):
        uses_file = positive_override is None or negative_override is None
        return rules_fingerprint() if uses_file else "overrides"

    @classmethod
    def check_lazy_status(cls, mode="reference", image=_MISSING, **kwargs):
        return ["image"] if mode in ("reference", "extract") and image is None else []

    @classmethod
    @debug_node(_LOG_PREFIX, values=("role", "mode"))
    def execute(cls, role, mode, description, instructions, image=None,
                positive_override=None, negative_override=None):
        return io.NodeOutput(*prepare_reference(role, mode, description, instructions,
                                              image, positive_override, negative_override))


class RvImage_CharacterReferenceSelect(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Character Reference Select [Eclipse]",
            display_name="Character Reference Select",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Choose the source or generated image using Prepare's mode: reference returns the source; extract/generate request the generated image; disabled returns no image. This lets optional reference branches be skipped without muting nodes. Send the result to the matching Character Reference Pack input.",
            inputs=[
                io.Custom("ECLIPSE_CHARACTER_REFERENCE").Input("reference", tooltip="Connect reference from the matching Character Reference Prepare. Its mode decides whether this node uses the original, requests generated, or returns no image."),
                io.Image.Input("generated", optional=True, lazy=True, tooltip="Connect the decoded image from this reference's preparation sampler. Evaluated only in extract/generate modes. Reference/disabled modes skip this branch through Select; other independent output nodes can still execute it."),
            ],
            outputs=[io.Image.Output("image", tooltip="The chosen single image, or no image when disabled. Connect to the corresponding face/body/clothing/rear input on the Pack; preview it first to check extraction fidelity.")],
        )

    @classmethod
    def check_lazy_status(cls, reference, generated=_MISSING, **kwargs):
        return ["generated"] if reference["mode"] in ("extract", "generate") and generated is None else []

    @classmethod
    @debug_node(_LOG_PREFIX, values=("role", "mode"))
    def execute(cls, reference, generated=None):
        return io.NodeOutput(select_reference(reference, generated))


class RvImage_CharacterReferencePack(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Character Reference Pack [Eclipse]",
            display_name="Character Reference Pack",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Combine separate reference roles into prompts and numbered Qwen image inputs. First Pack: use anchor_prompt to generate the full-body review character. Dataset Pack: connect that approved image to anchor and send character_lock to the shot planner. Prompts/roles come from prompts/character_references.json. This node prepares references; it does not generate images.",
            inputs=[
                io.Int.Input("width", default=832, min=256, max=2048, step=32, tooltip="Reference canvas width, 256–2048 in multiples of 32. Images are padded without stretching or cropping. For native Qwen 2.1, use encoder resolution=0 and its latent output to preserve this canvas size."),
                io.Int.Input("height", default=1216, min=256, max=2048, step=32, tooltip="Reference canvas height, 256–2048 in multiples of 32. Together with width, sets the padded reference dimensions. Share these settings between assembly and dataset Packs for consistent sizing."),
                io.String.Input("identity", multiline=True, default="The same adult character throughout the dataset.", tooltip="Identity details preserved in review and dataset prompts, such as age, hair and distinctive features. Supplements the face image; does not generate a face reference by itself."),
                io.String.Input("body_description", multiline=True, default="", tooltip="Describe visible build and relative proportions to preserve, such as chest-to-waist relationship or limb lengths. Supplements a body image, or guides physique without one. Not a measured BMI or numeric anatomy control."),
                io.String.Input("outfit", multiline=True, default="Plain grey cardigan, blue jeans and white sneakers.", tooltip="Fixed wardrobe and explicit choices for missing items. Keep consistent with the clothing reference and its completion inventory; clear the example outfit when using a different reference. Used for assembly and later shots, not for the separate clothing extraction sampler."),
                io.String.Input("scene", multiline=True, default="Plain white studio background and light floor, soft natural light.", tooltip="Background and lighting for anchor_prompt's review image only. Not included in character_lock: set the planner's scene separately for dataset shots, or connect one shared text node to both."),
                io.String.Input("extra_rules", multiline=True, default="", tooltip="Additional instructions appended to both character_lock and anchor_prompt. File role/assembly rules still apply; this is not a full override. Avoid fixed poses here if the planner should vary them."),
                io.Image.Input("face", optional=True, tooltip="Required to execute: one face reference controlling identity, hair and skin tone. Connect the face Select output or a direct image. This role does not control body proportions or wardrobe."),
                io.Image.Input("body", optional=True, tooltip="Optional single image controlling visible physique/proportions. Its face and clothing are excluded by the role prompt, but model adherence needs review. Without it, physique comes from the body description/approved character."),
                io.Image.Input("clothing", optional=True, tooltip="Optional outfit reference or completed garment flat lay. Controls garments, materials, colors and accessories, not the original wearer. Check missing pieces before assembly; keep written outfit choices consistent. Underwear stays beneath opaque outer layers when worn."),
                io.Image.Input("rear", optional=True, tooltip="Optional compatible back-view reference for rear hair and garment details when visible. Does not force rear-facing shots or override the selected face, physique or wardrobe."),
                io.Image.Input("anchor", optional=True, tooltip="Approved assembled character image, not a text prompt. Leave empty in the first Pack that creates the review character. Connect the reviewed image here in the dataset Pack to preserve the combined look. Becomes image_1; pose/framing may still vary."),
            ],
            outputs=[
                io.String.Output("character_lock", tooltip="Shared identity, physique, wardrobe and numbered reference-role instructions. Connect to Character Shot Planner's character_lock for varied dataset shots. Excludes the fixed full-body review pose and Pack scene."),
                io.String.Output("anchor_prompt", tooltip="Complete positive prompt to generate the first full-body REVIEW CHARACTER. Connect to the assembly encoder. Normally unused on the second/dataset Pack, whose character_lock goes to the planner instead. This is text, not the anchor image."),
                io.String.Output("negative", tooltip="General assembly/dataset negative from prompts/character_references.json. Connect to the corresponding encoder's negative input. Separate from each Prepare node's role-specific negative override."),
                *[io.Image.Output(f"image_{i}", tooltip=f"Connect to Qwen encoder image{i}. Active roles are packed consecutively: anchor if supplied, then face, body, clothing, rear. This socket has no fixed role; check report. Unused slots return no image. Keep paired with this Pack's prompt.") for i in range(1, 6)],
                io.String.Output("report", tooltip="Lists actual image-number-to-role mapping, canvas size and omitted optional references. Connect to Show Any when checking which image the prompt calls image1, image2, etc."),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return rules_fingerprint()

    @classmethod
    @debug_node(_LOG_PREFIX)
    def execute(cls, width, height, identity, body_description, outfit, scene, extra_rules,
                face=None, body=None, clothing=None, rear=None, anchor=None):
        return io.NodeOutput(*pack_references(width, height, identity, body_description, outfit, scene,
                                             extra_rules, face, body, clothing, rear, anchor))
