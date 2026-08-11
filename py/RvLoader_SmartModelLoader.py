from __future__ import annotations

# Smart Model Loader — All-in-one model loader with multi-select feature toggling
#
# Merges Smart Loader, Smart Loader Basic, and Smart Loader Plus into a single node
# with a chip-select multi-select widget to toggle feature groups on/off.
#
# Features (toggled via multi-select):
# - CLIP: External or baked CLIP support with up to 4 models
# - VAE: External or baked VAE support
# - Latent: Resolution presets/custom + batch size → latent tensor creation
# - Sampler: Sampler/scheduler/steps/cfg/flux_guidance output in pipe
# - LoRA: Up to 3 model-only LoRA slots with switches
# - Model Sampling: Advanced sampling method configuration
# - Block Swap: Offload transformer blocks to CPU for VRAM savings
# - Templates: Save/load configuration presets
import comfy  # type: ignore
import comfy.samplers  # type: ignore
import comfy.sd  # type: ignore
import comfy.utils  # type: ignore
import folder_paths  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY, RESOLUTION_PRESETS, SLIDER_DISPLAY
from ..core.loader_templates import get_template_list, get_template_mtime
from ..core.logger import log
from ..core.model_loader.loading import LoadRequest
from ..core.model_loader.smart import execute_smart_request
from ..core.model_loader.validation import (
    DOWNLOAD_TARGET_ROLES,
    MODEL_PRECISION_OPTIONS,
    LoaderValidationError,
)
from ..core.model_loader_common import GGUF_AVAILABLE
from ..core.nunchaku_wrapper import get_nunchaku_info

_LOG_PREFIX = "Smart Model Loader"

# All features the user can toggle via the multi-select chip widget
# Seed mode (random/increment/decrement) is handled by dedicated seed buttons
# in the JS frontend, not chips. Only resolved seed values arrive here.
FEATURE_OPTIONS = [
    "templates",
    "clip",
    "vae",
    "audio_vae",
    "latent",
    "sampler",
    "lora",
    "model_sampling",
    "block_swap",
    "memory_cleanup",
    "integrity",
    "seed",
]

DEFAULT_FEATURES = ["clip", "vae", "memory_cleanup"]

_support_messages_printed = False


def _print_support_messages():
    global _support_messages_printed
    if not _support_messages_printed:
        _support_messages_printed = True

        nunchaku_info = get_nunchaku_info()
        if nunchaku_info["available"]:
            version = (
                nunchaku_info["version"] if nunchaku_info["version"] else "installed"
            )
            log.debug(_LOG_PREFIX, f"✓ Nunchaku support: {version}")

        if GGUF_AVAILABLE:
            log.debug(_LOG_PREFIX, "✓ GGUF support available")


_print_support_messages()


class RvLoader_SmartModelLoader(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        weight_dtype_options = ["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"]

        loras = ["None"] + folder_paths.get_filename_list("loras")

        clip_files = list(folder_paths.get_filename_list("clip"))
        if "text_encoders" in folder_paths.folder_names_and_paths:
            clip_files.extend(folder_paths.get_filename_list("text_encoders"))
        clips = ["None"] + sorted(set(clip_files))

        return io.Schema(
            node_id="Smart Model Loader [Eclipse]",
            display_name="Smart Model Loader",
            category=CATEGORY.MAIN.value + CATEGORY.LOADER.value,
            description="All-in-one model loader with multi-select feature toggling. "
            "Supports Standard Checkpoints, UNet, Nunchaku (Flux/Qwen/ZImage), and GGUF models.",
            inputs=[
                # --- Multi-select feature toggle (JS replaces with combo-chip) ---
                io.String.Input(
                    "features",
                    default=",".join(DEFAULT_FEATURES),
                    socketless=True,
                    tooltip="Comma-separated list of enabled loader features. Selected via the Mode Bar chip widget on the frontend.",
                ),
                # --- Template management (visible when 'templates' selected) ---
                io.Combo.Input(
                    "template_action",
                    options=["None", "Load", "Save"],
                    default="None",
                    tooltip="Manage saved presets/templates for this node:\n• None: Do nothing\n• Load: Load configurations from template_name\n• Save: Save current configurations into a new template.",
                ),
                io.Combo.Input(
                    "template_name",
                    options=get_template_list(),
                    default="None",
                    tooltip="Select a saved configuration template to load or delete.",
                ),
                io.String.Input(
                    "new_template_name",
                    default="",
                    tooltip="Enter a name for the new configuration template when saving current settings.",
                ),
                # --- Model type & path selection ---
                io.Combo.Input(
                    "model_type",
                    options=[
                        "Standard Checkpoint",
                        "UNet Model",
                        "Nunchaku Flux",
                        "Nunchaku Qwen",
                        "Nunchaku ZImage",
                        "GGUF Model",
                    ],
                    default="Standard Checkpoint",
                    tooltip="Type of model architecture to load:\n• Standard Checkpoint: Full model (diffusion, CLIP, VAE) in one file\n• UNet Model: Diffusion-only model\n• Nunchaku: FP4 quantized GPU inference models\n• GGUF Model: llama.cpp/GGUF quantized models",
                ),
                io.Combo.Input(
                    "ckpt_name",
                    options=["None"] + folder_paths.get_filename_list("checkpoints"),
                    default="None",
                    tooltip="Select a standard Stable Diffusion or Flux checkpoint file containing diffusion, CLIP, and VAE weights.",
                ),
                io.Combo.Input(
                    "unet_name",
                    options=["None"]
                    + folder_paths.get_filename_list("diffusion_models"),
                    default="None",
                    tooltip="Select a standalone UNet/Diffusion model checkpoint (e.g. Flux, SD3, AuraFlow) from the diffusion_models directory.",
                ),
                io.Combo.Input(
                    "nunchaku_name",
                    options=["None"]
                    + folder_paths.get_filename_list("diffusion_models"),
                    default="None",
                    tooltip="Select a Nunchaku FP4-quantized model for Flux to load.",
                ),
                io.Combo.Input(
                    "qwen_name",
                    options=["None"]
                    + folder_paths.get_filename_list("diffusion_models"),
                    default="None",
                    tooltip="Select a Nunchaku FP4-quantized model for Qwen2-VL to load.",
                ),
                io.Combo.Input(
                    "zimage_name",
                    options=["None"]
                    + folder_paths.get_filename_list("diffusion_models"),
                    default="None",
                    tooltip="Select a Nunchaku FP4-quantized model for ZImage to load.",
                ),
                io.Combo.Input(
                    "gguf_name",
                    options=["None"]
                    + (
                        folder_paths.get_filename_list("diffusion_models_gguf")
                        if "diffusion_models_gguf"
                        in folder_paths.folder_names_and_paths
                        else []
                    ),
                    default="None",
                    tooltip="Select a GGUF format diffusion model from the diffusion_models_gguf directory.",
                ),
                # --- Weight & quantization settings ---
                io.Combo.Input(
                    "weight_dtype",
                    options=weight_dtype_options,
                    default="default",
                    tooltip="Preferred weight precision for loading checkpoint weights (e.g. default, fp8_e4m3fn, fp8_e5m2, bfloat16). FP8 saves VRAM.",
                ),
                io.Combo.Input(
                    "data_type",
                    options=["bfloat16", "float16"],
                    default="bfloat16",
                    tooltip="Data type precision for Nunchaku FP4 model layers. Select bfloat16 or float16.",
                ),
                io.Float.Input(
                    "cache_threshold",
                    default=0.0,
                    min=0.0,
                    max=1.0,
                    step=0.1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="GPU memory caching threshold for Nunchaku FP4 layers. Higher values reserve more GPU cache for layers.",
                ),
                io.Combo.Input(
                    "attention",
                    options=["flash-attention2", "nunchaku-fp16"],
                    default="flash-attention2",
                    tooltip="Attention implementation variant:\n• flash-attention2: Highly optimized for modern Ampere/Ada GPUs\n• nunchaku-fp16: Optimized FP16 attention kernel",
                ),
                io.Combo.Input(
                    "i2f_mode",
                    options=["enabled", "always"],
                    default="enabled",
                    tooltip="GEMM matrix multiplication implementation mode for Nunchaku GPU execution.",
                ),
                io.Combo.Input(
                    "cpu_offload",
                    options=["auto", "enable", "disable"],
                    default="auto",
                    tooltip="Toggle offloading of inactive parts of the model from VRAM to CPU RAM to save VRAM.",
                ),
                io.Int.Input(
                    "num_blocks_on_gpu",
                    default=30,
                    min=1,
                    max=60,
                    step=1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Number of transformer blocks to keep on the GPU for Nunchaku Qwen. Remaining blocks are offloaded to CPU.",
                ),
                io.Combo.Input(
                    "use_pin_memory",
                    options=["enable", "disable"],
                    default="enable",
                    tooltip="Enable pinned memory (host-allocated memory) for faster tensor transfers between system RAM and GPU VRAM.",
                ),
                # --- GGUF-specific settings ---
                io.Combo.Input(
                    "gguf_dequant_dtype",
                    options=["default", "target", "float32", "float16", "bfloat16"],
                    default="default",
                    tooltip="Dequantization data type precision for GGUF weights when patching them (default, float16, bfloat16, float32).",
                ),
                io.Combo.Input(
                    "gguf_patch_dtype",
                    options=["default", "target", "float32", "float16", "bfloat16"],
                    default="default",
                    tooltip="Data type precision to use when applying LoRA patches to GGUF weights.",
                ),
                io.Boolean.Input(
                    "gguf_patch_on_device",
                    default=False,
                    label_on="yes",
                    label_off="no",
                    tooltip="Apply patches directly on the GPU rather than host CPU memory (faster but requires more VRAM during load).",
                ),
                # --- Block swap (visible when 'block_swap' selected) ---
                io.Int.Input(
                    "blocks_to_swap",
                    default=10,
                    min=0,
                    max=100,
                    step=1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip=(
                        "Number of transformer blocks to swap/offload from GPU to CPU memory. "
                        "Higher values save significant VRAM but slow down generation. Recommended values (max blocks):\n"
                        "• Flux: ~10 (max 57)\n"
                        "• SD3: ~8 (max 24)\n"
                        "• Wan2.1: ~10 (max 40)\n"
                        "• HunyuanVideo: ~10 (max 60)\n"
                        "• LTX-Video: ~6 (max 28)\n"
                        "Set to 0 to disable."
                    ),
                ),
                io.Boolean.Input(
                    "offload_embeddings",
                    default=False,
                    label_on="Yes",
                    label_off="No",
                    tooltip=(
                        "Offloads the embedding and projection layers (text_embedding, img_emb, time_in) to CPU RAM. "
                        "Saves ~100-300MB VRAM at a slight speed cost."
                    ),
                ),
                # --- Model sampling (visible when 'model_sampling' selected) ---
                io.Combo.Input(
                    "sampling_method",
                    options=[
                        "None",
                        "SD3",
                        "AuraFlow",
                        "Flux",
                        "Stable Cascade",
                        "LCM",
                        "ContinuousEDM",
                        "ContinuousV",
                        "LTXV",
                    ],
                    default="None",
                    tooltip="Model-level sampling correction:\n• SD3/AuraFlow/Flux/Stable Cascade: Sets standard scheduler shifts\n• LCM: Configures latent consistency model scheduling\n• ContinuousEDM/ContinuousV: EDM scheduler\n• LTXV: LTX-Video scheduler",
                ),
                io.Combo.Input(
                    "sampling_subtype",
                    options=[
                        "eps",
                        "v_prediction",
                        "edm",
                        "edm_playground_v2.5",
                        "cosmos_rflow",
                    ],
                    default="eps",
                    tooltip="Subtype scheduling curve for ContinuousEDM sampling (e.g. eps, v_prediction, edm, cosmos_rflow).",
                ),
                io.Float.Input(
                    "shift",
                    default=3.0,
                    min=0.0,
                    max=10.0,
                    step=0.1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Universal scheduling shift multiplier. SD3 default: 3.0, AuraFlow: 1.73, Stable Cascade: 2.0.",
                ),
                io.Float.Input(
                    "base_shift",
                    default=0.5,
                    min=0.0,
                    max=10.0,
                    step=0.1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Base scheduling shift for Flux (default: 0.5) and LTX-Video (default: 2.05).",
                ),
                io.Int.Input(
                    "sampling_width",
                    default=1024,
                    min=16,
                    max=2000,
                    step=8,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Target resolution width for Flux/LTXV sampling shift calculations. Used to scale scheduler step sizes.",
                ),
                io.Int.Input(
                    "sampling_height",
                    default=1024,
                    min=16,
                    max=2000,
                    step=8,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Target resolution height for Flux/LTXV sampling shift calculations. Used to scale scheduler step sizes.",
                ),
                io.Int.Input(
                    "original_timesteps",
                    default=50,
                    min=1,
                    max=1000,
                    step=1,
                    tooltip="Original training timesteps of the LCM model (used to scale distilled step sizes).",
                ),
                io.Boolean.Input(
                    "zsnr",
                    default=False,
                    label_on="yes",
                    label_off="no",
                    tooltip="Zero-Terminal Signal-to-Noise Ratio (zsnr) adjustment to allow generating true darks/blacks.",
                ),
                io.Float.Input(
                    "sigma_max",
                    default=120.0,
                    min=0.0,
                    max=1000.0,
                    step=0.01,
                    tooltip="Maximum noise sigma boundary value for ContinuousEDM/ContinuousV scheduling.",
                ),
                io.Float.Input(
                    "sigma_min",
                    default=0.002,
                    min=0.0,
                    max=1000.0,
                    step=0.01,
                    tooltip="Minimum noise sigma boundary value for ContinuousEDM/ContinuousV scheduling.",
                ),
                # --- CLIP configuration (visible when 'clip' selected) ---
                io.Combo.Input(
                    "clip_source",
                    options=["Baked", "External", "External + Model File"],
                    default="Baked",
                    tooltip="Source of the text encoder (CLIP):\n• Baked: Uses CLIP embedded in the checkpoint\n• External: Uses separate CLIP files\n• External + Model File: Extends external loaders with the UNet file to auto-resolve baked projections (e.g. LTXV Gemma).",
                ),
                io.Combo.Input(
                    "clip_count",
                    options=["1", "2", "3", "4"],
                    default="1",
                    tooltip="Number of separate CLIP model/Text Encoder files to load concurrently (e.g., 2 for Flux, 3 for SD3).",
                ),
                io.Combo.Input(
                    "clip_name1",
                    options=clips,
                    default="None",
                    tooltip="Select the primary CLIP or Text Encoder checkpoint file.",
                ),
                io.Combo.Input(
                    "clip_name2",
                    options=clips,
                    default="None",
                    tooltip="Select the secondary CLIP or Text Encoder checkpoint file.",
                ),
                io.Combo.Input(
                    "clip_name3",
                    options=clips,
                    default="None",
                    tooltip="Select the third CLIP or Text Encoder checkpoint file.",
                ),
                io.Combo.Input(
                    "clip_name4",
                    options=clips,
                    default="None",
                    tooltip="Select the fourth CLIP or Text Encoder checkpoint file.",
                ),
                io.Combo.Input(
                    "clip_type",
                    options=[
                        "flux",
                        "flux2",
                        "sd3",
                        "sdxl",
                        "stable_cascade",
                        "stable_audio",
                        "hunyuan_dit",
                        "mochi",
                        "ltxv",
                        "hunyuan_video",
                        "pixart",
                        "cosmos",
                        "cogvideox",
                        "lumina2",
                        "wan",
                        "hidream",
                        "chroma",
                        "ace",
                        "omnigen2",
                        "qwen_image",
                        "hunyuan_image",
                        "hunyuan_video_15",
                        "ovis",
                        "kandinsky5",
                        "kandinsky5_image",
                        "newbie",
                        "lens",
                        "longcat_image",
                        "pixeldit",
                        "ideogram4",
                        "boogu",
                        "krea2",
                    ],
                    default="flux",
                    tooltip="CLIP loader wrapping/architecture mapping to match the target model type (e.g. flux, sd3, sdxl, wan, mochi, ltxv).",
                ),
                io.Boolean.Input(
                    "enable_clip_layer",
                    default=True,
                    label_on="yes",
                    label_off="no",
                    tooltip="Enables stopping CLIP text evaluation at a specific layer (CLIP skip) instead of evaluating all the way to the end.",
                ),
                io.Int.Input(
                    "stop_at_clip_layer",
                    default=-2,
                    min=-24,
                    max=-1,
                    step=1,
                    tooltip="Which layer to stop CLIP text evaluation at (e.g. -2 for SD1.5/SDXL, -3 for SD3). Negative values count back from final layer.",
                ),
                # --- VAE configuration (visible when 'vae' selected) ---
                io.Combo.Input(
                    "vae_source",
                    options=["Baked", "External"],
                    default="Baked",
                    tooltip="Source of the variational autoencoder (VAE):\n• Baked: Extract VAE from the checkpoint file\n• External: Load a standalone VAE file",
                ),
                io.Combo.Input(
                    "vae_name",
                    options=["None"] + folder_paths.get_filename_list("vae"),
                    default="None",
                    tooltip="Select a standalone VAE file to load from the vae directory.",
                ),
                # --- Latent configuration (visible when 'latent' selected) ---
                io.Combo.Input(
                    "resolution",
                    options=RESOLUTION_PRESETS,
                    default="1024x1024 (1:1 XL/SD3/Flux/HiDream)",
                    tooltip="Select a pre-calculated latent size preset (Select 'Custom' to manually specify width and height).\n"
                    "Note: Latent properties (channels, downscale ratio) are automatically detected from the active VAE (baked or external).",
                ),
                io.Int.Input(
                    "width",
                    default=1024,
                    min=16,
                    max=2000,
                    step=8,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Custom width for empty latent generation. Must be a multiple of 8.",
                ),
                io.Int.Input(
                    "height",
                    default=1024,
                    min=16,
                    max=2000,
                    step=8,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Custom height for empty latent generation. Must be a multiple of 8.",
                ),
                # --- LoRA configuration (visible when 'lora' selected) ---
                io.Combo.Input(
                    "lora_count",
                    options=["1", "2", "3"],
                    default="1",
                    tooltip="Number of active LoRA slots to configure.",
                ),
                io.Boolean.Input(
                    "lora_switch_1",
                    default=False,
                    label_on="ON",
                    label_off="OFF",
                    tooltip="Toggle to quickly enable or disable this LoRA slot without clearing the filename.",
                ),
                io.Combo.Input(
                    "lora_name_1",
                    options=loras,
                    default="None",
                    tooltip="Select a LoRA model file from the loras directory.",
                ),
                io.Float.Input(
                    "lora_weight_1",
                    default=1.0,
                    min=-10.0,
                    max=10.0,
                    step=0.1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Weight scale to apply to this LoRA's weights. 1.0 is standard strength; negative values invert the effect.",
                ),
                io.Boolean.Input(
                    "lora_switch_2",
                    default=False,
                    label_on="ON",
                    label_off="OFF",
                    tooltip="Toggle to quickly enable or disable this LoRA slot without clearing the filename.",
                ),
                io.Combo.Input(
                    "lora_name_2",
                    options=loras,
                    default="None",
                    tooltip="Select a LoRA model file from the loras directory.",
                ),
                io.Float.Input(
                    "lora_weight_2",
                    default=1.0,
                    min=-10.0,
                    max=10.0,
                    step=0.1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Weight scale to apply to this LoRA's weights. 1.0 is standard strength; negative values invert the effect.",
                ),
                io.Boolean.Input(
                    "lora_switch_3",
                    default=False,
                    label_on="ON",
                    label_off="OFF",
                    tooltip="Toggle to quickly enable or disable this LoRA slot without clearing the filename.",
                ),
                io.Combo.Input(
                    "lora_name_3",
                    options=loras,
                    default="None",
                    tooltip="Select a LoRA model file from the loras directory.",
                ),
                io.Float.Input(
                    "lora_weight_3",
                    default=1.0,
                    min=-10.0,
                    max=10.0,
                    step=0.1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Weight scale to apply to this LoRA's weights. 1.0 is standard strength; negative values invert the effect.",
                ),
                # --- Sampler settings (visible when 'sampler' selected) ---
                io.Combo.Input(
                    "sampler_name",
                    options=comfy.samplers.KSampler.SAMPLERS,
                    default="euler",
                    tooltip="Select the ComfyUI sampling algorithm (e.g. euler, heun, dpmpp_2m).",
                ),
                io.Combo.Input(
                    "scheduler",
                    options=comfy.samplers.KSampler.SCHEDULERS,
                    default="normal",
                    tooltip="Select the noise scheduling curve (e.g. normal, karras, exponential, sgm_uniform).",
                ),
                io.Int.Input(
                    "steps",
                    default=20,
                    min=1,
                    max=150,
                    step=1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Number of denoising steps. Higher values take longer but refine the image; 20-30 steps is standard for most models.",
                ),
                io.Float.Input(
                    "cfg",
                    default=8.0,
                    min=1.0,
                    max=30.0,
                    step=0.1,
                    round=0.1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Classifier-Free Guidance (CFG) scale. Controls prompt adherence. Higher values enforce the prompt strictly but can burn colors; 1.0 disables it.",
                ),
                io.Float.Input(
                    "flux_guidance",
                    default=3.5,
                    min=0.0,
                    max=10.0,
                    step=0.1,
                    display_mode=SLIDER_DISPLAY,
                    tooltip="Guidance scale specific to Flux models. Controls prompt adherence/contrast without standard CFG burn.",
                ),
                io.Int.Input(
                    "batch_size",
                    default=1,
                    min=1,
                    max=4096,
                    tooltip="Number of latent images to generate in parallel in a single execution batch.",
                ),
                # --- Audio VAE (LTXV/LTX2; visible when 'audio_vae' selected) ---
                io.Combo.Input(
                    "audio_vae_source",
                    options=["External", "Baked"],
                    default="External",
                    tooltip="Source of the audio decoder/VAE:\n• External: Loads separate vocoder/audio-VAE files\n• Baked: Extracts audio VAE directly from LTX2 all-in-one model files",
                ),
                io.Combo.Input(
                    "audio_vae_name",
                    options=["None"] + folder_paths.get_filename_list("vae"),
                    default="None",
                    tooltip="Select a standalone LTXV/LTX2 audio VAE file from the vae directory.",
                ),
                # --- Integrity / download (placed near the bottom so they don't float to the
                # top when the templates feature is disabled) ---
                io.Combo.Input(
                    "verify_file",
                    options=["off", "sidecar", "verify"],
                    default="off",
                    tooltip="Primary model integrity mode:\n• off: No model hashing.\n• sidecar: Computes and saves a .sha256 baseline next to the selected primary model.\n• verify: Compares the primary model when a trusted SHA256 is available and stops on mismatch; without one, it records a local baseline and continues loading.\n\nExternal CLIP, VAE, audio VAE, and LoRA files always retain path and safe-format validation. For downloads, paste a CivitAI URN:AIR or SHA256 into air_or_hash.",
                ),
                io.String.Input(
                    "expected_hashes",
                    default="{}",
                    socketless=True,
                    tooltip="Internal JSON database mapping filenames to expected SHA256 and CivitAI AIR metadata. Managed automatically.",
                ),
                io.String.Input(
                    "air_or_hash",
                    default="",
                    socketless=True,
                    tooltip="Paste CivitAI AIR (urn:air:...) or a SHA256 hash here. Add +<fileId> to an AIR to select an exact file when several artifacts share a precision. Used to verify integrity on load or trigger auto-downloads if files are missing.",
                ),
                io.String.Input(
                    "download_locators",
                    default="[]",
                    socketless=True,
                    tooltip="Internal JSON store containing locator downloads. Managed automatically.",
                ),
                io.Combo.Input(
                    "download_target_role",
                    options=list(DOWNLOAD_TARGET_ROLES),
                    default="",
                    tooltip="Select which ComfyUI input folder to save downloaded models into (e.g. checkpoints, diffusion_models, vae).",
                ),
                io.Combo.Input(
                    "model_precision",
                    options=list(MODEL_PRECISION_OPTIONS),
                    default="default",
                    tooltip="Preferred weight precision (e.g. fp16, bf16, fp8) when downloading models from CivitAI using URN:AIR locators. 'default' grabs the primary file; an explicit precision selects the unique largest matching artifact. Use the AIR +<fileId> suffix when matching sizes are missing or tied.",
                ),
                io.Int.Input(
                    "seed",
                    default=0,
                    min=-3,
                    max=2**64 - 1,
                    tooltip="Controls generation reproducibility. Use specific values for deterministic output:\n• -1: Randomize the seed on every execution\n• -2: Increment the seed by 1 after each run\n• -3: Decrement the seed by 1 after each run",
                ),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def validate_inputs(cls, **kwargs):
        try:
            LoadRequest.from_kwargs(kwargs, smart=True)
        except (LoaderValidationError, TypeError) as error:
            return str(error)
        return True

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        mtime = get_template_mtime()
        return str(mtime) if mtime else "0"

    @classmethod
    def execute(cls, **kwargs):
        result = execute_smart_request(**kwargs)
        return io.NodeOutput(result.pipe)
