import re
import threading
from collections.abc import Mapping
from types import MappingProxyType

from .logger import log

_LOG_PREFIX = "Docker Image Policy"
_TAG_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


RELEASE_DOCKER_IMAGES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "ollama": MappingProxyType(
            {
                "nvidia": "ollama/ollama:0.20.2@sha256:0455f166da85b1d07f694c33ba09278ca649603c0611ba8e46272b16eed7fccd",
                "amd": "ollama/ollama:0.20.2-rocm@sha256:d90fa63ebb73e34203ce169f55ec78ef3a47538a03cefb951df14bcdedb8c85f",
            }
        ),
        "vllm": MappingProxyType(
            {
                "nvidia": "vllm/vllm-openai:v0.15.1@sha256:8c9aaddfa6011b9651d06834d2fb90bdb9ab6ced4b420ec76925024eb12b22d0",
                "amd": "vllm/vllm-openai-rocm:v0.15.1@sha256:4c7fbd92fe07e4dab956d283b5d61b971f6242516647df6af06fdcbc34fddc2c",
            }
        ),
        "sglang": MappingProxyType(
            {
                "nvidia": "lmsysorg/sglang:v0.5.9@sha256:e216b7dc4ac1938b599b982233ccf7eb2b11dd1f07fc2e00a7b9841052c553be",
                "amd": "lmsysorg/sglang:v0.5.9-rocm720-mi30x@sha256:a7147071bf3cdd7e2fa8565f376a6bf01b89589aaca3767e8ed3927106e70e0b",
            }
        ),
        "llamacpp": MappingProxyType(
            {
                "nvidia": "ghcr.io/ggml-org/llama.cpp:server-cuda-b8067@sha256:e2c4612f86f6c24408f87f2743fe33063d343c7e9f523ce24a9a60ee401fde05",
                "amd": "ghcr.io/ggml-org/llama.cpp:server-b8067@sha256:76a06f8e186e8aaab15f1c0447b1d3eb8f10647ee468b901fe016fe03229aa1b",
            }
        ),
    }
)


_LEGACY_RELEASE_ALIASES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "ollama": frozenset(
            {
                "ollama/ollama",
                "ollama/ollama:latest",
                "ollama/ollama:0.20.2",
                "ollama/ollama:rocm",
                "ollama/ollama:0.20.2-rocm",
            }
        ),
        "vllm": frozenset(
            {
                "vllm/vllm-openai",
                "vllm/vllm-openai:latest",
                "vllm/vllm-openai:v0.15.1",
                "vllm/vllm-openai-rocm:v0.15.1",
                "rocm/vllm",
                "rocm/vllm:latest",
                "rocm/vllm:rocm7.0.0_vllm_0.11.2_20251210",
            }
        ),
        "sglang": frozenset(
            {
                "lmsysorg/sglang",
                "lmsysorg/sglang:latest",
                "lmsysorg/sglang:v0.5.9",
                "lmsysorg/sglang:v0.5.9-rocm720-mi30x",
            }
        ),
        "llamacpp": frozenset(
            {
                "ghcr.io/ggml-org/llama.cpp:server-cuda",
                "ghcr.io/ggml-org/llama.cpp:server-cuda-b8067",
                "ghcr.io/ggml-org/llama.cpp:server",
                "ghcr.io/ggml-org/llama.cpp:server-b8067",
            }
        ),
    }
)


_warning_lock = threading.Lock()
_warned_development_images: set[tuple[str, str]] = set()


def is_tag_digest_reference(image_reference: str) -> bool:
    # Require both a human-readable tag and an immutable SHA-256 digest.
    if not isinstance(image_reference, str):
        return False
    digest_match = _TAG_DIGEST_RE.search(image_reference)
    if digest_match is None:
        return False
    name_with_tag = image_reference[: digest_match.start()]
    final_component = name_with_tag.rsplit("/", 1)[-1]
    return ":" in final_component


def resolve_managed_docker_image(
    backend: str,
    configured_image: str,
    gpu_vendor: str,
    *,
    allow_unpinned: object = False,
) -> str:
    # Resolve stock aliases to release pins and fail closed for mutable overrides.
    if backend not in RELEASE_DOCKER_IMAGES:
        raise ValueError(f"Unsupported managed Docker backend: {backend!r}")
    if gpu_vendor not in {"amd", "nvidia", "none"}:
        raise ValueError(f"Unsupported Docker GPU vendor: {gpu_vendor!r}")
    if not isinstance(configured_image, str) or not configured_image:
        raise ValueError(f"Invalid Docker image reference: {configured_image!r}")

    release_vendor = "amd" if gpu_vendor == "amd" else "nvidia"
    release_image = RELEASE_DOCKER_IMAGES[backend][release_vendor]
    if configured_image in _LEGACY_RELEASE_ALIASES[backend]:
        log.debug(
            _LOG_PREFIX,
            f"Resolved legacy {backend} image alias to release pin: {release_image}",
        )
        return release_image
    if is_tag_digest_reference(configured_image):
        return configured_image
    if allow_unpinned is not True:
        raise RuntimeError(
            f"Mutable Docker image '{configured_image}' is blocked for managed "
            f"backend '{backend}'. Configure a tag-plus-digest reference, or set "
            "allow_unpinned_docker_images=true only for intentional development use."
        )

    warning_key = (backend, configured_image)
    with _warning_lock:
        should_warn = warning_key not in _warned_development_images
        _warned_development_images.add(warning_key)
    if should_warn:
        log.warning(
            _LOG_PREFIX,
            "Development override permits mutable Docker image "
            f"'{configured_image}' for {backend}; reproducibility is disabled.",
        )
    return configured_image
