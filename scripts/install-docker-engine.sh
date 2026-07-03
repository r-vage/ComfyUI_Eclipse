#!/usr/bin/env bash
# install-docker-engine.sh
# Interactive installer for Docker Engine + GPU support (NVIDIA/AMD) + pass setup
# Auto-detects GPU vendor and installs the appropriate toolkit
# Designed to be cross-distro: detects package manager and runs the correct commands
# Usage: sudo ./install-docker-engine.sh [--all] [--yes] [--dry-run]

set -euo pipefail

DRY_RUN=0
ASSUME_YES=0
RUN_ALL=0
PM=unknown

# Behaviour: pause and prompt on errors when running interactively.
# When ASSUME_YES=1 (non-interactive) the script will exit on error.
PAUSE_ON_ERROR=1
set -o errtrace -o functrace

on_error() {
  local exit_code="$1"
  local line="$2"
  local cmd="${BASH_COMMAND:-<unknown>}"

  # Disable errexit while we handle the prompt
  set +e

  # Print a clear error message
  echo
  echo "========================================"
  printf '\e[1;31mERROR:\e[0m Command exited with code %s at line %s\n' "$exit_code" "$line"
  echo "Failed command: $cmd"
  echo "========================================"

  # If non-interactive or pause disabled, exit immediately
  if [ "${ASSUME_YES:-0}" -eq 1 ] || [ "${PAUSE_ON_ERROR:-1}" -eq 0 ]; then
    echo "Non-interactive or pause disabled — exiting with code $exit_code."
    exit "$exit_code"
  fi

  # Interactive prompt: continue, abort, or drop to a shell
  while true; do
    read -r -p "Choose action — (c)ontinue, (a)bort, (s)hell: " ans </dev/tty || true
    case "${ans,,}" in
      c|continue)
        echo "Continuing after error..."
        break
        ;;
      a|abort)
        echo "Aborting (exit $exit_code)"
        exit "$exit_code"
        ;;
      s|shell)
        echo "Dropping to shell. Type 'exit' to continue the script."
        bash --noprofile --norc </dev/tty || true
        ;;
      *)
        echo "Please enter c, a or s." ;;
    esac
  done

  # Restore errexit behaviour
  set -e
}

trap 'on_error $? $LINENO' ERR

# parse args
while [ "$#" -gt 0 ]; do
  case "$1" in
    --all) RUN_ALL=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      cat <<'USAGE'
install-docker-engine.sh - Install Docker Engine, GPU support (NVIDIA/AMD) and (optionally) pass

Auto-detects GPU vendor:
  NVIDIA → installs NVIDIA Container Toolkit (--gpus all)
  AMD    → verifies ROCm driver setup (/dev/kfd, /dev/dri)
  None   → skips GPU setup

Usage: sudo ./install-docker-engine.sh [--all] [--yes] [--dry-run]

Options:
  --all      Run full install (engine + GPU setup + pass) non-interactively
  --yes      Assume yes to prompts (non-interactive; CLI only, use with care)
  --dry-run  Print actions without executing them
  -h, --help Show this help
USAGE
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

RUN() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "+ $*"
  else
    echo "+ $*" >&2
    eval "$@"
  fi
}

confirm() {
  if [ "$ASSUME_YES" -eq 1 ]; then
    return 0
  fi
  read -r -p "$1 [y/N]: " ans
  case "${ans,,}" in
    y|yes) return 0 ;;
    *) return 1 ;;
  esac
}

detect_pm() {
  if command -v apt-get >/dev/null 2>&1; then
    PM=apt
  elif command -v dnf >/dev/null 2>&1; then
    PM=dnf
  elif command -v yum >/dev/null 2>&1; then
    PM=yum
  elif command -v pacman >/dev/null 2>&1; then
    PM=pacman
  elif command -v zypper >/dev/null 2>&1; then
    PM=zypper
  elif command -v apk >/dev/null 2>&1; then
    PM=apk
  else
    PM=unknown
  fi
}

# If running as root, avoid calling sudo for each command. Use $SUDO variable.
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

# Determine the real (non-root) user — needed for group membership.
# $SUDO_USER is set by sudo; fall back to $USER or logname.
REAL_USER="${SUDO_USER:-${USER:-$(logname 2>/dev/null || echo root)}}"

# Detected GPU vendor: nvidia, rocm, or cpu
GPU_VENDOR="unknown"

# Detect GPU vendor on this system (reuses logic from manage-docker-images.sh)
detect_gpu_vendor() {
  # Check for NVIDIA first (nvidia-smi is the most reliable indicator)
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    echo "nvidia"
    return
  fi

  # Check for AMD ROCm (/dev/kfd is the kernel fusion driver for AMD GPUs)
  if [ -e /dev/kfd ] && [ -d /dev/dri ]; then
    echo "rocm"
    return
  fi

  # Check via rocm-smi
  if command -v rocm-smi >/dev/null 2>&1 && rocm-smi >/dev/null 2>&1; then
    echo "rocm"
    return
  fi

  # Check lspci for GPU presence
  if command -v lspci >/dev/null 2>&1; then
    if lspci 2>/dev/null | grep -qi "nvidia"; then
      echo "nvidia"
      return
    elif lspci 2>/dev/null | grep -qi "amd.*radeon\|amd.*instinct\|advanced micro devices.*gpu"; then
      echo "rocm"
      return
    fi
  fi

  # No GPU detected
  echo "cpu"
}


install_prereqs() {
  echo "Installing common prerequisites (curl, ca-certificates, gnupg if needed)"
  case "$PM" in
    apt)
      RUN $SUDO apt-get update
      RUN $SUDO apt-get install -y ca-certificates curl gnupg
      ;;
    dnf|yum)
      RUN $SUDO $PM install -y curl gnupg2 ca-certificates
      ;;
    pacman)
      RUN $SUDO pacman -Sy --noconfirm curl gnupg ca-certificates
      ;;
    zypper)
      RUN $SUDO zypper refresh
      RUN $SUDO zypper install -y curl gpg2 ca-certificates
      ;;
    apk)
      RUN $SUDO apk add --no-cache curl gnupg ca-certificates
      ;;
    *) echo "Unsupported package manager: $PM"; exit 1 ;;
  esac
}

add_docker_repo_and_install() {
  echo "Installing Docker Engine (packages vary by distro)..."
  case "$PM" in
    apt)
      # Install Docker's official GPG key and repo (DEB822 format — current method)
      RUN $SUDO install -m 0755 -d /etc/apt/keyrings
      
      # Download Docker GPG key
      # Handle Ubuntu derivatives (Linux Mint, Pop!_OS, etc.) by using ubuntu repo
      local DOCKER_ID DOCKER_CODENAME
      DOCKER_ID=$(. /etc/os-release; echo "$ID")
      
      # Check if this is an Ubuntu derivative - use ubuntu repo instead
      if [ "$DOCKER_ID" = "linuxmint" ] || [ "$DOCKER_ID" = "pop" ] || [ "$DOCKER_ID" = "zorin" ] || [ "$DOCKER_ID" = "elementary" ]; then
        echo "Detected Ubuntu derivative ($DOCKER_ID) - using Ubuntu Docker repository"
        DOCKER_ID="ubuntu"
      elif [ "$DOCKER_ID" != "ubuntu" ] && [ "$DOCKER_ID" != "debian" ]; then
        # Check if UBUNTU_CODENAME exists (indicates Ubuntu derivative)
        if grep -q "UBUNTU_CODENAME" /etc/os-release 2>/dev/null; then
          echo "Detected Ubuntu derivative ($DOCKER_ID) - using Ubuntu Docker repository"
          DOCKER_ID="ubuntu"
        fi
      fi
      
      RUN "curl -fsSL https://download.docker.com/linux/${DOCKER_ID}/gpg | $SUDO tee /etc/apt/keyrings/docker.asc > /dev/null"
      RUN $SUDO chmod a+r /etc/apt/keyrings/docker.asc

      # Use DEB822 .sources format (modern)
      # For Ubuntu derivatives, use UBUNTU_CODENAME; otherwise use VERSION_CODENAME
      DOCKER_CODENAME=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}")
      if [ -z "$DOCKER_CODENAME" ]; then
        echo "ERROR: Could not determine distro codename from /etc/os-release"
        echo "Check that VERSION_CODENAME or UBUNTU_CODENAME is set."
        return 1
      fi
      RUN "cat <<SRCEOF | $SUDO tee /etc/apt/sources.list.d/docker.sources
Types: deb
URIs: https://download.docker.com/linux/${DOCKER_ID}
Suites: ${DOCKER_CODENAME}
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
SRCEOF"

      RUN $SUDO apt-get update
      RUN $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      ;;
    dnf|yum)
      # Fedora/RHEL/CentOS: use Docker upstream repo
      RUN $SUDO dnf -y install dnf-plugins-core || true

      # Determine repo URL based on distro
      local DISTRO_ID
      DISTRO_ID=$(. /etc/os-release; echo "$ID")
      # Docker provides repos for fedora, centos, rhel — fall back to centos for unknown
      local REPO_URL
      case "$DISTRO_ID" in
        fedora|centos|rhel) REPO_URL="https://download.docker.com/linux/${DISTRO_ID}/docker-ce.repo" ;;
        *) REPO_URL="https://download.docker.com/linux/centos/docker-ce.repo" ;;
      esac

      # Fedora 42+ uses 'dnf config-manager addrepo --from-repofile=URL'
      # Older dnf uses 'dnf config-manager --add-repo URL'
      # Fall back to direct download if neither works
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "+ dnf config-manager addrepo --from-repofile=$REPO_URL  (or fallback)"
      elif $SUDO dnf config-manager addrepo --from-repofile="$REPO_URL" 2>/dev/null; then
        echo "Added Docker repo using 'dnf config-manager addrepo --from-repofile'"
      elif $SUDO dnf config-manager --add-repo "$REPO_URL" 2>/dev/null; then
        echo "Added Docker repo using 'dnf config-manager --add-repo'"
      else
        echo "dnf config-manager not available; fetching repo file directly"
        RUN $SUDO curl -fsSL "$REPO_URL" -o /etc/yum.repos.d/docker-ce.repo || true
      fi

      RUN $SUDO $PM install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin || true
      ;;
    pacman)
      echo "Pacman: installing docker package from official repos"
      RUN $SUDO pacman -S --noconfirm docker
      ;;
    zypper)
      RUN $SUDO zypper addrepo https://download.docker.com/linux/opensuse/docker-ce.repo docker
      RUN $SUDO zypper refresh
      RUN $SUDO zypper install -y docker-ce docker-ce-cli containerd
      ;;
    apk)
      RUN $SUDO apk add docker
      ;;
    *) echo "Unsupported package manager: $PM"; exit 1 ;;
  esac

  echo "Post-install: enable/start docker (system mode) and add user to docker group"
  RUN $SUDO systemctl enable --now docker || true
  if ! getent group docker >/dev/null 2>&1; then
    RUN $SUDO groupadd docker || true
  fi
  if [ -n "$REAL_USER" ] && [ "$REAL_USER" != "root" ]; then
    if ! id -nG "$REAL_USER" 2>/dev/null | grep -qw docker; then
      if [ "$ASSUME_YES" -eq 1 ] || confirm "Add user '$REAL_USER' to group 'docker' to run docker without sudo?"; then
        RUN $SUDO usermod -aG docker "$REAL_USER" || true
        echo ""
        echo "============================================================"
        echo "  User '$REAL_USER' added to 'docker' group."
        echo "  To use docker without sudo, you MUST either:"
        echo "    - Log out and log back in, OR"
        echo "    - Reboot"
        echo "  (Running 'newgrp docker' works for the current terminal only)"
        echo "============================================================"
        echo ""
      fi
    fi
  else
    echo "Running as root — skipping docker group membership (not needed for root)."
  fi
}

# Setup GPU support — auto-detects vendor and runs the appropriate setup
setup_gpu_support() {
  echo "Setting up GPU support for Docker containers..."
  echo "Detected GPU vendor: $GPU_VENDOR"
  echo

  case "$GPU_VENDOR" in
    nvidia)
      install_nvidia_toolkit
      ;;
    rocm)
      verify_amd_rocm
      ;;
    cpu)
      echo "No GPU detected. Skipping GPU setup."
      echo "Docker containers will run in CPU-only mode."
      echo "If you have a GPU, ensure drivers are installed and re-run this step."
      ;;
    *)
      echo "Unknown GPU vendor: $GPU_VENDOR"
      echo "You can manually run option 3a (NVIDIA) or 3b (AMD) from the menu."
      ;;
  esac
}

install_nvidia_toolkit() {
  echo "Installing NVIDIA Container Toolkit — current method from https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"

  # Check if NVIDIA drivers are available on the host
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "WARNING: nvidia-smi not found. NVIDIA GPU drivers may not be installed."
    echo "The container toolkit requires working GPU drivers on the host."
    if ! confirm "Continue anyway?"; then
      return
    fi
  else
    echo "Host GPU detected:"
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null || true
  fi

  case "$PM" in
    apt)
      # Official method: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
      RUN curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | $SUDO gpg --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
      RUN 'curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed "s#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g" | '"$SUDO"' tee /etc/apt/sources.list.d/nvidia-container-toolkit.list'
      RUN $SUDO apt-get update
      RUN $SUDO apt-get install -y nvidia-container-toolkit
      ;;
    dnf|yum)
      # Official method for RPM-based distros
      RUN 'curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo | '"$SUDO"' tee /etc/yum.repos.d/nvidia-container-toolkit.repo'
      RUN $SUDO $PM clean expire-cache || true
      RUN $SUDO $PM install -y nvidia-container-toolkit
      ;;
    zypper)
      RUN $SUDO zypper ar https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo
      RUN $SUDO zypper --gpg-auto-import-keys install -y nvidia-container-toolkit
      ;;
    pacman)
      echo "Arch Linux: install nvidia-container-toolkit from AUR (e.g., yay -S nvidia-container-toolkit)"
      echo "Or use: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
      return
      ;;
    apk)
      echo "Alpine: please follow https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
      return
      ;;
    *) echo "Unsupported package manager: $PM"; return ;;
  esac

  # Configure Docker to use the NVIDIA runtime (official method via nvidia-ctk)
  echo "Configuring Docker runtime with nvidia-ctk..."
  if command -v nvidia-ctk >/dev/null 2>&1; then
    RUN $SUDO nvidia-ctk runtime configure --runtime=docker
  else
    echo "WARNING: nvidia-ctk not found after install. You may need to configure manually."
    echo "Run: sudo nvidia-ctk runtime configure --runtime=docker"
  fi
  RUN $SUDO systemctl restart docker || true
}

verify_amd_rocm() {
  echo "Verifying AMD/ROCm setup for Docker GPU passthrough..."
  echo
  echo "AMD GPUs do NOT need a container toolkit like NVIDIA."
  echo "Docker uses direct device passthrough: --device=/dev/kfd --device=/dev/dri"
  echo "SmartLML auto-detects AMD GPUs and passes the correct flags."
  echo

  local all_ok=1

  # Check /dev/kfd (kernel fusion driver)
  if [ -e /dev/kfd ]; then
    echo "  ✓ /dev/kfd exists (ROCm kernel driver loaded)"
  else
    echo "  ✗ /dev/kfd NOT found — ROCm kernel driver may not be installed"
    echo "    Install ROCm: https://rocm.docs.amd.com/projects/install-on-linux/en/latest/"
    all_ok=0
  fi

  # Check /dev/dri
  if [ -d /dev/dri ]; then
    echo "  ✓ /dev/dri exists (DRI render nodes available)"
    if ls /dev/dri/render* >/dev/null 2>&1; then
      echo "    Render nodes: $(ls /dev/dri/render* 2>/dev/null | tr '\n' ' ')"
    fi
  else
    echo "  ✗ /dev/dri NOT found — GPU driver may not be loaded"
    all_ok=0
  fi

  # Check video group membership
  if [ -n "$REAL_USER" ] && [ "$REAL_USER" != "root" ]; then
    if id -nG "$REAL_USER" 2>/dev/null | grep -qw video; then
      echo "  ✓ User '$REAL_USER' is in 'video' group"
    else
      echo "  ⚠ User '$REAL_USER' is NOT in 'video' group"
      echo "    This may be needed for GPU access in containers"
      if [ "$ASSUME_YES" -eq 1 ] || confirm "Add user '$REAL_USER' to 'video' group?"; then
        RUN $SUDO usermod -aG video "$REAL_USER" || true
        echo "    Added to 'video' group (re-login required)"
      fi
    fi
    if id -nG "$REAL_USER" 2>/dev/null | grep -qw render; then
      echo "  ✓ User '$REAL_USER' is in 'render' group"
    else
      echo "  ⚠ User '$REAL_USER' is NOT in 'render' group"
      if [ "$ASSUME_YES" -eq 1 ] || confirm "Add user '$REAL_USER' to 'render' group?"; then
        RUN $SUDO usermod -aG render "$REAL_USER" || true
        echo "    Added to 'render' group (re-login required)"
      fi
    fi
  fi

  # Check rocm-smi if available
  if command -v rocm-smi >/dev/null 2>&1; then
    echo "  ✓ rocm-smi available"
    echo "    GPU info:"
    rocm-smi --showproductname 2>/dev/null | head -20 || true
  elif command -v rocminfo >/dev/null 2>&1; then
    echo "  ✓ rocminfo available"
    rocminfo 2>/dev/null | grep -E "Name:|Marketing" | head -6 || true
  else
    echo "  ⚠ Neither rocm-smi nor rocminfo found (ROCm userspace tools not installed)"
    echo "    This is OK if you only need Docker GPU passthrough"
  fi

  echo
  if [ "$all_ok" -eq 1 ]; then
    echo "AMD/ROCm setup looks good. Docker containers will use:"
    echo "  --device=/dev/kfd --device=/dev/dri --group-add video"
  else
    echo "Some AMD/ROCm requirements are missing. See above for details."
  fi
}

setup_pass() {
  echo "Setting up pass (passwordstore) for Docker credential storage"
  if ! command -v pass >/dev/null 2>&1; then
    case "$PM" in
      apt) RUN $SUDO apt-get install -y pass gnupg || true ;;
      dnf|yum) RUN $SUDO $PM install -y pass gnupg2 || true ;;
      pacman) RUN $SUDO pacman -S --noconfirm pass gnupg || true ;;
      zypper) RUN $SUDO zypper install -y pass gnupg || true ;;
      apk) RUN $SUDO apk add pass gnupg || true ;;
    esac
  fi

  if command -v gpg >/dev/null 2>&1; then
    echo "GPG present. To use pass you need a GPG key."
    if ! gpg --list-secret-keys >/dev/null 2>&1 || [ -z "$(gpg --list-secret-keys --keyid-format LONG 2>/dev/null)" ]; then
      if [ "$ASSUME_YES" -eq 1 ] || confirm "No GPG key found. Create one now (interactive)?"; then
        echo "Please follow GPG prompts to create a key (name/email passphrase)."
        RUN gpg --generate-key
      else
        echo "Skipping GPG key generation. You can create one later and run this step again."
        return
      fi
    fi
    KEYID=$(gpg --list-secret-keys --keyid-format LONG | awk '/sec/{print $2}' | head -n1 | cut -d'/' -f2)
    if [ -n "$KEYID" ]; then
      if [ "$ASSUME_YES" -eq 1 ] || confirm "Initialize pass with key $KEYID?"; then
        RUN pass init "$KEYID"
      fi
    fi
  else
    echo "gpg not found; cannot initialize pass."
  fi
}

test_install() {
  echo "Testing Docker installation..."

  # After 'usermod -aG docker', the current shell may not have the new group yet.
  # Use sudo for docker commands if direct access fails (common right after install).
  local DRUN=""
  if ! docker info >/dev/null 2>&1; then
    if [ -n "$SUDO" ]; then
      echo "Note: current shell does not have 'docker' group yet (need re-login)."
      echo "Using sudo for docker test commands..."
      DRUN="$SUDO"
    fi
  fi

  RUN $DRUN docker --version || true
  RUN "$DRUN docker info 2>/dev/null | sed -n '1,120p'" || true

  case "$GPU_VENDOR" in
    nvidia)
      if command -v nvidia-smi >/dev/null 2>&1; then
        echo "Testing NVIDIA GPU inside container (this will pull a small image if necessary)"
        RUN $DRUN docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi || true
      fi
      ;;
    rocm)
      if [ -e /dev/kfd ] && [ -d /dev/dri ]; then
        echo "Testing AMD GPU inside container (this will pull a small image if necessary)"
        RUN $DRUN docker run --rm --device=/dev/kfd --device=/dev/dri --group-add video rocm/rocm-terminal:latest rocm-smi || true
      fi
      ;;
    *)
      echo "No GPU detected — skipping GPU container test."
      ;;
  esac
}

show_status() {
  echo
  echo "--- Status ---"
  echo "Package manager: $PM"
  echo "Dry-run: $DRY_RUN  Assume-yes: $ASSUME_YES"
  if command -v docker >/dev/null 2>&1; then
    echo "Docker: $(docker --version 2>/dev/null || echo 'not working')"
  else
    echo "Docker: not installed"
  fi
  echo "GPU vendor: $GPU_VENDOR"
  case "$GPU_VENDOR" in
    nvidia)
      if command -v nvidia-ctk >/dev/null 2>&1; then
        echo "NVIDIA CTK: installed"
      else
        echo "NVIDIA CTK: not installed"
      fi
      nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null || true
      ;;
    rocm)
      echo "AMD/ROCm: /dev/kfd $([ -e /dev/kfd ] && echo 'present' || echo 'MISSING')"
      echo "AMD/ROCm: /dev/dri $([ -d /dev/dri ] && echo 'present' || echo 'MISSING')"
      rocm-smi --showproductname 2>/dev/null | head -5 || true
      ;;
    *)
      echo "GPU: none detected"
      ;;
  esac
  echo "------------"
  echo
}

main_menu() {
  detect_pm
  GPU_VENDOR=$(detect_gpu_vendor)
  echo "Detected package manager: $PM"
  echo "Detected GPU vendor: $GPU_VENDOR"
  if [ "$RUN_ALL" -eq 1 ]; then
    echo "Running ALL steps (non-interactive)"
    install_prereqs
    add_docker_repo_and_install
    setup_gpu_support
    setup_pass
    test_install
    echo "ALL steps finished."
    exit 0
  fi

  # Build GPU menu label based on detected vendor
  local gpu_label
  case "$GPU_VENDOR" in
    nvidia) gpu_label="Setup GPU support (auto: NVIDIA → install toolkit)" ;;
    rocm)   gpu_label="Setup GPU support (auto: AMD → verify ROCm)" ;;
    *)      gpu_label="Setup GPU support (auto: no GPU detected)" ;;
  esac

  while true; do
    cat <<MENU

Install Docker Engine Menu:
  1) Install prerequisites (curl, gnupg, etc.)
  2) Install Docker Engine (system mode) and configure group
  3) ${gpu_label}
    3a) Force: Install NVIDIA Container Toolkit
    3b) Force: Verify AMD/ROCm setup
  4) Setup pass (GPG + pass init) for credential helper
  5) Test installation (docker info, optional GPU test)
  a) Run ALL steps sequentially (with prompts)
  b) Run ALL steps non-interactively (auto-yes)
  d) Toggle dry-run
  s) Show status
  q) Quit
MENU
    read -r -p "Choice: " choice
    case "${choice,,}" in
      1) install_prereqs ;;
      2) add_docker_repo_and_install ;;
      3) setup_gpu_support ;;
      3a) install_nvidia_toolkit ;;
      3b) verify_amd_rocm ;;
      4) setup_pass ;;
      5) test_install ;;
      a)
        if confirm "Run ALL steps?"; then
          install_prereqs
          add_docker_repo_and_install
          setup_gpu_support
          setup_pass
          test_install
        fi
        ;;
      b)
        echo "Running ALL steps with --yes (non-interactive)..."
        local PREV_YES=$ASSUME_YES
        ASSUME_YES=1
        install_prereqs
        add_docker_repo_and_install
        setup_gpu_support
        setup_pass
        test_install
        ASSUME_YES=$PREV_YES
        echo "ALL steps finished."
        ;;
      d)
        DRY_RUN=$((1-DRY_RUN))
        echo "Dry-run set to: $DRY_RUN" ;;
      t)
        echo "Interactive toggle 'assume-yes' is disabled. Use '--yes' on the command line for non-interactive runs." ;;
      s) show_status ;;
      q) echo "Exit."; exit 0 ;;
      *) echo "Invalid option" ;;
    esac
  done
}

if [ "$DRY_RUN" -eq 1 ]; then
  echo "*** DRY RUN MODE - no changes will be made ***"
fi

main_menu
