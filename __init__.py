# ComfyUI_Eclipse Extension Loader
# Initializes and loads all custom nodes for ComfyUI_Eclipse using the ComfyUI V3 extension API.
WEB_DIRECTORY = "./js"

import sys

# Prevent shadowing of ComfyUI's top-level utils package by comfy/utils.py when nodes.py is imported first.
if "utils" not in sys.modules:
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
    _sml_active = (_custom_nodes / "comfyui_smartlml" / "__init__.py").exists() or (
        _custom_nodes / "ComfyUI_SmartLML" / "__init__.py"
    ).exists()
    if _sml_active:
        log.warning("", "⚠ Standalone ComfyUI_SmartLML is still active!")
        log.warning("", "  SmartLML is now included in Eclipse.")
        log.warning(
            "",
            "  Please rename or remove the comfyui_smartlml folder to avoid conflicts.",
        )
        log.warning("", "  e.g.: mv comfyui_smartlml comfyui_smartlml.disabled")
except Exception:
    pass

# Initialize server endpoints
try:
    from .core.server_endpoints import initialize_endpoints

    initialize_endpoints()
except Exception as e:
    log.warning("", f"Failed to initialize server endpoints: {e}")

# Initialize the standalone Download Manager endpoints and persistent queue.
try:
    from .core.download_manager import (
        initialize_endpoints as initialize_download_manager,
    )

    initialize_download_manager()
except (AttributeError, ImportError, OSError, RuntimeError, ValueError) as e:
    log.warning("Download Manager", f"Failed to initialize endpoints: {e}")

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

    if (
        not florence2_wrapper.FLORENCE2_CUSTOM_AVAILABLE
        and florence2_wrapper.transformers_version < (5, 0)
    ):
        log.msg(
            "Florence-2",
            "Tip: Install comfyui-florence2 extension for better compatibility",
        )
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
                "none": "None detected (CPU mode)",
            }
            log.msg("Docker", f"GPU vendor: {vendor_map.get(gpu_vendor, gpu_vendor)}")
        except Exception:
            pass
except Exception:
    pass

# SML server endpoints
try:
    from .core.sml.server_endpoints import (
        initialize_endpoints as sml_initialize_endpoints,
    )

    sml_initialize_endpoints()
except Exception as e:
    log.warning("SML", f"Failed to initialize SML server endpoints: {e}")

# V3 Extension Registration
from comfy_api.latest import ComfyExtension, io  # type: ignore


class EclipseExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        # Conversion nodes
        from .py.RvConversion_ConcatMulti import RvConversion_ConcatMulti
        from .py.RvConversion_ConvertPrimitive import RvConversion_ConvertPrimitive
        from .py._legacy.legacy_ConvertToBatch import legacy_ConvertToBatch
        from .py.RvConversion_ToBatch import RvConversion_ToBatch
        from .py.RvConversion_RIFEMultiplier import RvConversion_RIFEMultiplier
        from .py._legacy.legacy_ConvertToList import legacy_ConvertToList
        from .py.RvConversion_ToList import RvConversion_ToList
        from .py.RvConversion_DetectionToBboxes import RvConversion_DetectionToBboxes
        from .py.RvConversion_ImageConvert import RvConversion_ImageConvert
        from .py.RvConversion_IndicesToList import RvConversion_IndicesToList
        from .py.RvConversion_Join import RvConversion_Join
        from .py.RvConversion_MergeStrings import RvConversion_MergeStrings
        from .py.RvConversion_StringFromList import RvConversion_StringFromList
        from .py.RvConversion_WidgetToString import RvConversion_WidgetToString

        # Folder nodes
        from .py.RvFolder_AddFolder import RvFolder_AddFolder
        from .py.RvFolder_FilenamePrefix import RvFolder_FilenamePrefix
        from .py.RvFolder_FolderPath import RvFolder_FolderPath
        from .py.RvFolder_SmartFolder import RvFolder_SmartFolder

        # Image nodes
        from .py.RvImage_AddWatermarkImage import RvImage_AddWatermarkImage
        from .py.RvImage_Load import RvImage_Load
        from .py.RvImage_LoadPipe import RvImage_LoadPipe
        from .py.RvImage_LoadImageFromFolder import RvImage_LoadImageFromFolder
        from .py.RvImage_LoadImageFromFolder_Pipe import (
            RvImage_LoadImageFromFolder_Pipe,
        )
        from .py.RvImage_LoadBatchFromFolder import RvImage_LoadBatchFromFolder
        from .py._legacy.legacy_LoadBatchFromFolderAdvanced import (
            legacy_LoadBatchFromFolderAdvanced,
        )
        from .py.RvImage_LoadBatchFromFolderStepAdvanced import (
            RvImage_LoadBatchFromFolderStepAdvanced,
        )
        from .py.RvImage_Preview import RvImage_Preview
        from .py.RvImage_PreviewDom import RvImage_PreviewDom
        from .py.RvImage_PreviewDom_Stop import RvImage_PreviewDom_Stop
        from .py.RvMask_Preview import RvMask_Preview
        from .py.RvImage_Selector import RvImage_Selector
        from .py.RvImage_Comparer import RvImage_Comparer
        from .py.RvImage_ColorMatch import RvImage_ColorMatch
        from .py.RvImage_CropByMask import RvImage_CropByMask
        from .py.RvImage_Resize import RvImage_Resize
        from .py.RvImage_Soften import RvImage_Soften
        from .py.RvImage_Save import RvImage_Save
        from .py.RvImage_Preview_SEGS import RvImage_Preview_SEGS
        from .py.RvImage_Preview_SEGS_Simple import RvImage_Preview_SEGS_Simple
        from .py.RvMask_ToSEGS import RvMask_ToSEGS
        from .py.RvSampler_DetailerForEach import RvSampler_DetailerForEach
        from .py.RvImage_TileAssembly import RvImage_TileAssembly
        from .py.RvImage_TileDecodeAssembly import RvImage_TileDecodeAssembly
        from .py.RvImage_TileSplit import RvImage_TileSplit
        from .py.RvImage_TextImageWithFX import RvImage_TextImageWithFX
        from .py.RvImage_ImageWithFX import RvImage_ImageWithFX
        from .py.RvImage_AlignSize import RvImage_AlignSize
        from .py.RvImage_GetLast import RvImage_GetLast
        from .py.RvImage_GetFirst import RvImage_GetFirst
        from .py.RvImage_BatchSlice import RvImage_BatchSlice
        from .py.RvImage_BatchInterleave import RvImage_BatchInterleave
        from .py.RvImage_BatchStrip import RvImage_BatchStrip
        from .py.RvImage_BatchExtendWithOverlap import RvImage_BatchExtendWithOverlap
        from .py.RvImage_BatchExtendWithRife import RvImage_BatchExtendWithRife
        from .py.RvImage_InsetCrop import RvImage_InsetCrop
        from .py.RvImage_FilterAdjustments import RvImage_FilterAdjustments
        from .py.RvImage_FilterAdjustmentsAdvanced import (
            RvImage_FilterAdjustmentsAdvanced,
        )
        from .py.RvImage_Rescale import RvImage_Rescale
        from .py.RvImage_UpscaleWithModel import RvImage_UpscaleWithModel
        from .py.RvImage_UpscaleWithModel_v2 import RvImage_UpscaleWithModel_v2

        # Loader nodes
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
            from .py.RvLoader_SmartDetection import (
                RvLoader_Detection as RvLoader_SmartDetection,
            )

            _sml_available = True
        except Exception as e:
            log.warning("SML", f"Smart LML nodes unavailable: {e}")
            _sml_available = False
        # Logic nodes
        from .py.RvLogic_Boolean import RvLogic_Boolean
        from .py.RvLogic_Float import RvLogic_Float
        from .py.RvLogic_Integer import RvLogic_Integer
        from .py.RvLogic_Integer_Gen import RvLogic_IntegerGen
        from .py.RvLogic_None import RvLogic_None
        from .py.RvLogic_String import RvLogic_String
        from .py.RvLogic_Seed import RvLogic_Seed

        # Sampler nodes
        from .py.RvSampler_KSamplerPipe import RvSampler_KSamplerPipe
        from .py.RvSampler_KSamplerKargim import RvSampler_KSamplerKargim

        # Pipe IO nodes
        from .py.RvPipe_IO_12CH_Any import RvPipe_IO_12CH_Any
        from .py.RvPipe_IO_24CH_Any import RvPipe_IO_24CH_Any
        from .py.RvPipe_IO_36CH_Any import RvPipe_IO_36CH_Any
        from .py.RvPipe_IO_Context_Image import RvPipe_IO_Context_Image
        from .py.RvPipe_IO_Context_Video import RvPipe_IO_Context_Video
        from .py.RvPipe_IO_Context_WanVideoWrapper import (
            RvPipe_IO_Context_WanVideoWrapper,
        )
        from .py.RvPipe_IO_CheckpointLoader import RvPipe_IO_CheckpointLoader
        from .py.RvPipe_IO_LoadImage import RvPipe_IO_LoadImage
        from .py.RvPipe_IO_Generation_Data import RvPipe_IO_Generation_Data
        from .py.RvPipe_IO_Generation_Data_Gated import RvPipe_IO_Generation_Data_Gated
        from .py.RvPipe_IO_Sampler_Settings import RvPipe_IO_Sampler_Settings
        from .py.RvPipe_IO_SliceDice import RvPipe_IO_SliceDice

        # Pipe Out nodes
        from .py.RvPipe_Out_SmartFolder import RvPipe_Out_SmartFolder
        from .py.RvPipe_Out_WanVideo_Setup import RvPipe_Out_WanVideo_Setup

        # Basic Pipe nodes
        # Router nodes
        from .py.RvRouter_Any_DualSwitch import RvRouter_Any_DualSwitch
        from .py.RvRouter_Any_DualSwitch_purge import RvRouter_Any_DualSwitch_purge
        from .py.RvRouter_Any_MultiSwitch import RvRouter_Any_MultiSwitch
        from .py.RvRouter_Any_MultiSwitch_purge import RvRouter_Any_MultiSwitch_purge
        from .py.RvRouter_Any_MultiSwitch_lazy import RvRouter_Any_MultiSwitch_lazy
        from .py.RvRouter_Any_MultiSwitch_lazy_purge import (
            RvRouter_Any_MultiSwitch_lazy_purge,
        )
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

        # Settings nodes
        from .py.RvSettings_ControlNetUnionType import RvSettings_ControlNetUnionType
        from .py.RvSettings_Image_Resolution import RvSettings_Image_Resolution
        from .py.RvSettings_Image_ResolutionPipe import RvSettings_Image_ResolutionPipe
        from .py.RvSettings_Image_ResolutionSimplePipe import (
            RvSettings_Image_ResolutionSimplePipe,
        )
        from .py.RvSettings_Video_Resolution import RvSettings_Video_Resolution
        from .py.RvSettings_SmartSamplerSettings import RvSettings_SmartSamplerSettings
        from .py.RvSettings_WanVideo_Setup import RvSettings_WanVideo_Setup

        # Text nodes
        from .py.RvCond_CLIPTextEncode import RvCond_CLIPTextEncode
        from .py.RvCond_CLIPTextEncodeAdvanced import RvCond_CLIPTextEncodeAdvanced
        from .py.RvCond_ConditioningZeroOut import RvCond_ConditioningZeroOut
        from .py.RvText_DeDuplicate import RvText_DeDuplicate
        from .py.RvText_DualText import RvText_DualText
        from .py.RvText_FilterPrompt import RvText_FilterPrompt
        from .py.RvText_Multiline import RvText_Multiline
        from .py.RvText_Multiline_List import RvText_Multiline_List
        from .py.RvText_PromptStyler import RvText_PromptStyler
        from .py.RvText_ReadPromptFiles import RvText_ReadPromptFiles
        from .py.RvText_ReplaceString import RvText_ReplaceString
        from .py.RvText_ReplaceString_Adv import RvText_ReplaceStringV3
        from .py.RvText_SavePrompt import RvText_SavePrompt
        from .py.RvText_SmartPrompt import RvText_SmartPrompt_All
        from .py.RvText_SmartPromptV2 import RvText_SmartPrompt_v2
        from .py.RvText_WildcardProcessor import RvText_WildcardProcessor

        # Tools nodes
        from .py.RvTools_FastModeToggle import RvTools_FastModeToggle
        from .py.RvTools_FastModeSwitcher import RvTools_FastModeSwitcher
        from .py.RvTools_NodeModeRepeater import RvTools_NodeModeRepeater
        from .py.RvTools_ModeRelay import RvTools_ModeRelay
        from .py.RvTools_ModeBridgeSet import RvTools_ModeBridgeSet
        from .py.RvTools_ModeBridgeGet import RvTools_ModeBridgeGet
        from .py.RvTools_NodeCollector import RvTools_NodeCollector
        from .py.RvVideo_LoopCalc import RvVideo_LoopCalc
        from .py.RvAudio_LoopCalc import RvAudio_LoopCalc
        from .py.RvVideo_LoopKeepCalc import RvVideo_LoopKeepCalc
        from .py.RvAudio_LoopAlignSilence import RvAudio_LoopAlignSilence
        from .py.RvVideo_WanLipSyncPlanStep import RvVideo_WanLipSyncPlanStep
        from .py.RvVideo_WanLipSyncTimelinePlanner import (
            RvVideo_WanLipSyncTimelinePlanner,
        )
        from .py.RvVideo_TrimToShortest import RvVideo_TrimToShortest
        from .py.RvVideo_Preview import RvVideo_Preview
        from .py.RvVideo_Save import RvVideo_Save
        from .py.RvVideo_FrameConsistency import RvVideo_FrameConsistency
        from .py.RvTools_LoraStack import RvTools_LoraStack
        from .py.RvTools_LoraStack_Apply import RvTools_LoraStack_Apply

        try:
            from .py.RvTools_NunchakuPuLID import (
                RvTools_NunchakuPuLIDLoader,
                RvTools_NunchakuPuLIDApply,
            )

            _nunchaku_available = True
        except Exception as e:
            log.warning("NunchakuPuLID", f"Nunchaku nodes unavailable: {e}")
            _nunchaku_available = False
        from .py.RvTools_ResolutionScale import RvTools_ResolutionScale
        from .py.RvTools_ShowAny import RvTools_ShowAny
        from .py.RvTools_ShowAnyStop import RvTools_ShowAnyStop
        from .py.RvTools_ShowText import RvTools_ShowText
        from .py.RvTools_ShowText_Stop import RvTools_ShowText_Stop
        from .py.RvTools_Stop import RvTools_Stop
        from .py.RvTools_BlockSwap import RvTools_BlockSwap
        from .py.RvTools_WorkflowMigration import RvTools_WorkflowMigration

        node_list: list[type[io.ComfyNode]] = [
            # Conversion
            RvConversion_ConcatMulti,
            RvConversion_ConvertPrimitive,
            legacy_ConvertToBatch,
            RvConversion_ToBatch,
            RvConversion_RIFEMultiplier,
            legacy_ConvertToList,
            RvConversion_ToList,
            RvConversion_DetectionToBboxes,
            RvConversion_ImageConvert,
            RvConversion_IndicesToList,
            RvConversion_Join,
            RvConversion_MergeStrings,
            RvConversion_StringFromList,
            RvConversion_WidgetToString,
            # Folder
            RvFolder_AddFolder,
            RvFolder_FilenamePrefix,
            RvFolder_FolderPath,
            RvFolder_SmartFolder,
            # Image
            RvImage_AddWatermarkImage,
            RvImage_Load,
            RvImage_LoadPipe,
            RvImage_LoadImageFromFolder,
            RvImage_LoadImageFromFolder_Pipe,
            RvImage_LoadBatchFromFolder,
            legacy_LoadBatchFromFolderAdvanced,
            RvImage_LoadBatchFromFolderStepAdvanced,
            RvImage_Preview,
            RvImage_PreviewDom,
            RvImage_PreviewDom_Stop,
            RvMask_Preview,
            RvImage_Selector,
            RvImage_Comparer,
            RvImage_ColorMatch,
            RvImage_CropByMask,
            RvImage_Resize,
            RvImage_Soften,
            RvImage_Save,
            RvImage_Preview_SEGS,
            RvImage_Preview_SEGS_Simple,
            RvMask_ToSEGS,
            RvSampler_DetailerForEach,
            RvImage_TileAssembly,
            RvImage_TileDecodeAssembly,
            RvImage_TileSplit,
            RvImage_TextImageWithFX,
            RvImage_ImageWithFX,
            RvImage_AlignSize,
            RvImage_GetLast,
            RvImage_GetFirst,
            RvImage_BatchSlice,
            RvImage_BatchInterleave,
            RvImage_BatchStrip,
            RvImage_BatchExtendWithOverlap,
            RvImage_BatchExtendWithRife,
            RvImage_InsetCrop,
            RvImage_FilterAdjustments,
            RvImage_FilterAdjustmentsAdvanced,
            RvImage_Rescale,
            RvImage_UpscaleWithModel,
            RvImage_UpscaleWithModel_v2,
            # Loader
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
            RvPipe_IO_LoadImage,
            RvPipe_IO_Generation_Data,
            RvPipe_IO_Generation_Data_Gated,
            RvPipe_IO_Sampler_Settings,
            RvPipe_IO_SliceDice,
            # Pipe Out
            RvPipe_Out_SmartFolder,
            RvPipe_Out_WanVideo_Setup,
            # Basic Pipe
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
            RvSettings_Image_Resolution,
            RvSettings_Image_ResolutionPipe,
            RvSettings_Image_ResolutionSimplePipe,
            RvSettings_Video_Resolution,
            RvSettings_SmartSamplerSettings,
            RvSettings_WanVideo_Setup,
            # Text
            RvCond_CLIPTextEncode,
            RvCond_CLIPTextEncodeAdvanced,
            RvCond_ConditioningZeroOut,
            RvText_DeDuplicate,
            RvText_DualText,
            RvText_FilterPrompt,
            RvText_Multiline_List,
            RvText_Multiline,
            RvText_PromptStyler,
            RvText_ReadPromptFiles,
            RvText_ReplaceString,
            RvText_ReplaceStringV3,
            RvText_SavePrompt,
            RvText_SmartPrompt_All,
            RvText_SmartPrompt_v2,
            RvText_WildcardProcessor,
            # Tools
            RvTools_FastModeToggle,
            RvTools_FastModeSwitcher,
            RvTools_NodeModeRepeater,
            RvTools_ModeRelay,
            RvTools_ModeBridgeSet,
            RvTools_ModeBridgeGet,
            RvTools_NodeCollector,
            RvVideo_LoopCalc,
            RvAudio_LoopCalc,
            RvVideo_LoopKeepCalc,
            RvAudio_LoopAlignSilence,
            RvVideo_WanLipSyncTimelinePlanner,
            RvVideo_WanLipSyncPlanStep,
            RvVideo_TrimToShortest,
            RvVideo_Preview,
            RvVideo_Save,
            RvVideo_FrameConsistency,
            RvTools_LoraStack,
            RvTools_LoraStack_Apply,
            RvTools_ResolutionScale,
            RvTools_ShowAny,
            RvTools_ShowAnyStop,
            RvTools_ShowText,
            RvTools_ShowText_Stop,
            RvTools_Stop,
            RvTools_BlockSwap,
            RvTools_WorkflowMigration,
        ]

        if _sml_available:
            node_list.extend([RvLoader_SmartModelLoader_LM, RvLoader_SmartDetection])  # type: ignore
        if _nunchaku_available:
            node_list.extend([RvTools_NunchakuPuLIDLoader, RvTools_NunchakuPuLIDApply])  # type: ignore

        return node_list


async def comfy_entrypoint() -> EclipseExtension:
    return EclipseExtension()
