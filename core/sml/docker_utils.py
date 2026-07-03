# Docker utilities
# Shared Docker daemon management and path helpers used by all Docker backends.

import os
import re
import time
import platform
import subprocess
from pathlib import Path
from typing import Union, Tuple, Optional

from .logger import log

# Windows-specific subprocess creation flags to avoid static analysis/AttributeErrors on Linux/macOS
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

_LOG_PREFIX = "Docker"


# ==============================================================================
# PLATFORM DETECTION
# ==============================================================================

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MACOS = platform.system() == "Darwin"


# ==============================================================================
# DOCKER AVAILABILITY CHECK
# ==============================================================================

_DOCKER_AVAILABLE = False
_DOCKER_VERSION = ""
_DOCKER_DAEMON_RUNNING = False


def _check_docker_installed() -> Tuple[bool, str]:
    # Check if Docker CLI is installed. Returns (installed, version_string).
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            timeout=5,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, ""
    except FileNotFoundError:
        return False, "Docker not installed"
    except subprocess.TimeoutExpired:
        return False, "Docker check timed out"
    except Exception as e:
        return False, str(e)


def is_docker_installed() -> bool:
    # Returns True if Docker CLI is available on the system.
    return _DOCKER_AVAILABLE


def get_docker_version() -> str:
    # Returns the Docker version string, or empty string if not installed.
    return _DOCKER_VERSION


def get_cached_daemon_status() -> bool:
    # Returns the daemon status cached at module load time.
    # Use this for startup logging in backends to avoid redundant `docker info` calls.
    # For live checks (e.g., before starting a container), use is_docker_daemon_running().
    return _DOCKER_DAEMON_RUNNING


def is_docker_daemon_running() -> bool:
    # Check if Docker daemon is actually running (not just installed).
    # NOTE: This runs `docker info` subprocess - avoid calling at module load.
    # Use get_cached_daemon_status() for startup checks instead.
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0
        )
        return result.returncode == 0
    except Exception:
        return False


def _wait_for_daemon(wait_timeout: int) -> bool:
    # Wait for Docker daemon to become responsive.
    global _DOCKER_DAEMON_RUNNING
    log.msg(_LOG_PREFIX, f"Waiting for Docker daemon to start (up to {wait_timeout}s)...")
    start_time = time.time()
    while time.time() - start_time < wait_timeout:
        if is_docker_daemon_running():
            _DOCKER_DAEMON_RUNNING = True
            log.msg(_LOG_PREFIX, "\u2713 Docker daemon started successfully")
            return True
        time.sleep(2)
    log.warning(_LOG_PREFIX, f"\u26a0 Docker daemon did not start within {wait_timeout}s")
    return False


def _start_docker_windows(wait_timeout: int) -> bool:
    # Start Docker Desktop on Windows.
    docker_paths = [
        os.path.expandvars(r"%ProgramFiles%\Docker\Docker\Docker Desktop.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Docker\Docker Desktop.exe"),
        r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
    ]
    docker_exe = None
    for path in docker_paths:
        if os.path.exists(path):
            docker_exe = path
            break
    if not docker_exe:
        log.warning(_LOG_PREFIX, "Docker Desktop executable not found")
        return False
    log.msg(_LOG_PREFIX, "Starting Docker Desktop...")
    try:
        subprocess.Popen(
            [docker_exe],
            creationflags=(CREATE_NO_WINDOW | DETACHED_PROCESS) if IS_WINDOWS else 0
        )
        return _wait_for_daemon(wait_timeout)
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to start Docker Desktop: {e}")
        return False


def _start_docker_linux(wait_timeout: int) -> bool:
    # Start Docker daemon on Linux via systemd.
    # Try rootless (no sudo) first, fall back to system docker (sudo) if needed.
    log.msg(_LOG_PREFIX, "Starting Docker daemon via systemd...")
    try:
        # Try rootless / user-level docker first (no privilege escalation)
        result = subprocess.run(
            ["systemctl", "--user", "start", "docker"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            # Fall back to system docker (requires sudo)
            log.debug(_LOG_PREFIX, "Rootless docker not available, trying system docker with sudo...")
            result = subprocess.run(
                ["sudo", "systemctl", "start", "docker"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                log.warning(_LOG_PREFIX, "Failed to start Docker via systemctl")
                return False
        return _wait_for_daemon(wait_timeout)
    except FileNotFoundError:
        log.warning(_LOG_PREFIX, "systemctl not found \u2014 cannot auto-start Docker")
        return False
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to start Docker daemon: {e}")
        return False


def _start_docker_macos(wait_timeout: int) -> bool:
    # Start Docker Desktop on macOS.
    log.msg(_LOG_PREFIX, "Starting Docker Desktop...")
    try:
        subprocess.Popen(["open", "-a", "Docker"])
        return _wait_for_daemon(wait_timeout)
    except Exception as e:
        log.error(_LOG_PREFIX, f"Failed to start Docker Desktop: {e}")
        return False


def start_docker_daemon(wait_timeout: int = 60) -> bool:
    # Start the Docker daemon on any platform (Windows, Linux, macOS).
    #
    # - Windows: Launches Docker Desktop
    # - Linux: Starts docker.service via systemd
    # - macOS: Opens Docker Desktop app
    #
    # Args:
    #     wait_timeout: Maximum seconds to wait for Docker to start
    #
    # Returns:
    #     bool: True if Docker daemon is now running
    global _DOCKER_DAEMON_RUNNING
    if is_docker_daemon_running():
        _DOCKER_DAEMON_RUNNING = True
        return True
    if IS_WINDOWS:
        return _start_docker_windows(wait_timeout)
    elif IS_LINUX:
        return _start_docker_linux(wait_timeout)
    elif IS_MACOS:
        return _start_docker_macos(wait_timeout)
    else:
        log.warning(_LOG_PREFIX, f"Auto-start not supported on {platform.system()}")
        return False


def ensure_docker_running() -> bool:
    # Ensure Docker is running. Attempt to start the daemon if not.
    #
    # Returns:
    #     bool: True if Docker is available and running
    global _DOCKER_DAEMON_RUNNING
    if not _DOCKER_AVAILABLE:
        return False
    if _DOCKER_DAEMON_RUNNING or is_docker_daemon_running():
        _DOCKER_DAEMON_RUNNING = True
        return True
    return start_docker_daemon()


# ==============================================================================
# PATH HELPERS
# ==============================================================================


def host_path_for_docker(path: Union[str, Path], resolve: bool = True) -> str:
    """Convert a host path (Windows or POSIX) to a Docker-friendly POSIX path.

    Examples:
        C:\\AI\\ComfyUI -> /c/AI/ComfyUI
        /home/user/models -> /home/user/models

    Args:
        path: Path-like object or string.
        resolve: If True, attempt to resolve the path (non-strict). If False, do not call resolve().

    Returns:
        POSIX-style path string suitable for Docker host mounts.

    Raises:
        ValueError: If UNC or unsupported path formats are provided.
    """
    if path is None:
        raise ValueError("Empty path provided")

    p_str = str(path)
    # Normalize backslashes first (handles Windows-style input on any OS)
    p_str = p_str.replace('\\', '/')

    # If this looks like a Windows absolute path (C:/...), don't resolve with Path
    m_drive = re.match(r'^([A-Za-z]):/(.*)$', p_str)
    if m_drive:
        drive = m_drive.group(1).lower()
        rest = m_drive.group(2)
        posix = f"/{drive}/{rest}" if rest else f"/{drive}"
        return posix

    p = Path(p_str)
    try:
        if resolve:
            p = p.resolve(strict=False)
    except Exception:
        # Keep original path if resolve fails
        p = Path(p_str)

    posix = p.as_posix()

    # UNC paths (//server/share) are not supported for Docker mounts on some setups
    if posix.startswith('////') or posix.startswith('//'):
        # Normalize to single leading '//' and return (caller may choose to reject)
        return posix

    return posix


def make_docker_volume(host_path: Union[str, Path], container_path: str, readonly: bool = False) -> str:
    """Build a Docker volume mount string from host and container paths.

    Args:
        host_path: Host path (str or Path)
        container_path: Container path (str)
        readonly: If True, append ':ro' to mount string

    Returns:
        Mount string like '/c/AI/ComfyUI/models:/models:ro' or '/home/user/models:/models'
    """
    host_posix = host_path_for_docker(host_path)
    mount = f"{host_posix}:{container_path}"
    if readonly:
        mount += ":ro"
    return mount


def is_container_image_stale(container_name: str, expected_image: str) -> bool:
    # Check if a stopped container was created from an older image than what's locally available.
    # Returns True if the container should be recreated (image was updated).
    try:
        # Get image ID the container was created from
        result = subprocess.run(
            ["docker", "inspect", container_name, "--format", "{{.Image}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False  # Can't inspect → not stale (let caller handle)

        container_image_id = result.stdout.strip()

        # Get current local image ID
        result = subprocess.run(
            ["docker", "image", "inspect", expected_image, "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False  # Image not found locally → not stale

        local_image_id = result.stdout.strip()

        if container_image_id != local_image_id:
            log.msg(_LOG_PREFIX,
                f"Image updated — container uses {container_image_id[:19]}, "
                f"local image is {local_image_id[:19]}. Recreating container.")
            return True
        return False

    except Exception as e:
        log.debug(_LOG_PREFIX, f"Could not check image freshness: {e}")
        return False


# ==============================================================================
# SECURITY HELPERS
# ==============================================================================

# Conservative whitelist for Docker image references. Matches:
#   - lowercase registry/org/repo path (a-z, 0-9, ., _, -, /)
#   - optional :TAG or @sha256:... digest with limited charset
# Rejects shell metacharacters, whitespace, embedded flags, etc. This is a
# defense-in-depth check — image strings come from docker_config.json which the
# user controls, but they're passed to subprocess so we still validate.
_IMAGE_REF_RE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*"          # first path segment
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"      # additional path segments
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?" # optional tag
    r"(?:@sha256:[a-f0-9]{64})?$"              # optional digest
)


def validate_docker_image(image: str) -> str:
    # Validate a Docker image reference before passing to subprocess.
    #
    # Raises ValueError on invalid input. Returns the image string unchanged on
    # success. Allowed characters cover all real-world registry/image/tag forms
    # (Docker Hub, ghcr.io, quay.io, etc.) while rejecting shell metacharacters,
    # whitespace, and leading dashes that could be misread as CLI flags.
    if not isinstance(image, str) or not image:
        raise ValueError(f"Invalid Docker image reference: {image!r}")
    if len(image) > 512:
        raise ValueError("Docker image reference is too long (max 512 chars)")
    if image.startswith("-"):
        raise ValueError(f"Docker image reference may not start with '-': {image!r}")
    if not _IMAGE_REF_RE.match(image):
        raise ValueError(f"Docker image reference contains invalid characters: {image!r}")
    return image


def get_docker_bind_host(config: Optional[dict] = None) -> str:
    # Return the host interface to bind container ports to.
    #
    # Defaults to 127.0.0.1 so SML's local model servers (vLLM, SGLang, Ollama,
    # llama.cpp) are not exposed on the LAN. Users who want LAN access can set
    # docker_bind_host: "0.0.0.0" in docker_config.json (at their own risk —
    # these are unauthenticated OpenAI-compatible APIs that can run arbitrary
    # model inference).
    if config is None:
        try:
            from .backend_vllm_docker import load_docker_config
            config = load_docker_config()
        except Exception:
            return "127.0.0.1"
    host = config.get("docker_bind_host", "127.0.0.1")
    if not isinstance(host, str) or not host:
        return "127.0.0.1"
    # Simple sanity check — allow IPv4 dotted quads, hostnames, or 0.0.0.0.
    if not re.match(r"^[A-Za-z0-9_.\-:]+$", host) or len(host) > 64:
        log.warning(_LOG_PREFIX, f"Invalid docker_bind_host {host!r}, falling back to 127.0.0.1")
        return "127.0.0.1"
    return host


# ==============================================================================
# MODULE-LEVEL INITIALIZATION
# ==============================================================================

_DOCKER_AVAILABLE, _DOCKER_VERSION = _check_docker_installed()
if _DOCKER_AVAILABLE:
    _DOCKER_DAEMON_RUNNING = is_docker_daemon_running()
    if _DOCKER_DAEMON_RUNNING:
        log.debug(_LOG_PREFIX, f"Docker available: {_DOCKER_VERSION}")
    else:
        log.debug(_LOG_PREFIX, f"Docker installed but daemon not running: {_DOCKER_VERSION}")
else:
    log.debug(_LOG_PREFIX, "Docker not installed")
