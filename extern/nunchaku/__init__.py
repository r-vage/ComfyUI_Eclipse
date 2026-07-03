# Vendored from ComfyUI-nunchaku by MIT-HAN-LAB
# License: Apache-2.0 (apache.org/licenses/LICENSE-2.0)
# Source: https://github.com/mit-han-lab/ComfyUI-nunchaku
#
# This is a frozen copy of the ComfyUI glue code to prevent breakage
# from upstream updates. The compiled `nunchaku` pip package is still
# required at runtime for the actual quantized model operations.

from .wrappers.flux import ComfyFluxWrapper, copy_with_ctx
from .model_configs.qwenimage import NunchakuQwenImage as QwenConfig
from .model_base.qwenimage import NunchakuQwenImage as QwenModelBase
from .model_patcher.common import NunchakuModelPatcher
from .model_patcher.zimage import ZImageModelPatcher
from .model_configs.zimage import NunchakuZImage as ZImageConfig
from .models.zimage import patch_model as patch_zimage_model
