# Docker Installation Guide — Linux 🐧

A comprehensive guide to install Docker Engine + NVIDIA GPU support on Linux for use with Eclipse backends (vLLM, SGLang, Ollama, llama.cpp).

> **Recommendation:** For Eclipse, use **Docker Engine** (not Docker Desktop). Docker Engine runs natively on the host, gives direct GPU access via `--gpus all`, and avoids the VM overhead of Docker Desktop. Eclipse manages containers programmatically — no GUI needed.

---

## Table of Contents

1. [Quick Start (TL;DR)](#1-quick-start-tldr)
2. [Prerequisites](#2-prerequisites)
3. [Install Docker Engine](#3-install-docker-engine)
   - [Option A: Convenience Script (Easiest)](#option-a-convenience-script-easiest)
   - [Option B: Official Repository (Recommended for Production)](#option-b-official-repository-recommended-for-production)
   - [Option C: Use the Eclipse Install Script](#option-c-use-the-eclipse-install-script)
4. [Post-Installation Steps](#4-post-installation-steps)
5. [NVIDIA GPU Support](#5-nvidia-gpu-support)
6. [Pull Eclipse Backend Images](#6-pull-eclipse-backend-images)
7. [Configure Eclipse](#7-configure-eclipse)
8. [Verify Everything Works](#8-verify-everything-works)
9. [Docker Desktop (Alternative)](#9-docker-desktop-alternative)
10. [Useful Docker Commands](#10-useful-docker-commands)
11. [Troubleshooting](#11-troubleshooting)
12. [Uninstall Docker](#12-uninstall-docker)

---

## 1) Quick Start (TL;DR)

For most users on Ubuntu/Debian/Fedora, this is all you need:

```bash
# 1. Install Docker Engine (convenience script)
curl -fsSL https://get.docker.com | sudo sh

# 2. Allow your user to run docker without sudo
sudo usermod -aG docker $USER
newgrp docker

# 3. Enable Docker on boot
sudo systemctl enable --now docker

# 4. Install NVIDIA Container Toolkit (if you have an NVIDIA GPU)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 5. Test GPU access
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi

# 6. Pull your preferred backend image
docker pull vllm/vllm-openai:latest     # vLLM
docker pull ollama/ollama                # Ollama
```

> **Note:** For RPM-based distros (Fedora/RHEL/CentOS), replace the `apt-get` NVIDIA toolkit commands with the `dnf` variant shown in [Section 5](#5-nvidia-gpu-support).

---

## 2) Prerequisites

- A **64-bit Linux** distribution (Ubuntu 22.04+, Debian 12+, Fedora 41+, RHEL 8+, CentOS Stream 9+)
- **Root/sudo access**
- **NVIDIA GPU drivers** already installed (verify with `nvidia-smi`)
- **Internet connectivity** for downloading packages and Docker images

### Check your system

```bash
# Verify architecture
uname -m    # Should show x86_64 or aarch64

# Verify NVIDIA drivers (if using GPU)
nvidia-smi  # Should show your GPU and driver version

# Check Linux version
cat /etc/os-release
```

### Uninstall conflicting packages

Your distro may ship unofficial Docker packages. Remove them first:

**Debian/Ubuntu:**
```bash
sudo apt remove docker.io docker-compose docker-doc podman-docker containerd runc 2>/dev/null || true
```

**Fedora/RHEL/CentOS:**
```bash
sudo dnf remove docker docker-client docker-client-latest docker-common \
  docker-latest docker-latest-logrotate docker-logrotate docker-engine 2>/dev/null || true
```

> Existing images/containers/volumes in `/var/lib/docker/` are preserved when uninstalling.

---

## 3) Install Docker Engine

### Option A: Convenience Script (Easiest)

Docker's official convenience script auto-detects your distro and installs everything:

```bash
curl -fsSL https://get.docker.com -o get-docker.sh

# Preview what it will do (optional)
sudo sh ./get-docker.sh --dry-run

# Install
sudo sh ./get-docker.sh
```

**Pros:** One command, works on all supported distros.  
**Cons:** No customization, installs latest stable, not ideal for upgrading.

> After installation, on **RPM-based distros** (Fedora, RHEL, CentOS), Docker doesn't auto-start. Run: `sudo systemctl enable --now docker`

### Option B: Official Repository (Recommended for Production)

#### Ubuntu / Debian

```bash
# 1. Add Docker's official GPG key
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/$(. /etc/os-release; echo "$ID")/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# 2. Add the repository
sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/$(. /etc/os-release; echo "$ID")
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update

# 3. Install Docker Engine
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

> **Derivative distros** (Linux Mint, Kali, etc.): Replace the `Suites` codename with the parent distro's codename (e.g., `bookworm` for Debian-based, `noble` for Ubuntu-based).

#### Fedora

```bash
# 1. Add Docker repository
sudo dnf config-manager addrepo \
  --from-repofile https://download.docker.com/linux/fedora/docker-ce.repo

# 2. Install Docker Engine
sudo dnf install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

# 3. Start and enable
sudo systemctl enable --now docker
```

#### RHEL / CentOS Stream

```bash
# 1. Add Docker repository
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
# For RHEL specifically, use: .../linux/rhel/docker-ce.repo

# 2. Install Docker Engine
sudo dnf install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

# 3. Start and enable
sudo systemctl enable --now docker
```

> **GPG key fingerprint** (if prompted): `060A 61C5 1B55 8A7F 742B 77AA C52F EB6B 621E 9F35`

### Option C: Use the Eclipse Install Script

Eclipse includes an interactive installer in `scripts/install-docker-engine.sh`:

```bash
cd ComfyUI/custom_nodes/comfyui_eclipse/scripts
sudo ./install-docker-engine.sh
```

Features:
- Interactive menu with step-by-step options
- Auto-detects your package manager (apt, dnf, pacman, zypper, apk)
- Installs Docker Engine + NVIDIA Container Toolkit + `pass` for credentials
- `--dry-run` mode to preview actions
- `--all --yes` for non-interactive full install

---

## 4) Post-Installation Steps

### Run Docker without sudo

```bash
# Create docker group (may already exist)
sudo groupadd docker 2>/dev/null || true

# Add your user
sudo usermod -aG docker $USER

# Activate without logout (current terminal only)
newgrp docker

# Verify
docker run hello-world
```

> **Important:** `newgrp docker` only activates the group in the **current terminal session**. To make it permanent across all terminals, applications, and ComfyUI, you must **log out and log back in** (or reboot). This is a one-time requirement after the initial install.

### Enable Docker on boot

```bash
sudo systemctl enable docker.service
sudo systemctl enable containerd.service
```

### Fix permissions (if you previously ran docker with sudo)

```bash
sudo chown "$USER":"$USER" /home/"$USER"/.docker -R 2>/dev/null || true
sudo chmod g+rwx "$HOME/.docker" -R 2>/dev/null || true
```

---

## 5) NVIDIA GPU Support

Eclipse uses `--gpus all` to pass GPUs to containers. This requires the **NVIDIA Container Toolkit**.

### Install NVIDIA Container Toolkit

#### Ubuntu / Debian (apt)

```bash
# 1. Add NVIDIA GPG key and repository
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# 2. Install
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# 3. Configure Docker runtime
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

#### Fedora / RHEL / CentOS (dnf)

```bash
# 1. Add repository
curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
  | sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo

# 2. Install
sudo dnf clean expire-cache
sudo dnf install -y nvidia-container-toolkit

# 3. Configure Docker runtime
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

#### openSUSE / SLES (zypper)

```bash
sudo zypper ar https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo
sudo zypper --gpg-auto-import-keys install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### Verify GPU access in Docker

```bash
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

You should see your GPU listed. If this works, Eclipse can use GPU-accelerated backends.

---

## 6) Pull Eclipse Backend Images

Eclipse auto-pulls images when needed, but you can pre-pull them to avoid delays:

```bash
# Use the Eclipse image management script
cd ComfyUI/custom_nodes/comfyui_eclipse/scripts
./manage-docker-images.sh
```

Or pull manually:

| Backend | Image | Pull Command |
|---------|-------|-------------|
| **vLLM** | `vllm/vllm-openai:latest` | `docker pull vllm/vllm-openai:latest` |
| **SGLang** | `lmsysorg/sglang:latest` | `docker pull lmsysorg/sglang:latest` |
| **Ollama** | `ollama/ollama` | `docker pull ollama/ollama` |
| **llama.cpp** | `ghcr.io/ggml-org/llama.cpp:server-cuda` | `docker pull ghcr.io/ggml-org/llama.cpp:server-cuda` |

### Image sizes

| Image | Approximate Size |
|-------|-----------------|
| vLLM | ~8-12 GB |
| SGLang | ~8-10 GB |
| Ollama | ~1-2 GB (models downloaded separately) |
| llama.cpp (CUDA) | ~2-4 GB |

> **Tip:** Pull images before your first ComfyUI session to avoid long waits. The `manage-docker-images.sh` script handles this interactively.

---

## 7) Configure Eclipse

### docker_config.json

Eclipse's Docker settings live in `comfyui_eclipse/docker_config.json`. Key settings per backend:

```jsonc
{
  "gpu_memory_utilization": 0.6,        // Fraction of VRAM to use (global)
  "trust_remote_code": true,            // Trust remote code for HuggingFace models

  "vllm": {
    "docker_image": "vllm/vllm-openai:latest",
    "url": "http://localhost:8000/v1",
    "port": 8000,
    "startup_timeout": 600,             // Seconds to wait for model loading
    "request_timeout": 300,             // Seconds for generation requests
    "tensor_parallel_size": 1           // Multi-GPU tensor parallelism
  },

  "ollama": {
    "docker_image": "ollama/ollama",
    "url": "http://localhost:11434/v1",
    "port": 11434,
    "auto_pull": true,                  // Auto-download models from registry
    "startup_timeout": 300,
    "request_timeout": 300,
    "pull_timeout": 1800
  }
  // ... sglang, llamacpp sections similar
}
```

### Port reference

| Backend | Default Port | API Base URL |
|---------|-------------|-------------|
| vLLM | 8000 | `http://localhost:8000/v1` |
| SGLang | 30000 | `http://localhost:30000/v1` |
| Ollama | 11434 | `http://localhost:11434/v1` |
| llama.cpp | 8080 | `http://localhost:8080/v1` |

### Key behaviors

- **Auto-start** — Eclipse automatically starts the Docker container when you load a model (always enabled, no config toggle)
- **`auto_stop_container` widget** — Node widget (default: True) that stops the container after inference to free VRAM
- **Image auto-pull** — If the Docker image isn't local, Eclipse pulls it automatically on first use
- **AMD/ROCm auto-detection** — GPU vendor is detected automatically and the correct Docker images are selected

---

## 8) Verify Everything Works

### Step 1: Docker basics

```bash
docker --version          # Docker Engine version
docker info               # Daemon status, driver info
docker compose version    # Compose plugin version
```

### Step 2: GPU access

```bash
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

### Step 3: Quick backend test

```bash
# Test Ollama (lightweight, good first test)
docker run --rm --gpus all -p 11434:11434 ollama/ollama

# In another terminal:
curl http://localhost:11434/api/tags
```

### Step 4: ComfyUI integration

1. Start ComfyUI
2. Add the **Smart Language Model Loader** node
3. Select a template and backend
4. Run — Eclipse handles container lifecycle automatically

---

## 9) Docker Desktop (Alternative)

Docker Desktop is an alternative with a GUI, but we **recommend Docker Engine** for Eclipse because:

| | Docker Engine | Docker Desktop |
|-|--------------|----------------|
| **GPU support** | Direct `--gpus all` | Requires extra config, may not work |
| **Performance** | Native, no VM | Runs inside a VM |
| **Resource usage** | Minimal | ~2 GB RAM for the VM |
| **License** | Free (Apache 2.0) | Free for personal/small business; paid for >250 employees or >$10M revenue |
| **Eclipse compat** | Full | May need manual GPU setup |

If you still want Docker Desktop:

```bash
# Fedora example
curl -LO https://desktop.docker.com/linux/main/amd64/docker-desktop-x86_64.rpm
sudo dnf install ./docker-desktop-x86_64.rpm
systemctl --user start docker-desktop
```

**Known issue:** Docker Desktop on Linux often **cannot see the GPU** even with NVIDIA toolkit installed on the host. This is because Desktop runs containers inside a VM. If you need GPU access, switch to Docker Engine:

```bash
systemctl --user stop docker-desktop
sudo systemctl enable --now docker
```

---

## 10) Useful Docker Commands

### Container management

```bash
docker ps                          # List running containers
docker ps -a                       # List all containers (including stopped)
docker stop <container_id>         # Stop a container
docker start <container_id>        # Start a stopped container
docker rm <container_id>           # Remove a container
docker logs <container_id>         # View container logs
docker logs -f <container_id>      # Follow logs in real-time
```

### Image management

```bash
docker images                      # List local images
docker pull <image>                # Download/update an image
docker rmi <image>                 # Remove an image
docker image prune                 # Remove unused images
docker system prune                # Remove all unused data (containers, images, networks)
```

### Eclipse-specific

```bash
# Find Eclipse containers
docker ps -a --filter "name=eclipse"

# Check vLLM health
curl http://localhost:8000/health

# Check Ollama health
curl http://localhost:11434/api/tags

# Stop all Eclipse containers
docker ps -a --filter "name=eclipse" -q | xargs -r docker stop

# View VRAM usage inside a running container
docker exec <container_id> nvidia-smi
```

### Disk space

```bash
docker system df                   # Show disk usage
docker system prune -a             # Remove ALL unused data (careful!)
```

---

## 11) Troubleshooting

### "permission denied" when running docker

```bash
# Ensure your user is in the docker group
sudo usermod -aG docker $USER
newgrp docker
# Or log out and back in
```

### "Cannot connect to the Docker daemon"

```bash
# Check if Docker is running
sudo systemctl status docker

# Start it
sudo systemctl start docker

# Enable on boot
sudo systemctl enable docker
```

### GPU not visible in containers

```bash
# 1. Verify host GPU works
nvidia-smi

# 2. Check NVIDIA Container Toolkit is installed
nvidia-ctk --version

# 3. Check Docker runtime is configured
cat /etc/docker/daemon.json
# Should contain "nvidia" runtime

# 4. If daemon.json is missing nvidia runtime, reconfigure:
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 5. Test
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

### "could not select device driver" GPU error

This means Docker can't find the NVIDIA runtime. Fix:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### Port already in use

```bash
# Find what's using the port
sudo ss -ltnp | grep :8000

# Kill it or change the port in docker_config.json
```

### Container OOM (Out of Memory)

- Lower `gpu_memory_utilization` in `docker_config.json` (e.g., `0.5` instead of `0.6`)
- Use a smaller model or quantized version
- Reduce `max_model_len` (context size)

### Eclipse container won't start / timeout

- Increase `startup_timeout` in `docker_config.json` (large models need 5-10 minutes)
- Check container logs: `docker logs <container_id>`
- Ensure the model files are complete and not corrupted

### Docker Desktop GPU issues

Docker Desktop on Linux runs a VM that may not pass through GPUs. Switch to Docker Engine:

```bash
systemctl --user stop docker-desktop
sudo systemctl enable --now docker
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

---

## 12) Uninstall Docker

### Docker Engine

```bash
# Debian/Ubuntu
sudo apt purge docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras
sudo rm /etc/apt/sources.list.d/docker.sources
sudo rm /etc/apt/keyrings/docker.asc

# Fedora/RHEL/CentOS
sudo dnf remove docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras

# Remove data (DESTRUCTIVE - removes all images, containers, volumes)
sudo rm -rf /var/lib/docker /var/lib/containerd
```

### NVIDIA Container Toolkit

```bash
# Debian/Ubuntu
sudo apt purge nvidia-container-toolkit
sudo rm /etc/apt/sources.list.d/nvidia-container-toolkit.list

# Fedora/RHEL
sudo dnf remove nvidia-container-toolkit
sudo rm /etc/yum.repos.d/nvidia-container-toolkit.repo
```

### Use the Eclipse removal script

```bash
cd ComfyUI/custom_nodes/comfyui_eclipse/scripts
sudo ./remove-docker-nvidia.sh
```

---

## Appendix: Eclipse Backend Quick Reference

### Docker images used by Eclipse

| Backend | Image | GPU Flag | Port | Health Check |
|---------|-------|----------|------|-------------|
| vLLM | `vllm/vllm-openai:latest` | `--gpus all --ipc=host` | 8000 | `/health` |
| SGLang | `lmsysorg/sglang:latest` | `--gpus all --shm-size 16g` | 30000 | `/health` |
| Ollama | `ollama/ollama` | `--gpus all` | 11434 | `/api/tags` |
| llama.cpp | `ghcr.io/ggml-org/llama.cpp:server-cuda` | `--gpus all` | 8080 | `/health` |

### Container naming convention

| Backend | Container Name Pattern |
|---------|----------------------|
| vLLM | (tracked by ID) |
| SGLang | `sml_sglang_{model_name}` |
| Ollama | `sml-ollama` |
| llama.cpp | `sml-llamacpp-{model_name}` |

### Volume mounts

All backends mount the host model directory to `/models` inside the container. Eclipse handles path mapping automatically.

---

## Extra Resources

- [Docker Engine Docs](https://docs.docker.com/engine/)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- [vLLM GitHub](https://github.com/vllm-project/vllm)
- [SGLang GitHub](https://github.com/sgl-project/sglang)
- [Ollama](https://ollama.com/)
- [llama.cpp](https://github.com/ggerganov/llama.cpp)
