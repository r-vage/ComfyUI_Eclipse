#!/usr/bin/env bash
# manage-docker-images.sh
# Interactive Docker image manager for SmartLML backends
# Pull, update, list, inspect, and remove Docker images used by SmartLML
#
# Supports both NVIDIA (CUDA) and AMD (ROCm) GPUs with auto-detection
# GPU vendor can be overridden via GPU_VENDOR environment variable
#
# Usage:
#   ./manage-docker-images.sh              # interactive menu (auto-detects GPU)
#   ./manage-docker-images.sh pull-all     # pull/update all SmartLML images
#   ./manage-docker-images.sh pull IMAGE   # pull a specific image (name or number)
#   ./manage-docker-images.sh list         # list all SmartLML images
#   ./manage-docker-images.sh status       # show image status (installed/missing)
#   ./manage-docker-images.sh versions     # probe installed images for version info
#   ./manage-docker-images.sh remove IMAGE # remove a specific image
#   ./manage-docker-images.sh clean        # remove unused/dangling images
#
# GPU Override Examples:
#   GPU_VENDOR=nvidia ./manage-docker-images.sh  # force NVIDIA images
#   GPU_VENDOR=rocm ./manage-docker-images.sh    # force AMD/ROCm images
#   GPU_VENDOR=cpu ./manage-docker-images.sh     # force CPU-only images

set -euo pipefail

# ─── Color helpers ───────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}ℹ${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC} $*"; }
error()   { echo -e "${RED}✗${NC} $*"; }
header()  { echo -e "\n${BOLD}${CYAN}═══ $* ═══${NC}\n"; }

# ─── SmartLML Backend Image Definitions ──────────────────────────────────
# Format: NAME|IMAGE|DESCRIPTION|PORT|GPU_NOTE

# NVIDIA GPU images (require NVIDIA Container Toolkit with --gpus all)
IMAGES_NVIDIA=(
  "vLLM|vllm/vllm-openai:latest|High-performance OpenAI-compatible inference server|8000|--gpus all --ipc=host"
  "SGLang|lmsysorg/sglang:latest|Fast inference with RadixAttention (alternative to vLLM)|30000|--gpus all --shm-size 16g --ipc host"
  "Ollama|ollama/ollama:latest|Easy model management, supports GGUF and Mistral3|11434|--gpus all"
  "llama.cpp|ghcr.io/ggml-org/llama.cpp:server-cuda|Lightweight GGUF inference with CUDA support|8080|--gpus all"
)

# AMD/ROCm GPU images (require ROCm drivers and /dev/kfd, /dev/dri access)
# Note: SGLang ROCm images are architecture-specific (mi30x=MI300X, mi35x=MI350X)
IMAGES_ROCM=(
  "vLLM (ROCm)|rocm/vllm:latest|AMD-optimized vLLM from official ROCm repository|8000|--device=/dev/kfd --device=/dev/dri --group-add video"
  "SGLang (ROCm)|lmsysorg/sglang:v0.5.9-rocm720-mi30x|SGLang for ROCm 7.2 + MI300X (see extras for other GPUs)|30000|--device=/dev/kfd --device=/dev/dri --group-add video --shm-size 16g"
  "Ollama (ROCm)|ollama/ollama:rocm|Official Ollama image with AMD ROCm support|11434|--device=/dev/kfd --device=/dev/dri --group-add video"
  "llama.cpp|ghcr.io/ggml-org/llama.cpp:server|llama.cpp CPU (no official ROCm image)|8080|none"
)

# CPU-only images (no GPU required)
IMAGES_CPU=(
  "Ollama (CPU)|ollama/ollama:latest|Ollama without GPU acceleration|11434|none"
  "llama.cpp (CPU)|ghcr.io/ggml-org/llama.cpp:server|llama.cpp CPU inference|8080|none"
)

# Current GPU vendor (nvidia, rocm, cpu, or auto)
GPU_VENDOR="${GPU_VENDOR:-auto}"

# Active images array (set by detect_or_select_gpu)
IMAGES=()

# Additional useful images that users may want
EXTRA_IMAGES=(
  "vLLM (specific)|vllm/vllm-openai:v0.8.5|vLLM pinned to v0.8.5 (NVIDIA)|8000|--gpus all --ipc=host"
  "vLLM ROCm (dev)|rocm/vllm-dev:latest|AMD vLLM development image|8000|--device=/dev/kfd --device=/dev/dri --group-add video"
  "SGLang ROCm 7.2 MI350X|lmsysorg/sglang:v0.5.9-rocm720-mi35x|SGLang for ROCm 7.2 + MI350X|30000|--device=/dev/kfd --device=/dev/dri --group-add video --shm-size 16g"
  "SGLang ROCm 7.0 MI300X|lmsysorg/sglang:v0.5.9-rocm700-mi30x|SGLang for ROCm 7.0 + MI300X|30000|--device=/dev/kfd --device=/dev/dri --group-add video --shm-size 16g"
  "SGLang ROCm 7.0 MI350X|lmsysorg/sglang:v0.5.9-rocm700-mi35x|SGLang for ROCm 7.0 + MI350X|30000|--device=/dev/kfd --device=/dev/dri --group-add video --shm-size 16g"
)

# ─── GPU Detection ───────────────────────────────────────────────────────

# Detect GPU vendor on this system
detect_gpu_vendor() {
  # Check for NVIDIA first (nvidia-smi is the most reliable indicator)
  if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null 2>&1; then
    echo "nvidia"
    return
  fi
  
  # Check for AMD ROCm (/dev/kfd is the kernel fusion driver for AMD GPUs)
  if [ -e /dev/kfd ] && [ -d /dev/dri ]; then
    echo "rocm"
    return
  fi
  
  # Check via rocm-smi
  if command -v rocm-smi &>/dev/null && rocm-smi &>/dev/null 2>&1; then
    echo "rocm"
    return
  fi
  
  # Check lspci for GPU presence
  if command -v lspci &>/dev/null; then
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

# Set the IMAGES array based on GPU vendor
set_images_for_vendor() {
  local vendor="$1"
  case "$vendor" in
    nvidia)
      IMAGES=("${IMAGES_NVIDIA[@]}")
      ;;
    rocm|amd)
      IMAGES=("${IMAGES_ROCM[@]}")
      ;;
    cpu|none)
      IMAGES=("${IMAGES_CPU[@]}")
      ;;
    *)
      # Default to NVIDIA
      IMAGES=("${IMAGES_NVIDIA[@]}")
      ;;
  esac
}

# Get human-readable GPU vendor name
gpu_vendor_name() {
  case "$1" in
    nvidia) echo "NVIDIA (CUDA)" ;;
    rocm|amd) echo "AMD (ROCm)" ;;
    cpu|none) echo "CPU only" ;;
    *) echo "Unknown" ;;
  esac
}

# Get docker GPU args for current vendor (informational)
gpu_docker_args() {
  case "$1" in
    nvidia) echo "--gpus all" ;;
    rocm|amd) echo "--device=/dev/kfd --device=/dev/dri --group-add video" ;;
    cpu|none) echo "(none)" ;;
    *) echo "(unknown)" ;;
  esac
}

# Initialize GPU detection with auto-detect or user override
init_gpu_vendor() {
  if [ "$GPU_VENDOR" = "auto" ]; then
    GPU_VENDOR=$(detect_gpu_vendor)
  fi
  set_images_for_vendor "$GPU_VENDOR"
}

# ─── Helper Functions ────────────────────────────────────────────────────

# Parse pipe-delimited image definition
get_field() {
  local entry="$1" field="$2"
  echo "$entry" | cut -d'|' -f"$field"
}

# Check if docker is available and running
check_docker() {
  # Initialize GPU vendor detection first
  init_gpu_vendor
  
  if ! command -v docker &>/dev/null; then
    error "Docker is not installed or not in PATH"
    echo "  Run install-docker-engine.sh first, or visit https://docs.docker.com/engine/install/"
    exit 1
  fi
  
  if ! docker info &>/dev/null 2>&1; then
    # Check if user is in docker group but current shell doesn't have it active
    if id -nG "$USER" 2>/dev/null | grep -qw docker; then
      # User is in docker group but needs to activate it
      info "Docker group membership detected - activating group for this session..."
      
      # Re-exec this script with docker group active using sg (similar to newgrp but for single command)
      if command -v sg &>/dev/null; then
        exec sg docker -c "$0 $*"
      else
        warn "Could not auto-activate docker group (sg command not found)"
        echo "  Run: newgrp docker"
        echo "  Then try again, or just log out/in"
        exit 1
      fi
    else
      # User not in docker group or docker daemon not running
      error "Docker daemon is not running or you lack permissions"
      
      # Check if docker daemon is actually running
      if ! systemctl is-active --quiet docker 2>/dev/null; then
        echo "  Docker daemon is not running. Start it with:"
        echo "    sudo systemctl start docker"
      else
        echo "  You are not in the 'docker' group. Add yourself with:"
        echo "    sudo usermod -aG docker \$USER"
        echo "  Then log out/in or run: newgrp docker"
      fi
      exit 1
    fi
  fi
}

# Check if an image exists locally
image_exists() {
  local img="$1"
  docker image inspect "$img" &>/dev/null 2>&1
}

# Get image size (human-readable)
image_size() {
  local img="$1"
  docker image inspect "$img" --format='{{.Size}}' 2>/dev/null | awk '{
    if ($1 > 1073741824) printf "%.1f GB", $1/1073741824
    else if ($1 > 1048576) printf "%.1f MB", $1/1048576
    else printf "%.1f KB", $1/1024
  }'
}

# Get image creation date
image_date() {
  local img="$1"
  docker image inspect "$img" --format='{{.Created}}' 2>/dev/null | cut -d'T' -f1
}

# Get image ID (short)
image_id() {
  local img="$1"
  docker image inspect "$img" --format='{{.ID}}' 2>/dev/null | cut -d: -f2 | head -c 12
}

# Get software version from inside image (backend-specific)
# Uses a short-lived container with timeout to avoid blocking
image_version() {
  local img="$1"
  local name="$2"
  local ver=""

  case "${name,,}" in
    *ollama*)
      ver=$(timeout 10 docker run --rm --entrypoint ollama "$img" --version 2>&1 | grep -oP 'version is \K[0-9.]+' || true)
      ;;
    *vllm*)
      ver=$(timeout 30 docker run --rm --entrypoint python3 "$img" -c "import vllm; print(vllm.__version__)" 2>/dev/null | tail -1 || true)
      ;;
    *sglang*)
      ver=$(timeout 30 docker run --rm --entrypoint python3 "$img" -c "import sglang; print(sglang.__version__)" 2>/dev/null | tail -1 || true)
      ;;
    *llama*)
      ver=$(timeout 10 docker run --rm "$img" --version 2>&1 | grep -oP 'version: \K[0-9]+ \([a-f0-9]+\)' || true)
      ;;
  esac

  # Only return if it looks like a version string (not error output)
  if [[ -n "$ver" && ${#ver} -lt 40 && ! "$ver" =~ Error|error|Traceback ]]; then
    echo "$ver"
  else
    echo "—"
  fi
}

# ─── Core Actions ────────────────────────────────────────────────────────

# Pull a single image
pull_image() {
  local img="$1"
  local name="${2:-$img}"

  echo -e "${BOLD}Pulling ${CYAN}${name}${NC} ${DIM}(${img})${NC}"

  # Save full image ID before pull (ancestor filter needs full hash, not truncated)
  local old_id="" old_id_full="" old_ver=""
  if image_exists "$img"; then
    old_id=$(image_id "$img")
    old_id_full=$(docker image inspect "$img" --format='{{.ID}}' 2>/dev/null)
    old_ver=$(image_version "$img" 2>/dev/null)
    info "Installed: ${old_id}${old_ver:+  (${old_ver})}"
  fi

  if docker pull "$img"; then
    success "Successfully pulled ${name}"

    # Check if image was actually updated
    local new_id new_ver
    new_id=$(image_id "$img")
    if [[ -n "$old_id" && "$old_id" != "$new_id" ]]; then
      new_ver=$(image_version "$img" 2>/dev/null)
      echo
      success "Image updated!  ${old_id}${old_ver:+ ($old_ver)} → ${new_id}${new_ver:+ ($new_ver)}"
      echo -e "  Size: $(image_size "$img")  |  Date: $(image_date "$img")"

      # Find containers still using the OLD image (by full ID — short IDs don't work with ancestor filter)
      local stale_containers
      stale_containers=$(docker ps -a --filter "ancestor=$old_id_full" --format '{{.Names}}\t{{.Status}}' 2>/dev/null)
      if [[ -n "$stale_containers" ]]; then
        echo
        warn "These containers still use the old image:"
        echo "$stale_containers" | while IFS=$'\t' read -r cname cstatus; do
          echo -e "  ${DIM}${cname}${NC}  (${cstatus})"
        done
        echo
        read -r -p "Remove old containers so SmartLML creates fresh ones? [Y/n] " ans </dev/tty || true
        if [[ -z "${ans}" || "${ans,,}" == "y" ]]; then
          docker ps -a --filter "ancestor=$old_id_full" --format '{{.ID}}' | xargs -r docker rm -f >/dev/null 2>&1
          success "Old containers removed — SmartLML will create new ones on next run"
        else
          info "Kept old containers (SmartLML will auto-detect and recreate them)"
        fi
      fi
    elif [[ -n "$old_id" ]]; then
      info "Already up to date${old_ver:+  (${old_ver})}"
    else
      echo -e "  Size: $(image_size "$img")  |  Date: $(image_date "$img")  |  ID: ${new_id}"
    fi
  else
    error "Failed to pull ${img}"
    return 1
  fi
}

# Pull all SmartLML backend images
pull_all() {
  header "Pulling All SmartLML Backend Images"
  local failed=0
  for entry in "${IMAGES[@]}"; do
    local name img
    name=$(get_field "$entry" 1)
    img=$(get_field "$entry" 2)
    echo
    pull_image "$img" "$name" || ((failed++))
  done
  echo
  if [ "$failed" -eq 0 ]; then
    success "All images pulled successfully!"
  else
    warn "${failed} image(s) failed to pull"
  fi
}

# Show status of all images
show_status() {
  header "SmartLML Docker Image Status"
  
  # Show GPU vendor info
  local detected
  detected=$(detect_gpu_vendor)
  echo -e "  GPU Mode:     ${BOLD}$(gpu_vendor_name "$GPU_VENDOR")${NC}"
  echo -e "  Detected GPU: ${DIM}$(gpu_vendor_name "$detected")${NC}"
  echo -e "  Docker args:  ${DIM}$(gpu_docker_args "$GPU_VENDOR")${NC}"
  echo

  printf "${BOLD}%-15s %-45s %-10s %-12s %-8s${NC}\n" "Backend" "Image" "Status" "Size" "Port"
  printf "%-15s %-45s %-10s %-12s %-8s\n" "───────────" "─────────────────────────────────────────" "──────" "────────" "────"

  for entry in "${IMAGES[@]}"; do
    local name img desc port
    name=$(get_field "$entry" 1)
    img=$(get_field "$entry" 2)
    desc=$(get_field "$entry" 3)
    port=$(get_field "$entry" 4)

    if image_exists "$img"; then
      local size
      size=$(image_size "$img")
      printf "%-15s %-45s ${GREEN}%-10s${NC} %-12s %-8s\n" "$name" "$img" "installed" "$size" "$port"
    else
      printf "%-15s %-45s ${RED}%-10s${NC} %-12s %-8s\n" "$name" "$img" "missing" "—" "$port"
    fi
  done
  echo
}

# Show versions of installed images (probes each image — may take 10-30s per backend)
show_versions() {
  header "SmartLML Backend Versions"
  info "Probing installed images for version info (this may take a moment)...\n"

  printf "${BOLD}%-15s %-20s %-12s %-12s${NC}\n" "Backend" "Version" "Size" "Image Date"
  printf "%-15s %-20s %-12s %-12s\n" "───────────" "────────────────" "────────" "──────────"

  for entry in "${IMAGES[@]}"; do
    local name img
    name=$(get_field "$entry" 1)
    img=$(get_field "$entry" 2)

    if image_exists "$img"; then
      local ver size date
      ver=$(image_version "$img" "$name")
      size=$(image_size "$img")
      date=$(image_date "$img")
      printf "%-15s ${CYAN}%-20s${NC} %-12s %-12s\n" "$name" "$ver" "$size" "$date"
    else
      printf "%-15s ${DIM}%-20s${NC} %-12s %-12s\n" "$name" "(not installed)" "—" "—"
    fi
  done
  echo
}

# List all Docker images (not just SmartLML ones)
list_images() {
  header "SmartLML Backend Images"
  show_status

  echo -e "${DIM}───── All Docker images on this system ─────${NC}"
  docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}" 2>/dev/null || \
    docker images
  echo
}

# Remove a specific image
remove_image() {
  local img="$1"
  local name="${2:-$img}"

  if ! image_exists "$img"; then
    warn "${name} (${img}) is not installed"
    return 0
  fi

  # Check if any containers are using this image
  local containers
  containers=$(docker ps -a --filter "ancestor=$img" --format '{{.Names}}' 2>/dev/null)
  if [ -n "$containers" ]; then
    warn "Active/stopped containers using this image:"
    echo "$containers" | sed 's/^/  /'
    echo
    read -r -p "Stop and remove these containers first? [y/N] " ans </dev/tty || true
    if [[ "${ans,,}" == "y" ]]; then
      docker ps -a --filter "ancestor=$img" --format '{{.ID}}' | xargs -r docker rm -f
      success "Containers removed"
    else
      error "Cannot remove image while containers exist. Aborting."
      return 1
    fi
  fi

  echo -e "Removing ${CYAN}${name}${NC} ($(image_size "$img"))..."
  if docker rmi "$img"; then
    success "Removed ${name}"
  else
    error "Failed to remove ${img}"
    return 1
  fi
}

# Clean up dangling/unused images
clean_images() {
  header "Docker Image Cleanup"

  # Show dangling images
  local dangling
  dangling=$(docker images -f "dangling=true" -q 2>/dev/null | wc -l)
  info "Dangling images: ${dangling}"

  if [ "$dangling" -gt 0 ]; then
    echo -e "${DIM}Dangling images (old layers from updates):${NC}"
    docker images -f "dangling=true" --format "  {{.ID}}  {{.Size}}  {{.CreatedSince}}" 2>/dev/null
    echo
    read -r -p "Remove all dangling images? [y/N] " ans </dev/tty || true
    if [[ "${ans,,}" == "y" ]]; then
      docker image prune -f
      success "Dangling images removed"
    fi
  fi

  echo
  # Show total disk usage
  info "Docker disk usage:"
  docker system df 2>/dev/null || true
  echo
  read -r -p "Run full Docker system prune (removes all unused data)? [y/N] " ans </dev/tty || true
  if [[ "${ans,,}" == "y" ]]; then
    docker system prune -f
    success "System prune complete"
  fi
}

# Pull a custom image by name
pull_custom() {
  local img="${1:-}"
  if [ -z "$img" ]; then
    echo -e "Enter the full image name (e.g., ${CYAN}vllm/vllm-openai:v0.8.5${NC}):"
    read -r img </dev/tty || true
  fi
  if [ -z "$img" ]; then
    error "No image name provided"
    return 1
  fi
  pull_image "$img" "$img"
}

# ─── Interactive Menu ────────────────────────────────────────────────────

# Switch GPU vendor interactively
switch_gpu_vendor() {
  header "Select GPU Vendor"
  
  local detected
  detected=$(detect_gpu_vendor)
  
  echo -e "  Detected GPU: ${BOLD}$(gpu_vendor_name "$detected")${NC}"
  echo -e "  Current mode: ${BOLD}$(gpu_vendor_name "$GPU_VENDOR")${NC}"
  echo
  echo -e "  ${BOLD}1)${NC} NVIDIA (CUDA)  ${DIM}— requires NVIDIA Container Toolkit${NC}"
  echo -e "  ${BOLD}2)${NC} AMD (ROCm)     ${DIM}— requires ROCm drivers and /dev/kfd${NC}"
  echo -e "  ${BOLD}3)${NC} CPU only       ${DIM}— no GPU acceleration${NC}"
  echo -e "  ${BOLD}a)${NC} Auto-detect    ${DIM}— use detected GPU ($(gpu_vendor_name "$detected"))${NC}"
  echo -e "  ${BOLD}b)${NC} Back"
  echo
  
  read -r -p "Choose GPU vendor: " choice </dev/tty || true
  case "${choice,,}" in
    1|nvidia) GPU_VENDOR="nvidia"; set_images_for_vendor "$GPU_VENDOR"; success "Switched to NVIDIA images" ;;
    2|rocm|amd) GPU_VENDOR="rocm"; set_images_for_vendor "$GPU_VENDOR"; success "Switched to AMD/ROCm images" ;;
    3|cpu|none) GPU_VENDOR="cpu"; set_images_for_vendor "$GPU_VENDOR"; success "Switched to CPU-only images" ;;
    a|auto) GPU_VENDOR="$detected"; set_images_for_vendor "$GPU_VENDOR"; success "Using auto-detected: $(gpu_vendor_name "$GPU_VENDOR")" ;;
    b|back) return ;;
    *) warn "Invalid choice" ;;
  esac
}

show_menu() {
  while true; do
    header "SmartLML Docker Image Manager"
    
    # Show current GPU vendor
    echo -e "  ${DIM}GPU Mode: ${NC}${BOLD}$(gpu_vendor_name "$GPU_VENDOR")${NC}  ${DIM}(Docker: $(gpu_docker_args "$GPU_VENDOR"))${NC}"
    echo

    echo -e "  ${BOLD}1)${NC} Show image status       ${DIM}— see which backends are installed${NC}"
    echo -e "  ${BOLD}2)${NC} Show versions           ${DIM}— probe installed images for version info${NC}"
    echo -e "  ${BOLD}3)${NC} Pull/update ALL images   ${DIM}— download all ${#IMAGES[@]} SmartLML backends${NC}"
    echo -e "  ${BOLD}4)${NC} Pull a specific image    ${DIM}— choose one backend to install${NC}"
    echo -e "  ${BOLD}5)${NC} Pull a custom image      ${DIM}— enter any Docker image name${NC}"
    echo -e "  ${BOLD}6)${NC} Remove an image          ${DIM}— delete a backend image${NC}"
    echo -e "  ${BOLD}7)${NC} Clean up                 ${DIM}— remove dangling/unused images${NC}"
    echo -e "  ${BOLD}8)${NC} List all Docker images   ${DIM}— show everything on this system${NC}"
    echo -e "  ${BOLD}g)${NC} Switch GPU vendor        ${DIM}— change between NVIDIA / AMD / CPU${NC}"
    echo -e "  ${BOLD}q)${NC} Quit"
    echo

    read -r -p "Choose an option: " choice </dev/tty || true
    case "${choice,,}" in
      1) show_status ;;
      2) show_versions ;;
      3) pull_all ;;
      4) choose_and_pull ;;
      5) pull_custom ;;
      6) choose_and_remove ;;
      7) clean_images ;;
      8) list_images ;;
      g|gpu) switch_gpu_vendor ;;
      q|quit|exit) echo "Bye!"; exit 0 ;;
      *) warn "Invalid choice: $choice" ;;
    esac

    echo
    read -r -p "Press Enter to continue..." _ </dev/tty || true
  done
}

# Interactive: choose image to pull
choose_and_pull() {
  header "Choose Image to Pull"

  echo -e "${BOLD}SmartLML Backends:${NC}"
  local i=1
  for entry in "${IMAGES[@]}"; do
    local name img desc status_icon
    name=$(get_field "$entry" 1)
    img=$(get_field "$entry" 2)
    desc=$(get_field "$entry" 3)
    if image_exists "$img"; then
      status_icon="${GREEN}●${NC}"
    else
      status_icon="${RED}○${NC}"
    fi
    echo -e "  ${BOLD}${i})${NC} ${status_icon} ${name} ${DIM}— ${desc}${NC}"
    echo -e "     ${DIM}${img}${NC}"
    ((i++))
  done

  echo
  echo -e "${BOLD}Additional Images:${NC}"
  for entry in "${EXTRA_IMAGES[@]}"; do
    local name img desc
    name=$(get_field "$entry" 1)
    img=$(get_field "$entry" 2)
    desc=$(get_field "$entry" 3)
    if image_exists "$img"; then
      status_icon="${GREEN}●${NC}"
    else
      status_icon="${RED}○${NC}"
    fi
    echo -e "  ${BOLD}${i})${NC} ${status_icon} ${name} ${DIM}— ${desc}${NC}"
    echo -e "     ${DIM}${img}${NC}"
    ((i++))
  done

  echo
  read -r -p "Enter number (or image name), or 'b' to go back: " choice </dev/tty || true
  [[ "${choice,,}" == "b" || -z "$choice" ]] && return

  # Combine both arrays for lookup
  local all_entries=("${IMAGES[@]}" "${EXTRA_IMAGES[@]}")

  if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#all_entries[@]}" ]; then
    local entry="${all_entries[$((choice-1))]}"
    local name img
    name=$(get_field "$entry" 1)
    img=$(get_field "$entry" 2)
    echo
    pull_image "$img" "$name"
  elif [[ "$choice" == */* ]]; then
    # Looks like a Docker image name
    pull_image "$choice" "$choice"
  else
    error "Invalid selection: $choice"
  fi
}

# Interactive: choose image to remove
choose_and_remove() {
  header "Choose Image to Remove"

  local installed=()
  local i=1
  for entry in "${IMAGES[@]}"; do
    local name img
    name=$(get_field "$entry" 1)
    img=$(get_field "$entry" 2)
    if image_exists "$img"; then
      local size
      size=$(image_size "$img")
      echo -e "  ${BOLD}${i})${NC} ${name} ${DIM}(${img}, ${size})${NC}"
      installed+=("$entry")
      ((i++))
    fi
  done

  if [ "${#installed[@]}" -eq 0 ]; then
    info "No SmartLML backend images installed"
    return
  fi

  echo
  read -r -p "Enter number to remove, or 'b' to go back: " choice </dev/tty || true
  [[ "${choice,,}" == "b" || -z "$choice" ]] && return

  if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#installed[@]}" ]; then
    local entry="${installed[$((choice-1))]}"
    local name img
    name=$(get_field "$entry" 1)
    img=$(get_field "$entry" 2)
    echo
    read -r -p "Remove ${name} (${img})? [y/N] " confirm </dev/tty || true
    if [[ "${confirm,,}" == "y" ]]; then
      remove_image "$img" "$name"
    else
      info "Cancelled"
    fi
  else
    error "Invalid selection: $choice"
  fi
}

# ─── CLI Argument Handling ───────────────────────────────────────────────

main() {
  check_docker

  local cmd="${1:-}"
  case "$cmd" in
    pull-all|pull_all|pullall)
      pull_all
      ;;
    pull)
      local target="${2:-}"
      if [ -z "$target" ]; then
        choose_and_pull
      elif [[ "$target" =~ ^[0-9]+$ ]] && [ "$target" -ge 1 ] && [ "$target" -le "${#IMAGES[@]}" ]; then
        local entry="${IMAGES[$((target-1))]}"
        pull_image "$(get_field "$entry" 2)" "$(get_field "$entry" 1)"
      else
        pull_image "$target" "$target"
      fi
      ;;
    list|ls)
      list_images
      ;;
    status|info)
      show_status
      ;;
    versions|ver)
      show_versions
      ;;
    remove|rm)
      local target="${2:-}"
      if [ -z "$target" ]; then
        choose_and_remove
      else
        # Try to match by name first
        local found=0
        for entry in "${IMAGES[@]}"; do
          local name img
          name=$(get_field "$entry" 1)
          img=$(get_field "$entry" 2)
          if [[ "${name,,}" == "${target,,}" ]]; then
            remove_image "$img" "$name"
            found=1
            break
          fi
        done
        if [ "$found" -eq 0 ]; then
          remove_image "$target" "$target"
        fi
      fi
      ;;
    clean|prune)
      clean_images
      ;;
    help|--help|-h)
      echo "SmartLML Docker Image Manager"
      echo
      echo "Usage: $0 [command] [args]"
      echo "       GPU_VENDOR=nvidia|rocm|cpu $0 [command]  # Override GPU detection"
      echo
      echo "Commands:"
      echo "  (none)        Interactive menu"
      echo "  pull-all      Pull/update all SmartLML backend images for current GPU"
      echo "  pull [IMAGE]  Pull a specific image (number or full name)"
      echo "  list          List all Docker images on this system"
      echo "  status        Show SmartLML image install status with GPU info"
      echo "  versions      Probe installed images for software version (slower)"
      echo "  remove IMAGE  Remove an image (backend name or full image name)"
      echo "  clean         Remove dangling/unused Docker images"
      echo "  help          Show this help"
      echo
      echo "GPU Modes:"
      echo "  nvidia        NVIDIA GPUs with CUDA (uses --gpus all)"
      echo "  rocm          AMD GPUs with ROCm (uses /dev/kfd, /dev/dri)"
      echo "  cpu           CPU-only inference (no GPU acceleration)"
      echo
      echo "Auto-detected GPU: $(gpu_vendor_name "$(detect_gpu_vendor)")"
      echo "Current GPU mode:  $(gpu_vendor_name "$GPU_VENDOR")"
      echo
      echo "SmartLML Backend Images ($(gpu_vendor_name "$GPU_VENDOR")):"
      local i=1
      for entry in "${IMAGES[@]}"; do
        echo "  ${i}) $(get_field "$entry" 1): $(get_field "$entry" 2)"
        ((i++))
      done
      echo
      echo "Examples:"
      echo "  $0 pull-all                          # Pull all images for detected GPU"
      echo "  $0 pull 1                             # Pull vLLM image"
      echo "  GPU_VENDOR=rocm $0 pull-all          # Pull AMD/ROCm images"
      echo "  GPU_VENDOR=nvidia $0 status          # Show NVIDIA image status"
      echo "  $0 remove ollama                      # Remove Ollama image"
      ;;
    "")
      show_menu
      ;;
    *)
      error "Unknown command: $cmd"
      echo "Run '$0 help' for usage information"
      exit 1
      ;;
  esac
}

main "$@"
