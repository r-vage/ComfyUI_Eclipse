# Vendored from ComfyUI-GGUF by City96
# License: Apache-2.0 (apache.org/licenses/LICENSE-2.0)
# Source: https://github.com/city96/ComfyUI-GGUF
#
# This is a frozen copy to prevent breakage from upstream updates.
# The `gguf` pip package is still required at runtime.

from .ops import GGMLOps, GGMLTensor, GGMLLayer, move_patch_to_device
from .loader import gguf_sd_loader, gguf_clip_loader
from .nodes import GGUFModelPatcher
from .dequant import is_quantized, is_torch_compatible, dequantize_tensor
