#
# Credits to LAOGOU-666: https://github.com/LAOGOU-666/Comfyui-Memory_Cleanup.git
# improved and adapted for Comfyui_Eclipse

import time
import gc
import torch #type: ignore
from server import PromptServer #type: ignore
from ..core import CATEGORY
from ..core.common import any_type as any
from ..core.logger import log
from comfy_api.latest import io #type: ignore

_LOG_PREFIX = "VRAM Cleanup"


def _validate_prompt_server():
    # Validate that PromptServer is available and accessible
    try:
        if not hasattr(PromptServer, 'instance') or PromptServer.instance is None:
            return False, "PromptServer instance not available"
        return True, None
    except Exception as e:
        return False, f"PromptServer validation error: {e}"


def _send_cleanup_signal(offload_model, offload_cache):
    # Send cleanup signal to ComfyUI frontend
    signal_data = {
        "type": "cleanup_request",
        "data": {
            "unload_models": bool(offload_model),
            "free_memory": bool(offload_cache)
        }
    }

    try:
        PromptServer.instance.send_sync("memory_cleanup", signal_data)
        return True, signal_data
    except AttributeError as e:
        return False, f"PromptServer method not available: {e}"
    except Exception as e:
        return False, f"Failed to send cleanup signal: {e}"


def _aggressive_vram_cleanup():
    # Perform aggressive VRAM cleanup using PyTorch and garbage collection.
    # Supports CUDA/ROCm, MPS (Apple Silicon), XPU (Intel Arc), NPU, and MLU.
    try:
        results = []
        any_cleaned = False

        gc.collect()

        # CUDA / ROCm (NVIDIA + AMD)
        if torch.cuda.is_available():
            initial_allocated = torch.cuda.memory_allocated() / (1024 * 1024)
            initial_reserved = torch.cuda.memory_reserved() / (1024 * 1024)

            torch.cuda.empty_cache()
            torch.cuda.synchronize()

            freed_allocated = initial_allocated - torch.cuda.memory_allocated() / (1024 * 1024)
            freed_reserved = initial_reserved - torch.cuda.memory_reserved() / (1024 * 1024)
            results.append(f"CUDA: freed {freed_allocated:.1f}MB alloc, {freed_reserved:.1f}MB reserved")
            any_cleaned = True

        # MPS (Apple Silicon)
        if hasattr(torch, 'mps') and hasattr(torch.mps, 'empty_cache'):
            try:
                torch.mps.empty_cache()
                results.append("MPS: cache cleared")
                any_cleaned = True
            except Exception:
                pass

        # XPU (Intel Arc)
        if hasattr(torch, 'xpu') and hasattr(torch.xpu, 'empty_cache'):
            try:
                torch.xpu.empty_cache()
                results.append("XPU: cache cleared")
                any_cleaned = True
            except Exception:
                pass

        # NPU (Huawei/Ascend)
        if hasattr(torch, 'npu') and hasattr(torch.npu, 'empty_cache'):
            try:
                torch.npu.empty_cache()
                results.append("NPU: cache cleared")
                any_cleaned = True
            except Exception:
                pass

        # MLU (Cambricon)
        if hasattr(torch, 'mlu') and hasattr(torch.mlu, 'empty_cache'):
            try:
                torch.mlu.empty_cache()
                results.append("MLU: cache cleared")
                any_cleaned = True
            except Exception:
                pass

        if not any_cleaned:
            return False, "No GPU backend available"

        return True, "; ".join(results)
    except Exception as e:
        return False, f"Aggressive cleanup failed: {str(e)}"


class RvTools_VRAMCleanUp(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="VRAM Cleanup [Eclipse]",
            display_name="VRAM Cleanup",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[
                io.AnyType.Input("anything"),
                io.Boolean.Input("offload_model", default=True, tooltip="Unload models from VRAM via ComfyUI"),
                io.Boolean.Input("offload_cache", default=True, tooltip="Clear VRAM cache via ComfyUI"),
                io.Boolean.Input("aggressive_cleanup", default=False, tooltip="Force PyTorch GPU cache clear and garbage collection. Supports CUDA, ROCm, MPS, XPU (may cause brief lag)"),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
            outputs=[
                io.AnyType.Output("output"),
            ],
        )

    @classmethod
    def execute(cls, anything, offload_model, offload_cache, aggressive_cleanup) -> io.NodeOutput:
        # Send VRAM cleanup signal to ComfyUI frontend with validation and feedback
        start_time = time.time()

        try:
            # Validate inputs
            if not isinstance(offload_model, bool):
                offload_model = bool(offload_model)
            if not isinstance(offload_cache, bool):
                offload_cache = bool(offload_cache)
            if not isinstance(aggressive_cleanup, bool):
                aggressive_cleanup = bool(aggressive_cleanup)

            # Build status message
            operations = []
            if offload_model:
                operations.append("Offload Models")
            if offload_cache:
                operations.append("Clear Cache")
            if aggressive_cleanup:
                operations.append("Aggressive Cleanup")
            
            if not operations:
                log.msg(_LOG_PREFIX, "=== VRAM Cleanup Skipped ===")
                log.msg(_LOG_PREFIX, "No cleanup operations selected")
                return io.NodeOutput(anything)

            # Start message
            log.msg(_LOG_PREFIX, "=== VRAM Cleanup Started ===")
            log.msg(_LOG_PREFIX, f"Operations: {', '.join(operations)}")

            status_messages = []
            
            # Standard cleanup via PromptServer
            if offload_model or offload_cache:
                server_ok, server_error = _validate_prompt_server()
                if not server_ok:
                    status_messages.append(f"ComfyUI Signal: Failed - {server_error}")
                else:
                    signal_sent, signal_result = _send_cleanup_signal(offload_model, offload_cache)
                    if signal_sent:
                        time.sleep(0.5)  # Brief pause for frontend processing
                        status_messages.append("ComfyUI Signal: Success")
                    else:
                        status_messages.append(f"ComfyUI Signal: Failed - {signal_result}")
            
            # Aggressive cleanup
            if aggressive_cleanup:
                aggressive_ok, aggressive_msg = _aggressive_vram_cleanup()
                if aggressive_ok:
                    status_messages.append(f"Aggressive: {aggressive_msg}")
                else:
                    status_messages.append(f"Aggressive: {aggressive_msg}")
            
            elapsed = time.time() - start_time
            
            # Consolidated output
            log.msg(_LOG_PREFIX, f"Status: {', '.join(status_messages)}")
            log.msg(_LOG_PREFIX, f"Time: {elapsed:.2f}s")
            log.msg(_LOG_PREFIX, "=== VRAM Cleanup Complete ===")

        except Exception as e:
            elapsed = time.time() - start_time
            log.error(_LOG_PREFIX, f"Status: Error - {str(e)}")
            log.msg(_LOG_PREFIX, f"Time: {elapsed:.2f}s")
            log.msg(_LOG_PREFIX, "=== VRAM Cleanup Complete ===")

        return io.NodeOutput(anything)
