# Validated request/result contract shared by diffusion model-loader adapters.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .lifecycle import loader_execution
from .validation import ResolvedModelFile, validate_loader_request


@dataclass(frozen=True)
class LoadRequest:
    values: Mapping[str, Any]
    features: tuple[str, ...]
    files: Mapping[str, ResolvedModelFile]
    smart: bool = False

    @classmethod
    def from_kwargs(cls, kwargs: Mapping[str, Any], *, smart: bool = False):
        features, files = validate_loader_request(kwargs, smart=smart)
        return cls(
            values=MappingProxyType(dict(kwargs)),
            features=features,
            files=MappingProxyType(files),
            smart=smart,
        )


@dataclass(frozen=True)
class LoadResult:
    model: Any
    clip: Any
    vae: Any
    audio_vae: Any
    model_name: str
    lora_names: str
    pipe: dict[str, Any] | None = None


def load_request(request: LoadRequest, log_prefix: str) -> LoadResult:
    if request.smart:
        raise ValueError("Smart loader requests use execute_smart_request")

    # Import lazily so compatibility imports cannot form an initialization cycle.
    from ..model_loader_common import load_model

    # Revalidate directly before the backend opens files. This narrows the
    # selection-to-load race and catches replacement by a symlink or bad format.
    validate_loader_request(request.values, smart=False)
    with loader_execution():
        model, clip, vae, audio_vae, model_name, lora_names = load_model(
            log_prefix,
            **request.values,
        )
    return LoadResult(model, clip, vae, audio_vae, model_name, lora_names)
