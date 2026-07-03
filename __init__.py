# ComfyUI_Eclipse Extension Loader
# Initializes and loads all custom nodes for ComfyUI_Eclipse using the ComfyUI V3 extension API.
WEB_DIRECTORY = "./js"

import sys
# Prevent shadowing of ComfyUI's top-level utils package by comfy/utils.py when nodes.py is imported first.
if 'utils' not in sys.modules:
    try:
        import utils  # type: ignore
    except ImportError:
        pass

import os
from .core import version
from .core.logger import log, cstr

log.msg("", f"Version: {version}")

# Early check of wrappers (for consistent startup logging)
try:
    from .core import gguf_wrapper
except Exception as e:
    log.warning("GGUF Wrapper", f"Failed to load: {e}")

try:
    from .core import nunchaku_wrapper
except Exception as e:
    log.warning("Nunchaku Wrapper", f"Failed to load: {e}")

# Initialize Eclipse folder structure
from .core.migration import run_migrations

run_migrations()

# Dual-install safety — warn if standalone SmartLML is still active
try:
    import pathlib as _pathlib
    _custom_nodes = _pathlib.Path(__file__).parent.parent
    _sml_active = ((_custom_nodes / "comfyui_smartlml" / "__init__.py").exists() or
                   (_custom_nodes / "ComfyUI_SmartLML" / "__init__.py").exists())
    if _sml_active:
        log.warning("", "⚠ Standalone ComfyUI_SmartLML is still active!")
        log.warning("", "  SmartLML is now included in Eclipse.")
        log.warning("", "  Please rename or remove the comfyui_smartlml folder to avoid conflicts.")
        log.warning("", "  e.g.: mv comfyui_smartlml comfyui_smartlml.disabled")
except Exception:
    pass

# Initialize server endpoints
try:
    from .core.server_endpoints import initialize_endpoints
    initialize_endpoints()
except Exception as e:
    log.warning("", f"Failed to initialize server endpoints: {e}")

# --- SML Initialization ---

# Sync YOLO registry with on-disk models
try:
    from .core.sml.model_registry import sync_yolo_registry
    sync_yolo_registry()
except Exception as e:
    log.warning("SML", f"Could not sync YOLO registry: {e}")

# Initialize LLM paths
try:
    from .core.sml.config_templates import ensure_config_exists, initialize_llm_paths
    ensure_config_exists()
    initialize_llm_paths()
except Exception as e:
    log.warning("SML", f"Could not initialize LLM paths: {e}")

# Florence-2 wrapper check
try:
    from .core.sml import florence2_wrapper
    if not florence2_wrapper.FLORENCE2_CUSTOM_AVAILABLE and florence2_wrapper.transformers_version < (5, 0):
        log.msg("Florence-2", "Tip: Install comfyui-florence2 extension for better compatibility")
except Exception as e:
    log.warning("Florence-2 Wrapper", f"Failed to load: {e}")

# hf_transfer (fast HuggingFace downloads)
os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"

# Docker availability check
try:
    from .core.sml.docker_utils import is_docker_installed, get_docker_version
    if is_docker_installed():
        log.msg("Docker", f"✓ {get_docker_version()}")
        try:
            from .core.sml.device import detect_gpu_vendor
            gpu_vendor = detect_gpu_vendor()
            vendor_map = {
                "nvidia": "NVIDIA (--gpus all)",
                "amd": "AMD/ROCm (/dev/kfd, /dev/dri)",
                "none": "None detected (CPU mode)"
            }
            log.msg("Docker", f"GPU vendor: {vendor_map.get(gpu_vendor, gpu_vendor)}")
        except Exception:
            pass
except Exception:
    pass

# SML server endpoints
try:
    from .core.sml.server_endpoints import initialize_endpoints as sml_initialize_endpoints
    sml_initialize_endpoints()
except Exception as e:
    log.warning("SML", f"Failed to initialize SML server endpoints: {e}")

# V3 Extension Registration
from comfy_api.latest import ComfyExtension, io #type: ignore

class EclipseExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        # Conversion nodes
        from .py.RvConversion_ConcatMulti import RvConversion_ConcatMulti
        from .py.RvConversion_ConvertPrimitive import RvConversion_ConvertPrimitive
        from .py.RvConversion_ConvertToBatch import RvConversion_ConvertToBatch
        from .py.RvConversion_RIFEMultiplier import RvConversion_RIFEMultiplier
        from .py.RvConversion_ConvertToList import RvConversion_ConvertToList
        from .py.RvConversion_DetectionToBboxes import RvConversion_DetectionToBboxes
        from .py.RvConversion_ImageConvert import RvConversion_ImageConvert
        from .py.RvConversion_Join import RvConversion_Join
        from .py.RvConversion_LoraStackToString import RvConversion_LoraStackToString
        from .py.RvConversion_MergeStrings import RvConversion_MergeStrings
        from .py.RvConversion_StringFromList import RvConversion_StringFromList
        from .py.RvConversion_WidgetToString import RvConversion_WidgetToString
        # Folder nodes
        from .py.RvFolder_AddFolder import RvFolder_AddFolder
        from .py.RvFolder_FilenamePrefix import RvFolder_FilenamePrefix
        from .py.RvFolder_FolderPath import RvFolder_FolderPath
        from .py.legacy.legacy_SmartFolder import RvFolder_SmartFolder
        from .py.RvFolder_SmartFolder import RvFolder_SmartFolder_v2
        # Image nodes
        # from .py.RvImage_GeminiGenerate import RvImage_GeminiGenerate
        # from .py.RvImage_GeminiEdit import RvImage_GeminiEdit
        from .py.RvImage_AddWatermarkImage import RvImage_AddWatermarkImage
        from .py.RvImage_LoadImage import RvImage_LoadImage
        from .py.RvImage_LoadImage_Pipe import RvImage_LoadImage_Pipe
        from .py.RvImage_LoadImageFromFolder import RvImage_LoadImageFromFolder
        from .py.RvImage_LoadImageFromFolder_Pipe import RvImage_LoadImageFromFolder_Pipe
        from .py.RvImage_LoadBatchFromFolder import RvImage_LoadBatchFromFolder
        from .py.RvImage_LoadBatchFromFolderAdvanced import RvImage_LoadBatchFromFolderAdvanced
        from .py.legacy.legacy_LoadImagePath import RvImage_LoadImagePath
        from .py.legacy.legacy_LoadImagePath_Pipe import RvImage_LoadImagePath_Pipe
        from .py.RvImage_Preview_Image import RvImage_Preview_Image
        from .py.RvImage_Preview_Image_DOM import RvImage_Preview_Image_DOM
        from .py.RvImage_Preview_Mask import RvImage_Preview_Mask
        from .py.RvImage_Preview_Video import RvImage_Preview_Video
        from .py.RvImage_Selector import RvImage_Selector
        from .py.RvImage_Save_Video import RvImage_Save_Video
        from .py.RvImage_ImageComparer import RvImage_ImageComparer
        from .py.RvImage_ColorMatch import RvImage_ColorMatch
        from .py.RvImage_CropByMask import RvImage_CropByMask
        from .py.RvImage_Resize import RvImage_Resize
        from .py.RvImage_Soften import RvImage_Soften
        from .py.legacy.legacy_SaveImages import RvImage_SaveImages
        from .py.RvImage_SaveImages import RvImage_SaveImages_v2
        from .py.RvImage_SEGSPreview import RvImage_SEGSPreview
        from .py.RvImage_SEGSPreview_Simple import RvImage_SEGSPreview_Simple
        from .py.RvMask_ToSEGS import RvMask_ToSEGS
        from .py.RvDetailer_ForEachPipe import RvDetailer_ForEachPipe
        from .py.RvImage_TileAssembly import RvImage_TileAssembly
        from .py.RvImage_TileDecodeAssembly import RvImage_TileDecodeAssembly
        from .py.RvImage_TileSplit import RvImage_TileSplit
        from .py.RvImage_TextImageWithFX import RvImage_TextImageWithFX
        from .py.RvImage_ImageWithFX import RvImage_ImageWithFX
        from .py.RvImage_AlignSize import RvImage_AlignSize
        from .py.RvImage_GetLastImage import RvImage_GetLastImage
        from .py.RvImage_GetFirstImage import RvImage_GetFirstImage
        from .py.RvImage_BatchSlice import RvImage_BatchSlice
        from .py.RvImage_BatchInterleave import RvImage_BatchInterleave
        from .py.RvImage_BatchStrip import RvImage_BatchStrip
        from .py.RvImage_BatchExtendWithOverlap import RvImage_BatchExtendWithOverlap
        from .py.RvImage_InsetCrop import RvImage_InsetCrop
        from .py.RvImage_FilterAdjustments import RvImage_FilterAdjustments
        from .py.RvImage_Rescale import RvImage_Rescale
        from .py.RvImage_UpscaleWithModel import RvImage_UpscaleWithModel
        from .py.RvImage_UpscaleWithModel_v2 import RvImage_UpscaleWithModel_v2
        # Loader nodes
        from .py.legacy.legacy_Checkpoint_Loader_Small import RvLoader_Checkpoint_Loader_Small
        from .py.legacy.legacy_Checkpoint_Loader_Small_Pipe import RvLoader_Checkpoint_Loader_Small_Pipe
        from .py.legacy.legacy_SmartLoader import RvLoader_SmartLoader
        from .py.legacy.legacy_SmartLoader_Basic import RvLoader_SmartLoader_Basic
        from .py.legacy.legacy_SmartLoader_Plus import RvLoader_SmartLoader_Plus
        from .py.legacy.legacy_SmartLoader_v2 import RvLoader_SmartLoader_v2
        from .py.legacy.legacy_SmartLoader_Basic_v2 import RvLoader_SmartLoader_Basic_v2
        from .py.legacy.legacy_SmartLoader_Plus_v2 import RvLoader_SmartLoader_Plus_v2
        from .py.legacy.legacy_SmartModelLoader import Legacy_SmartModelLoader
        from .py.RvLoader_SmartModelLoader import RvLoader_SmartModelLoader
        from .py.RvLoader_ModelLoader import RvLoader_ModelLoader
        from .py.RvLoader_ModelLoaderPipe import RvLoader_ModelLoaderPipe
        from .py.RvLoader_ClipLoader import RvLoader_ClipLoader
        from .py.RvLoader_VaeLoader import RvLoader_VaeLoader
        from .py.RvLoader_VaeLoaderVideoAudio import RvLoader_VaeLoaderVideoAudio
        from .py.RvAudio_LoadAudio import RvAudio_LoadAudio
        # SML Loader nodes
        try:
            from .py.RvLoader_SmartModelLoader_LM import RvLoader_SmartModelLoader_LM
            from .py.RvLoader_SmartDetection import RvLoader_Detection as RvLoader_SmartDetection
            _sml_available = True
        except Exception as e:
            log.warning("SML", f"Smart LML nodes unavailable: {e}")
            _sml_available = False
        # SML Legacy wrappers (backward compat for old [SML] and pre-v3 workflows)
        try:
            from .py.legacy.legacy_SmartModelLoader_LM import Legacy_SmartModelLoader_LM
            from .py.legacy.legacy_SmartDetection import Legacy_SmartDetection
            from .py.legacy.legacy_SmartLML_v2 import Legacy_SmartLML_v2, Legacy_SmartLML_v2_Eclipse
            from .py.legacy.legacy_SmartLML_v3 import Legacy_SmartLML_v3
            from .py.legacy.legacy_PipeOut_LM_AdvancedOptions import Legacy_PipeOut_LM_AdvancedOptions, Legacy_PipeOut_LM_AdvancedOptions_Eclipse
            _sml_legacy_available = True
        except Exception as e:
            log.warning("SML", f"Legacy wrappers unavailable: {e}")
            _sml_legacy_available = False
        # Logic nodes
        from .py.RvLogic_Boolean import RvLogic_Boolean
        from .py.RvLogic_Float import RvLogic_Float
        from .py.RvLogic_Integer import RvLogic_Integer
        from .py.RvLogic_Integer_Gen import RvLogic_IntegerGen
        from .py.RvLogic_None import RvLogic_None
        from .py.RvLogic_String import RvLogic_String
        from .py.RvLogic_Seed import RvLogic_Seed
        from .py.RvLogic_Seed_32bit import RvLogic_Seed_32bit
        # Sampler nodes
        from .py.RvSampler_KSamplerPipe import RvSampler_KSamplerPipe
        from .py.RvSampler_KSamplerKargim import RvSampler_KSamplerKargim
        # Pipe IO nodes
        from .py.RvPipe_IO_12CH_Any import RvPipe_IO_12CH_Any
        from .py.RvPipe_IO_24CH_Any import RvPipe_IO_24CH_Any
        from .py.RvPipe_IO_36CH_Any import RvPipe_IO_36CH_Any
        from .py.RvPipe_IO_Context_Image import RvPipe_IO_Context_Image
        from .py.RvPipe_IO_Context_Video import RvPipe_IO_Context_Video
        from .py.RvPipe_IO_Context_WanVideoWrapper import RvPipe_IO_Context_WanVideoWrapper
        from .py.legacy.legacy_IO_CheckpointLoader import RvPipe_IO_CheckpointLoader
        from .py.RvPipe_IO_CheckpointLoader_v2 import RvPipe_IO_CheckpointLoader_v2
        from .py.RvPipe_IO_LoadImage import RvPipe_IO_LoadImage
        from .py.RvPipe_IO_Generation_Data import RvPipe_IO_Generation_Data
        from .py.RvPipe_IO_Generation_Data_Gated import RvPipe_IO_Generation_Data_Gated
        from .py.legacy.legacy_IO_Sampler_Settings import RvPipe_IO_Sampler_Settings
        from .py.legacy.legacy_IO_Sampler_Settings_v2 import RvPipe_IO_Sampler_Settings_v2
        from .py.RvPipe_IO_Sampler_Settings_v21 import RvPipe_IO_Sampler_Settings_v21
        from .py.RvPipe_IO_Sampler_Settings_v22 import RvPipe_IO_Sampler_Settings_v22
        from .py.RvPipe_IO_Sampler_Settings_v23 import RvPipe_IO_Sampler_Settings_v23
        # Pipe Out nodes
        from .py.legacy.legacy_Out_CheckpointLoader import RvPipe_Out_CheckpointLoader
        from .py.legacy.legacy_Out_LoadDirectorySettings import RvPipe_Out_LoadDirectorySettings
        from .py.legacy.legacy_Out_LoadImage import RvPipe_Out_LoadImage
        from .py.legacy.legacy_Out_Sampler_Settings import RvPipe_Out_Sampler_Settings
        from .py.RvPipe_Out_SmartFolder import RvPipe_Out_SmartFolder
        from .py.RvPipe_Out_VCNameGen import RvPipe_Out_VCNameGen
        from .py.RvPipe_Out_WanVideo_Setup import RvPipe_Out_WanVideo_Setup
        # Basic Pipe nodes
        from .py.legacy.legacy_ToBasicPipe import RvPipe_ToBasicPipe
        from .py.legacy.legacy_FromBasicPipe import RvPipe_FromBasicPipe
        from .py.legacy.legacy_BasicPipe import RvPipe_BasicPipe
        # Router nodes
        from .py.RvRouter_Any_DualSwitch import RvRouter_Any_DualSwitch
        from .py.RvRouter_Any_DualSwitch_purge import RvRouter_Any_DualSwitch_purge
        from .py.RvRouter_Any_MultiSwitch import RvRouter_Any_MultiSwitch
        from .py.RvRouter_Any_MultiSwitch_purge import RvRouter_Any_MultiSwitch_purge
        from .py.RvRouter_Any_MultiSwitch_lazy import RvRouter_Any_MultiSwitch_lazy
        from .py.RvRouter_Any_MultiSwitch_lazy_purge import RvRouter_Any_MultiSwitch_lazy_purge
        from .py.RvRouter_Any_Passer import RvRouter_Any_Passer
        from .py.RvRouter_Any_Passer_purge import RvRouter_Any_Passer_purge
        from .py.RvRouter_Boolean_Passer import RvRouter_Boolean_Passer
        from .py.RvRouter_Float_Passer import RvRouter_Float_Passer
        from .py.RvRouter_Int_Passer import RvRouter_Int_Passer
        from .py.RvRouter_String_Passer import RvRouter_String_Passer
        from .py.RvRouter_Model_Passer import RvRouter_Model_Passer
        from .py.RvRouter_Clip_Passer import RvRouter_Clip_Passer
        from .py.RvRouter_Vae_Passer import RvRouter_Vae_Passer
        from .py.RvRouter_Segs_Passer import RvRouter_Segs_Passer
        from .py.RvRouter_Audio_Passer import RvRouter_Audio_Passer
        from .py.RvRouter_BasicPipe_Passer import RvRouter_BasicPipe_Passer
        from .py.RvRouter_Conditioning_Passer import RvRouter_Conditioning_Passer
        from .py.RvRouter_ControlNet_Passer import RvRouter_ControlNet_Passer
        from .py.RvRouter_DetailerPipe_Passer import RvRouter_DetailerPipe_Passer
        from .py.RvRouter_Image_Passer import RvRouter_Image_Passer
        from .py.RvRouter_Latent_Passer import RvRouter_Latent_Passer
        from .py.RvRouter_Mask_Passer import RvRouter_Mask_Passer
        from .py.RvRouter_WanVideoModel_Passer import RvRouter_WanVideoModel_Passer
        from .py.RvRouter_Pipe_Passer import RvRouter_Pipe_Passer
        from .py.RvRouter_IfElse import RvRouter_IfElse
        from .py.RvImage_LoopImageSelector import RvImage_LoopImageSelector
        from .py.legacy.legacy_IfElse_Fallback import RvRouter_IfElse_Fallback
        # Settings nodes
        from .py.RvSettings_ControlNetUnionType import RvSettings_ControlNetUnionType
        from .py.RvSettings_CustomSize import RvSettings_CustomSize
        from .py.RvSettings_Image_Resolution import RvSettings_Image_Resolution
        from .py.RvSettings_Video_Resolution import RvSettings_Video_Resolution
        from .py.legacy.legacy_LoadDirectorySettings import RvSettings_LoadDirectorySettings
        from .py.RvSettings_Sampler_Selection import RvSettings_Sampler_Selection
        from .py.legacy.legacy_Sampler_Settings import RvSettings_Sampler_Settings
        from .py.legacy.legacy_Sampler_Settings_NI import RvSettings_Sampler_Settings_NI
        from .py.legacy.legacy_Sampler_Settings_NI_v2 import RvSettings_Sampler_Settings_NI_v2
        from .py.legacy.legacy_Sampler_Settings_NI_Seed import RvSettings_Sampler_Settings_NI_Seed
        from .py.legacy.legacy_Sampler_Settings_NI_Seed_v2 import RvSettings_Sampler_Settings_NI_Seed_v2
        from .py.legacy.legacy_Sampler_Settings_NI_Seed_v21 import RvSettings_Sampler_Settings_NI_Seed_v21
        from .py.RvSettings_SmartSamplerSettings import RvSettings_SmartSamplerSettings
        from .py.RvSettings_SmartSamplerSettings_v2 import RvSettings_SmartSamplerSettings_v2
        from .py.legacy.legacy_Sampler_Settings_Seed import RvSettings_Sampler_Settings_Seed
        from .py.legacy.legacy_Sampler_Settings_Seed_v2 import RvSettings_Sampler_Settings_Seed_v2
        from .py.legacy.legacy_Sampler_Settings_Small import RvSettings_Sampler_Settings_Small
        from .py.legacy.legacy_Sampler_Settings_Small_Seed import RvSettings_Sampler_Settings_Small_Seed
        from .py.legacy.legacy_RvSettings_VCNameGen_v1 import RvSettings_VCNameGen_v1
        from .py.legacy.legacy_RvSettings_VCNameGen_v2 import RvSettings_VCNameGen_v2
        from .py.RvSettings_WanVideo_Setup import RvSettings_WanVideo_Setup
        # Text nodes
        from .py.RvCond_CLIPTextEncode import RvCond_CLIPTextEncode
        from .py.RvCond_CLIPTextEncodeAdvanced import RvCond_CLIPTextEncodeAdvanced
        from .py.RvCond_ConditioningZeroOut import RvCond_ConditioningZeroOut
        from .py.RvText_DeDuplicate import RvText_DeDuplicate
        from .py.RvText_DualText import RvText_DualText
        from .py.RvText_Multiline import RvText_Multiline
        from .py.RvText_Multiline_List import RvText_Multiline_List
        from .py.RvText_PromptStyler import RvText_PromptStyler
        from .py.RvText_ReadPromptFiles import RvText_ReadPromptFiles
        from .py.RvText_ReplaceString import RvText_ReplaceString
        from .py.legacy.legacy_ReplaceStringV2 import RvText_ReplaceStringV2
        from .py.RvText_ReplaceString_Adv import RvText_ReplaceStringV3
        from .py.RvText_SavePrompt import RvText_SavePrompt
        from .py.RvText_SmartPrompt import RvText_SmartPrompt_All
        from .py.RvText_SmartPromptV2 import RvText_SmartPrompt_v2
        from .py.RvText_WildcardProcessor import RvText_WildcardProcessor
        # Tools nodes
        from .py.legacy.legacy_FastMuter import RvTools_FastMuter
        from .py.legacy.legacy_FastBypasser import RvTools_FastBypasser
        from .py.RvTools_FastModeToggle import RvTools_FastModeToggle
        from .py.RvTools_FastModeSwitcher import RvTools_FastModeSwitcher
        from .py.legacy.legacy_FastGroupsMuter import RvTools_FastGroupsMuter
        from .py.legacy.legacy_FastGroupsBypasser import RvTools_FastGroupsBypasser
        from .py.RvTools_NodeModeRepeater import RvTools_NodeModeRepeater
        from .py.RvTools_ModeRelay import RvTools_ModeRelay
        from .py.legacy.legacy_ModeBridge import RvTools_ModeBridge
        from .py.RvTools_ModeBridgeSet import RvTools_ModeBridgeSet
        from .py.RvTools_ModeBridgeGet import RvTools_ModeBridgeGet
        from .py.RvTools_NodeCollector import RvTools_NodeCollector
        from .py.RvVideo_LoopCalc import RvVideo_LoopCalc
        from .py.RvAudio_LoopCalc import RvAudio_LoopCalc
        from .py.RvVideo_LoopKeepCalc import RvVideo_LoopKeepCalc
        from .py.RvAudio_LoopAlignSilence import RvAudio_LoopAlignSilence
        from .py.RvVideo_TrimToShortest import RvVideo_TrimToShortest
        from .py.RvTools_LoraStack import RvTools_LoraStack
        from .py.RvTools_LoraStack_Apply import RvTools_LoraStack_Apply
        try:
            from .py.RvTools_NunchakuPuLID import RvTools_NunchakuPuLIDLoader, RvTools_NunchakuPuLIDApply
            _nunchaku_available = True
        except Exception as e:
            log.warning("NunchakuPuLID", f"Nunchaku nodes unavailable: {e}")
            _nunchaku_available = False
        from .py.RvTools_RAMCleanup import RvTools_RAMCleanup
        from .py.RvTools_ResolutionScale import RvTools_ResolutionScale
        from .py.RvTools_ShowAny import RvTools_ShowAny
        from .py.RvTools_ShowText import RvTools_ShowText
        from .py.RvTools_Stop import RvTools_Stop
        from .py.legacy.legacy_RvTools_VideoClips_Combine import RvTools_VideoClips_Combine
        from .py.legacy.legacy_RvTools_VideoClips_SeamlessJoin import RvTools_VideoClips_SeamlessJoin
        from .py.RvImage_FrameConsistency import RvImage_FrameConsistency
        from .py.RvTools_VRAMCleanUp import RvTools_VRAMCleanUp
        from .py.RvTools_BlockSwap import RvTools_BlockSwap

        node_list: list[type[io.ComfyNode]] = [
            # Conversion
            RvConversion_ConcatMulti,
            RvConversion_ConvertPrimitive,
            RvConversion_ConvertToBatch,
            RvConversion_RIFEMultiplier,
            RvConversion_ConvertToList,
            RvConversion_DetectionToBboxes,
            RvConversion_ImageConvert,
            RvConversion_Join,
            RvConversion_LoraStackToString,
            RvConversion_MergeStrings,
            RvConversion_StringFromList,
            RvConversion_WidgetToString,
            # Folder
            RvFolder_AddFolder,
            RvFolder_FilenamePrefix,
            RvFolder_FolderPath,
            RvFolder_SmartFolder,
            RvFolder_SmartFolder_v2,
            # Image
            # RvImage_GeminiGenerate,
            # RvImage_GeminiEdit,
            RvImage_AddWatermarkImage,
            RvImage_LoadImage,
            RvImage_LoadImage_Pipe,
            RvImage_LoadImageFromFolder,
            RvImage_LoadImageFromFolder_Pipe,
            RvImage_LoadBatchFromFolder,
            RvImage_LoadBatchFromFolderAdvanced,
            RvImage_LoadImagePath,
            RvImage_LoadImagePath_Pipe,
            RvImage_Preview_Image,
            RvImage_Preview_Image_DOM,
            RvImage_Preview_Mask,
            RvImage_Preview_Video,
            RvImage_Selector,
            RvImage_Save_Video,
            RvImage_ImageComparer,
            RvImage_ColorMatch,
            RvImage_CropByMask,
            RvImage_Resize,
            RvImage_Soften,
            RvImage_SaveImages,
            RvImage_SaveImages_v2,
            RvImage_SEGSPreview,
            RvImage_SEGSPreview_Simple,
            RvMask_ToSEGS,
            RvDetailer_ForEachPipe,
            RvImage_TileAssembly,
            RvImage_TileDecodeAssembly,
            RvImage_TileSplit,
            RvImage_TextImageWithFX,
            RvImage_ImageWithFX,
            RvImage_AlignSize,
            RvImage_GetLastImage,
            RvImage_GetFirstImage,
            RvImage_BatchSlice,
            RvImage_BatchInterleave,
            RvImage_BatchStrip,
            RvImage_BatchExtendWithOverlap,
            RvImage_InsetCrop,
            RvImage_FilterAdjustments,
            RvImage_Rescale,
            RvImage_UpscaleWithModel,
            RvImage_UpscaleWithModel_v2,
            # Loader
            RvLoader_Checkpoint_Loader_Small,
            RvLoader_Checkpoint_Loader_Small_Pipe,
            RvLoader_SmartLoader,
            RvLoader_SmartLoader_Basic,
            RvLoader_SmartLoader_Plus,
            RvLoader_SmartLoader_v2,
            RvLoader_SmartLoader_Basic_v2,
            RvLoader_SmartLoader_Plus_v2,
            Legacy_SmartModelLoader,
            RvLoader_SmartModelLoader,
            RvLoader_ModelLoader,
            RvLoader_ModelLoaderPipe,
            RvLoader_ClipLoader,
            RvLoader_VaeLoader,
            RvLoader_VaeLoaderVideoAudio,
            RvAudio_LoadAudio,
            # Logic
            RvLogic_Boolean,
            RvLogic_Float,
            RvLogic_Integer,
            RvLogic_IntegerGen,
            RvLogic_None,
            RvLogic_String,
            RvLogic_Seed,
            RvLogic_Seed_32bit,
            # Sampler
            RvSampler_KSamplerPipe,
            RvSampler_KSamplerKargim,
            # Pipe IO
            RvPipe_IO_12CH_Any,
            RvPipe_IO_24CH_Any,
            RvPipe_IO_36CH_Any,
            RvPipe_IO_Context_Image,
            RvPipe_IO_Context_Video,
            RvPipe_IO_Context_WanVideoWrapper,
            RvPipe_IO_CheckpointLoader,
            RvPipe_IO_CheckpointLoader_v2,
            RvPipe_IO_LoadImage,
            RvPipe_IO_Generation_Data,
            RvPipe_IO_Generation_Data_Gated,
            RvPipe_IO_Sampler_Settings,
            RvPipe_IO_Sampler_Settings_v2,
            RvPipe_IO_Sampler_Settings_v21,
            RvPipe_IO_Sampler_Settings_v22,
            RvPipe_IO_Sampler_Settings_v23,
            # Pipe Out
            RvPipe_Out_CheckpointLoader,
            RvPipe_Out_LoadDirectorySettings,
            RvPipe_Out_LoadImage,
            RvPipe_Out_Sampler_Settings,
            RvPipe_Out_SmartFolder,
            RvPipe_Out_VCNameGen,
            RvPipe_Out_WanVideo_Setup,
            # Basic Pipe
            RvPipe_ToBasicPipe,
            RvPipe_FromBasicPipe,
            RvPipe_BasicPipe,
            # Router
            RvRouter_Any_DualSwitch,
            RvRouter_Any_DualSwitch_purge,
            RvRouter_Any_MultiSwitch,
            RvRouter_Any_MultiSwitch_purge,
            RvRouter_Any_MultiSwitch_lazy,
            RvRouter_Any_MultiSwitch_lazy_purge,
            RvRouter_Any_Passer,
            RvRouter_Any_Passer_purge,
            RvRouter_Boolean_Passer,
            RvRouter_Float_Passer,
            RvRouter_Int_Passer,
            RvRouter_String_Passer,
            RvRouter_Model_Passer,
            RvRouter_Clip_Passer,
            RvRouter_Vae_Passer,
            RvRouter_Segs_Passer,
            RvRouter_Audio_Passer,
            RvRouter_BasicPipe_Passer,
            RvRouter_Conditioning_Passer,
            RvRouter_ControlNet_Passer,
            RvRouter_DetailerPipe_Passer,
            RvRouter_Image_Passer,
            RvRouter_Latent_Passer,
            RvRouter_Mask_Passer,
            RvRouter_WanVideoModel_Passer,
            RvRouter_Pipe_Passer,
            RvRouter_IfElse,
            RvImage_LoopImageSelector,
            # Settings
            RvSettings_ControlNetUnionType,
            RvSettings_CustomSize,
            RvSettings_Image_Resolution,
            RvSettings_Video_Resolution,
            RvSettings_LoadDirectorySettings,
            RvSettings_Sampler_Selection,
            RvSettings_Sampler_Settings,
            RvSettings_Sampler_Settings_NI,
            RvSettings_Sampler_Settings_NI_v2,
            RvSettings_Sampler_Settings_NI_Seed,
            RvSettings_Sampler_Settings_NI_Seed_v2,
            RvSettings_Sampler_Settings_NI_Seed_v21,
            RvSettings_SmartSamplerSettings,
            RvSettings_SmartSamplerSettings_v2,
            RvSettings_Sampler_Settings_Seed,
            RvSettings_Sampler_Settings_Seed_v2,
            RvSettings_Sampler_Settings_Small,
            RvSettings_Sampler_Settings_Small_Seed,
            RvSettings_VCNameGen_v1,
            RvSettings_VCNameGen_v2,
            RvSettings_WanVideo_Setup,
            # Text
            RvCond_CLIPTextEncode,
            RvCond_CLIPTextEncodeAdvanced,
            RvCond_ConditioningZeroOut,
            RvText_DeDuplicate,
            RvText_DualText,
            RvText_Multiline_List,
            RvText_Multiline,
            RvText_PromptStyler,
            RvText_ReadPromptFiles,
            RvText_ReplaceString,
            RvText_ReplaceStringV2,
            RvText_ReplaceStringV3,
            RvText_SavePrompt,
            RvText_SmartPrompt_All,
            RvText_SmartPrompt_v2,
            RvText_WildcardProcessor,
            # Tools
            RvTools_FastMuter,
            RvTools_FastBypasser,
            RvRouter_IfElse_Fallback,
            RvTools_FastModeToggle,
            RvTools_FastModeSwitcher,
            RvTools_FastGroupsMuter,
            RvTools_FastGroupsBypasser,
            RvTools_NodeModeRepeater,
            RvTools_ModeRelay,
            RvTools_ModeBridge,
            RvTools_ModeBridgeSet,
            RvTools_ModeBridgeGet,
            RvTools_NodeCollector,
            RvVideo_LoopCalc,
            RvAudio_LoopCalc,
            RvVideo_LoopKeepCalc,
            RvAudio_LoopAlignSilence,
            RvVideo_TrimToShortest,
            RvTools_LoraStack,
            RvTools_LoraStack_Apply,
            RvTools_RAMCleanup,
            RvTools_ResolutionScale,
            RvTools_ShowAny,
            RvTools_ShowText,
            RvTools_Stop,
            RvTools_VideoClips_Combine,
            RvTools_VideoClips_SeamlessJoin,
            RvImage_FrameConsistency,
            RvTools_VRAMCleanUp,
            RvTools_BlockSwap,
        ]

        if _sml_available:
            node_list.extend([RvLoader_SmartModelLoader_LM, RvLoader_SmartDetection])  # type: ignore
        if _sml_legacy_available:
            node_list.extend([Legacy_SmartModelLoader_LM, Legacy_SmartDetection, Legacy_SmartLML_v2, Legacy_SmartLML_v2_Eclipse, Legacy_SmartLML_v3, Legacy_PipeOut_LM_AdvancedOptions, Legacy_PipeOut_LM_AdvancedOptions_Eclipse])  # type: ignore
        if _nunchaku_available:
            node_list.extend([RvTools_NunchakuPuLIDLoader, RvTools_NunchakuPuLIDApply])  # type: ignore

        return node_list

async def comfy_entrypoint() -> EclipseExtension:
    return EclipseExtension()