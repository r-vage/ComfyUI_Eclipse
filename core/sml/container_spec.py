# Canonical Docker container specifications and fail-closed reuse inspection.

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTAINER_SPEC_SCHEMA_VERSION = 3
CONTAINER_SPEC_VERSION_LABEL = "comfyui.eclipse.sml.container-spec-version"
CONTAINER_SPEC_BACKEND_LABEL = "comfyui.eclipse.sml.backend"
CONTAINER_SPEC_FINGERPRINT_LABEL = (
    "comfyui.eclipse.sml.container-spec-sha256"
)
CONTAINER_MAPPING_SPEC_VERSION_KEY = "spec_schema_version"
CONTAINER_MAPPING_FINGERPRINT_KEY = "spec_fingerprint"
DEFAULT_CONTAINER_SECURITY_OPTIONS = ("no-new-privileges=true",)
DEFAULT_PRIVATE_SHM_SIZE = "16g"


@dataclass(frozen=True)
class ContainerHardeningProfile:
    capability_drops: tuple[str, ...] = ()
    read_only_rootfs: bool = False
    tmpfs_mounts: tuple[str, ...] = ()


def qualified_container_hardening(
    backend: str,
    gpu_vendor: str,
) -> ContainerHardeningProfile:
    # Capability dropping and a read-only root are qualified on NVIDIA/Linux.
    # ROCm vendor recipes require additional capabilities and remain excluded.
    if gpu_vendor != "nvidia":
        return ContainerHardeningProfile()
    cache_tmpfs = (
        "/tmp:rw,nosuid,nodev,exec,size=4g",
        "/root/.cache:rw,nosuid,nodev,exec,size=8g",
        "/root/.triton:rw,nosuid,nodev,exec,size=4g",
    )
    if backend in {"vllm", "sglang"}:
        tmpfs_mounts = cache_tmpfs
    else:
        tmpfs_mounts = ("/tmp:rw,nosuid,nodev,noexec,size=1g",)
    return ContainerHardeningProfile(
        capability_drops=("ALL",),
        read_only_rootfs=True,
        tmpfs_mounts=tmpfs_mounts,
    )

DockerCommandRunner = Callable[..., tuple[bool, str]]


def canonical_path_identity(path: str | os.PathLike[str]) -> str:
    # Resolve a host path consistently and respect case-insensitive platforms.
    resolved = Path(path).expanduser().resolve(strict=False)
    normalized = os.path.normcase(str(resolved))
    return Path(normalized).as_posix()


def stable_model_key(backend: str, model_identity: str) -> str:
    # Mapping keys must distinguish backends and full canonical model identities.
    if not backend or not model_identity:
        raise ValueError("Backend and model identity must not be empty")
    identity = f"{backend}\0{model_identity}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ContainerMount:
    source: str
    target: str
    read_only: bool

    def __post_init__(self) -> None:
        if not self.source or not self.target:
            raise ValueError("Container mount source and target must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "read_only": self.read_only,
            "source": self.source,
            "target": self.target,
        }

    @property
    def docker_volume_argument(self) -> str:
        suffix = ":ro" if self.read_only else ""
        return f"{self.source}:{self.target}{suffix}"


def _unique_pairs(
    pairs: tuple[tuple[str, Any], ...],
    field_name: str,
) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or not key:
            raise ValueError(f"{field_name} keys must be non-empty strings")
        if key in result:
            raise ValueError(f"Duplicate {field_name} key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class ContainerSpec:
    backend: str
    image_reference: str
    image_id: str
    bind_host: str
    host_port: int
    container_port: int
    mounts: tuple[ContainerMount, ...] = ()
    gpu_arguments: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    security_options: tuple[str, ...] = ()
    capability_drops: tuple[str, ...] = ()
    read_only_rootfs: bool = False
    tmpfs_mounts: tuple[str, ...] = ()
    ipc_mode: str = "private"
    shm_size: str = ""
    model_identity: str = ""
    settings: tuple[tuple[str, Any], ...] = ()
    schema_version: int = CONTAINER_SPEC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.backend:
            raise ValueError("Container backend must not be empty")
        if not self.image_reference or not self.image_id:
            raise ValueError("Container image reference and image ID are required")
        if not self.bind_host:
            raise ValueError("Container bind host must not be empty")
        for port in (self.host_port, self.container_port):
            if isinstance(port, bool) or not isinstance(port, int):
                raise TypeError("Container ports must be integers")
            if not 1 <= port <= 65535:
                raise ValueError("Container ports must be between 1 and 65535")
        if self.schema_version < 1:
            raise ValueError("Container specification version must be positive")

        object.__setattr__(self, "mounts", tuple(self.mounts))
        object.__setattr__(self, "gpu_arguments", tuple(self.gpu_arguments))
        object.__setattr__(self, "environment", tuple(self.environment))
        object.__setattr__(self, "security_options", tuple(self.security_options))
        object.__setattr__(self, "capability_drops", tuple(self.capability_drops))
        object.__setattr__(self, "tmpfs_mounts", tuple(self.tmpfs_mounts))
        object.__setattr__(self, "settings", tuple(self.settings))

        if any(not isinstance(arg, str) for arg in self.gpu_arguments):
            raise TypeError("Container GPU arguments must be strings")
        environment = _unique_pairs(self.environment, "environment")
        if any(not isinstance(value, str) for value in environment.values()):
            raise TypeError("Container environment values must be strings")
        if any(
            not isinstance(option, str) or not option
            for option in self.security_options
        ):
            raise TypeError("Container security options must be non-empty strings")
        if len(set(self.security_options)) != len(self.security_options):
            raise ValueError("Container security options must be unique")
        if any(
            not isinstance(capability, str) or not capability
            for capability in self.capability_drops
        ):
            raise TypeError("Container capability drops must be non-empty strings")
        if len(set(self.capability_drops)) != len(self.capability_drops):
            raise ValueError("Container capability drops must be unique")
        if not isinstance(self.read_only_rootfs, bool):
            raise TypeError("Container read-only-root setting must be boolean")
        if any(
            not isinstance(mount, str)
            or not re.fullmatch(r"/[A-Za-z0-9._/-]+:[A-Za-z0-9,=_-]+", mount)
            for mount in self.tmpfs_mounts
        ):
            raise ValueError("Container tmpfs mount specification is invalid")
        if len(set(self.tmpfs_mounts)) != len(self.tmpfs_mounts):
            raise ValueError("Container tmpfs mounts must be unique")
        if self.ipc_mode not in {"private", "host"}:
            raise ValueError("Container IPC mode must be 'private' or 'host'")
        if self.shm_size and not re.fullmatch(
            r"[1-9][0-9]*[bBkKmMgG]?",
            self.shm_size,
        ):
            raise ValueError("Container shared-memory size is invalid")
        if self.ipc_mode == "host" and self.shm_size:
            raise ValueError(
                "A private shared-memory size cannot be combined with host IPC"
            )
        settings = _unique_pairs(self.settings, "setting")
        try:
            json.dumps(settings, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise TypeError("Container settings must be finite JSON values") from error

    @property
    def canonical_json(self) -> str:
        mounts = sorted(
            (mount.to_dict() for mount in self.mounts),
            key=lambda mount: (
                mount["source"],
                mount["target"],
                mount["read_only"],
            ),
        )
        payload = {
            "backend": self.backend,
            "bind_host": self.bind_host,
            "container_port": self.container_port,
            "environment": _unique_pairs(self.environment, "environment"),
            "gpu_arguments": list(self.gpu_arguments),
            "host_port": self.host_port,
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "ipc_mode": self.ipc_mode,
            "model_identity": self.model_identity,
            "mounts": mounts,
            "schema_version": self.schema_version,
            "security_options": sorted(self.security_options),
            "capability_drops": sorted(self.capability_drops),
            "read_only_rootfs": self.read_only_rootfs,
            "tmpfs_mounts": sorted(self.tmpfs_mounts),
            "settings": _unique_pairs(self.settings, "setting"),
            "shm_size": self.shm_size,
        }
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()

    @property
    def docker_labels(self) -> dict[str, str]:
        return {
            CONTAINER_SPEC_VERSION_LABEL: str(self.schema_version),
            CONTAINER_SPEC_BACKEND_LABEL: self.backend,
            CONTAINER_SPEC_FINGERPRINT_LABEL: self.fingerprint,
        }

    @property
    def docker_isolation_arguments(self) -> tuple[str, ...]:
        arguments = []
        for option in sorted(self.security_options):
            arguments.extend(["--security-opt", option])
        for capability in sorted(self.capability_drops):
            arguments.extend(["--cap-drop", capability])
        if self.read_only_rootfs:
            arguments.append("--read-only")
        for mount in sorted(self.tmpfs_mounts):
            arguments.extend(["--tmpfs", mount])
        if self.ipc_mode != "private":
            arguments.extend(["--ipc", self.ipc_mode])
        if self.shm_size:
            arguments.extend(["--shm-size", self.shm_size])
        return tuple(arguments)


@dataclass(frozen=True)
class ContainerReuseCheck:
    reusable: bool
    reason: str


def container_mapping_spec_fields(spec: ContainerSpec) -> dict[str, Any]:
    return {
        CONTAINER_MAPPING_SPEC_VERSION_KEY: spec.schema_version,
        CONTAINER_MAPPING_FINGERPRINT_KEY: spec.fingerprint,
    }


def mapping_matches_container_spec(mapping: Any, spec: ContainerSpec) -> bool:
    if not isinstance(mapping, dict):
        return False
    return (
        mapping.get(CONTAINER_MAPPING_SPEC_VERSION_KEY) == spec.schema_version
        and mapping.get(CONTAINER_MAPPING_FINGERPRINT_KEY) == spec.fingerprint
    )


def inspect_container_reuse(
    container_name: str,
    expected_spec: ContainerSpec,
    run_docker_cmd: DockerCommandRunner,
) -> ContainerReuseCheck:
    # Docker labels and image identity are authoritative for reuse.
    labels_success, labels_output = run_docker_cmd(
        [
            "inspect",
            container_name,
            "--format",
            "{{json .Config.Labels}}",
        ],
        timeout=10,
    )
    if not labels_success:
        return ContainerReuseCheck(False, "label_inspect_failed")

    try:
        labels = json.loads(labels_output)
    except (TypeError, json.JSONDecodeError):
        return ContainerReuseCheck(False, "malformed_labels")
    if not isinstance(labels, dict):
        return ContainerReuseCheck(False, "malformed_labels")

    version = labels.get(CONTAINER_SPEC_VERSION_LABEL)
    if version is None:
        return ContainerReuseCheck(False, "legacy_or_unlabeled")
    if version != str(expected_spec.schema_version):
        return ContainerReuseCheck(False, "schema_mismatch")
    if labels.get(CONTAINER_SPEC_BACKEND_LABEL) != expected_spec.backend:
        return ContainerReuseCheck(False, "backend_mismatch")
    if (
        labels.get(CONTAINER_SPEC_FINGERPRINT_LABEL)
        != expected_spec.fingerprint
    ):
        return ContainerReuseCheck(False, "fingerprint_mismatch")

    image_success, image_output = run_docker_cmd(
        ["inspect", container_name, "--format", "{{.Image}}"],
        timeout=10,
    )
    if not image_success:
        return ContainerReuseCheck(False, "image_inspect_failed")
    if image_output.strip() != expected_spec.image_id:
        return ContainerReuseCheck(False, "image_mismatch")

    return ContainerReuseCheck(True, "exact_match")
