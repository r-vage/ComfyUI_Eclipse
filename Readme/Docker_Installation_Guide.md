# Docker Installation & Setup Guide for Windows

A comprehensive guide for setting up Docker on Windows, specifically optimized for running vLLM and AI workloads with ComfyUI Eclipse.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Understanding WSL2](#understanding-wsl2)
3. [Installing WSL2](#installing-wsl2)
4. [Installing Docker Desktop](#installing-docker-desktop)
5. [Docker Desktop Configuration](#docker-desktop-configuration)
6. [NVIDIA GPU Setup](#nvidia-gpu-setup)
7. [Verifying Your Installation](#verifying-your-installation)
8. [Useful Docker Commands](#useful-docker-commands)
9. [Troubleshooting](#troubleshooting)
10. [Performance Tips](#performance-tips)
11. [Native vLLM Setup (Without Docker)](#native-vllm-setup-without-docker)

---

## Prerequisites

Before starting, ensure you have:

- **Windows 10** (version 2004 or higher) or **Windows 11**
- **64-bit processor** with virtualization support (Intel VT-x or AMD-V)
- **At least 8GB RAM** (16GB+ recommended for AI workloads)
- **NVIDIA GPU** with CUDA support (for vLLM acceleration)
- **Administrator access** to your Windows machine

### Check Your Windows Version

Press `Win + R`, type `winver`, and press Enter. You need:
- Windows 10 Build 19041 or higher
- Windows 11 (any version)

---

## Understanding WSL2

### What is WSL?

**Windows Subsystem for Linux (WSL)** allows you to run a Linux environment directly on Windows without a traditional virtual machine or dual-boot setup.

### WSL1 vs WSL2

| Feature | WSL1 | WSL2 |
|---------|------|------|
| Architecture | Translation layer | Real Linux kernel |
| Performance | Slower for Linux syscalls | Near-native Linux performance |
| GPU Support | ❌ No | ✅ Full CUDA support |
| Docker Support | Limited | ✅ Full native support |
| File System | Slower Linux filesystem | Fast Linux filesystem |
| Memory | Shared with Windows | Dedicated (configurable) |

**⚠️ Important**: vLLM Docker requires **WSL2** for GPU acceleration. WSL1 will NOT work.

### Why WSL2 for Docker?

1. **Native Linux Containers**: Docker runs actual Linux containers, not Windows emulation
2. **GPU Passthrough**: NVIDIA GPUs work seamlessly with CUDA
3. **Better Performance**: Near-native Linux performance for AI workloads
4. **Resource Efficiency**: Uses less overhead than traditional VMs

---

## Installing WSL2

### Step 1: Enable WSL

Open **PowerShell as Administrator** and run:

```powershell
wsl --install
```

This command:
- Enables WSL feature
- Enables Virtual Machine Platform
- Downloads and installs Ubuntu (default)
- Sets WSL2 as default

### Step 2: Restart Your Computer

After installation completes, **restart your computer**.

### Step 3: Complete Linux Setup

After restart, Ubuntu will launch automatically. Create a username and password when prompted.

### Step 4: Verify WSL2 is Default

```powershell
wsl --status
```

You should see:
```
Default Distribution: Ubuntu
Default Version: 2
```

### Step 5: Update WSL Kernel (if needed)

```powershell
wsl --update
```

### Setting WSL2 as Default (if not already)

```powershell
wsl --set-default-version 2
```

### Converting Existing WSL1 to WSL2

If you have an existing WSL1 distribution:

```powershell
# List all distributions
wsl --list --verbose

# Convert specific distribution to WSL2
wsl --set-version Ubuntu 2
```

---

## Installing Docker Desktop

### Step 1: Download Docker Desktop

1. Go to [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
2. Click **Download for Windows**
3. Run the installer (`Docker Desktop Installer.exe`)

### Step 2: Installation Options

During installation, ensure these options are **checked**:
- ✅ Use WSL 2 instead of Hyper-V
- ✅ Add shortcut to desktop (optional)

### Step 3: Complete Installation

1. Click **Ok** to start installation
2. Wait for installation to complete
3. Click **Close and restart** when prompted

### Step 4: First Launch

1. Launch Docker Desktop from Start Menu
2. Accept the service agreement
3. Wait for Docker to start (whale icon in system tray turns solid)

---

## Docker Desktop Configuration

### Essential Settings for AI Workloads

Open Docker Desktop → Click ⚙️ **Settings**

#### General Settings

```
✅ Start Docker Desktop when you sign in to Windows
✅ Use the WSL 2 based engine
❌ Send usage statistics (optional - disable for privacy)
```

#### Resources → WSL Integration

This is **critical** for vLLM:

```
✅ Enable integration with my default WSL distro
✅ Ubuntu (or your preferred distro)
```

#### Resources → Advanced (WSL2 Mode)

In WSL2 mode, Docker uses WSL2's resource management. Configure via `.wslconfig`:

1. Open PowerShell and create/edit the config file:

```powershell
notepad "$env:USERPROFILE\.wslconfig"
```

2. Add recommended settings for AI workloads:

```ini
[wsl2]
# Memory - set to 50-75% of your total RAM
memory=32GB

# Processors - leave some for Windows
processors=12

# Swap - useful for large models
swap=16GB

# Localhost forwarding
localhostForwarding=true

# Nested virtualization (if needed)
nestedVirtualization=true

[experimental]
# Enable GPU in WSL2
gpuSupport=true

# Sparse VHD - reclaim disk space automatically
sparseVhd=true
```

3. Restart WSL for changes to take effect:

```powershell
wsl --shutdown
```

#### Docker Engine Settings

Click **Docker Engine** in settings and ensure these options are in your config:

```json
{
  "builder": {
    "gc": {
      "defaultKeepStorage": "20GB",
      "enabled": true
    }
  },
  "experimental": false,
  "features": {
    "buildkit": true
  },
  "default-runtime": "nvidia"
}
```

> **Note**: The `"default-runtime": "nvidia"` line should only be added AFTER installing NVIDIA Container Toolkit.

---

## NVIDIA GPU Setup

### Step 1: Install NVIDIA Drivers (Windows)

1. Go to [NVIDIA Driver Downloads](https://www.nvidia.com/Download/index.aspx)
2. Select your GPU model
3. Download and install the **Game Ready** or **Studio** driver
4. Restart your computer

**Minimum Driver Version**: 525.60.13 or later for CUDA 12.x support

### Step 2: Verify NVIDIA Driver in WSL2

Open WSL2 (Ubuntu) terminal:

```bash
nvidia-smi
```

You should see your GPU listed with driver version and CUDA version.

> **Note**: You do NOT need to install CUDA inside WSL2. The Windows driver provides CUDA support automatically.

### Step 3: Install NVIDIA Container Toolkit

In your WSL2 terminal (Ubuntu):

```bash
# Add NVIDIA package repository
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# Update package list
sudo apt-get update

# Install NVIDIA Container Toolkit
sudo apt-get install -y nvidia-container-toolkit

# Configure Docker to use NVIDIA runtime
sudo nvidia-ctk runtime configure --runtime=docker

# Restart Docker
sudo systemctl restart docker
```

### Step 4: Verify GPU Access in Docker

```bash
docker run --rm --gpus all nvidia/cuda:12.1-base-ubuntu22.04 nvidia-smi
```

You should see the same GPU info as running `nvidia-smi` directly.

---

## Verifying Your Installation

### Quick Verification Checklist

Run these commands to verify everything is working:

```powershell
# 1. Check WSL version
wsl --version

# 2. Check Docker version
docker --version

# 3. Check Docker Compose version
docker compose version

# 4. Check NVIDIA in WSL2
wsl nvidia-smi

# 5. Test GPU in Docker
docker run --rm --gpus all nvidia/cuda:12.1-base-ubuntu22.04 nvidia-smi
```

### Expected Output Summary

| Command | Expected Result |
|---------|-----------------|
| `wsl --version` | WSL version 2.x.x.x |
| `docker --version` | Docker version 24.x or higher |
| `docker compose version` | Docker Compose version v2.x |
| `nvidia-smi` | Shows your GPU with CUDA 12.x |
| Docker GPU test | Shows GPU info inside container |

---

## Useful Docker Commands

### Container Management

```bash
# List running containers
docker ps

# List all containers (including stopped)
docker ps -a

# Stop a container
docker stop <container_id>

# Remove a container
docker rm <container_id>

# Stop and remove all containers
docker stop $(docker ps -aq) && docker rm $(docker ps -aq)
```

### Image Management

```bash
# List downloaded images
docker images

# Remove an image
docker rmi <image_id>

# Remove unused images
docker image prune

# Remove ALL unused images (including tagged)
docker image prune -a
```

### Resource Management

```bash
# Show Docker disk usage
docker system df

# Clean up everything unused
docker system prune

# Nuclear option - clean EVERYTHING (careful!)
docker system prune -a --volumes
```

### Logs and Debugging

```bash
# View container logs
docker logs <container_id>

# Follow logs in real-time
docker logs -f <container_id>

# Execute command in running container
docker exec -it <container_id> bash
```

### vLLM-Specific Commands

```bash
# Pull the vLLM image
docker pull vllm/vllm-openai:latest

# Check vLLM container logs
docker logs $(docker ps -q --filter ancestor=vllm/vllm-openai)

# Stop vLLM container
docker stop $(docker ps -q --filter ancestor=vllm/vllm-openai)
```

---

## Troubleshooting

### WSL2 Issues

#### "WSL 2 requires an update to its kernel component"

```powershell
wsl --update
```

#### "The virtual machine could not be started"

1. Enable virtualization in BIOS (Intel VT-x / AMD-V)
2. Enable Windows features:

```powershell
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
```

3. Restart computer

#### WSL2 Using Too Much Memory

Edit `.wslconfig` (see [Resources → Advanced](#resources--advanced-wsl2-mode)) and set a memory limit:

```ini
[wsl2]
memory=16GB
```

Then restart WSL:
```powershell
wsl --shutdown
```

### Docker Issues

#### "Docker daemon not running"

1. Open Docker Desktop
2. Wait for it to fully start (whale icon turns solid)
3. If stuck, try:
   - Restart Docker Desktop
   - Restart your computer
   - Run as Administrator

#### "Cannot connect to Docker daemon"

```powershell
# Restart Docker service
net stop com.docker.service
net start com.docker.service
```

#### Docker Extremely Slow

1. Ensure WSL2 backend is enabled (not Hyper-V)
2. Check `.wslconfig` memory settings
3. Disable "Use containerd for pulling and storing images" in Docker settings

### GPU Issues

#### "nvidia-smi: command not found" in WSL2

Your NVIDIA driver is outdated or not properly installed:

1. Update Windows NVIDIA driver to latest version
2. Restart computer
3. Try again

#### "could not select device driver with capabilities: [[gpu]]"

NVIDIA Container Toolkit not installed. Follow [NVIDIA GPU Setup](#nvidia-gpu-setup) steps.

#### GPU Not Detected in Container

```bash
# Check if NVIDIA runtime is configured
docker info | grep -i nvidia

# If not showing, reconfigure:
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

#### CUDA Out of Memory

1. Close other GPU-intensive applications
2. Use smaller model or quantized version
3. Check for zombie Docker containers using GPU:

```bash
docker ps -a
docker stop $(docker ps -aq)
```

### vLLM-Specific Issues

#### vLLM Container Exits Immediately

Check logs:
```bash
docker logs $(docker ps -aq --filter ancestor=vllm/vllm-openai | head -1)
```

Common causes:
- Model path incorrect
- Not enough VRAM
- Missing `--gpus all` flag

#### Port Already in Use

```bash
# Find what's using port 8000
netstat -ano | findstr :8000

# Or use a different port in Eclipse settings
```

---

## Performance Tips

### WSL2 Optimization

1. **Store models in WSL2 filesystem** for faster access:
   ```bash
   # Inside WSL2
   mkdir -p ~/models
   # Copy models here instead of /mnt/c/...
   ```

2. **Disable Windows Defender scanning** for WSL directories:
   - Open Windows Security → Virus & threat protection → Manage settings
   - Add exclusion for `%USERPROFILE%\AppData\Local\Packages\*Ubuntu*`

3. **Use sparse VHD** to prevent disk bloat:
   ```ini
   [experimental]
   sparseVhd=true
   ```

### Docker Optimization

1. **Allocate sufficient memory** in `.wslconfig` (50-75% of RAM)

2. **Enable BuildKit** for faster image builds:
   ```bash
   export DOCKER_BUILDKIT=1
   ```

3. **Use Docker volumes** instead of bind mounts for models:
   ```bash
   docker volume create model-cache
   ```

4. **Regular cleanup** to free disk space:
   ```bash
   docker system prune -f
   ```

### vLLM Performance

1. **Use appropriate GPU memory utilization**:
   - `gpu_memory_utilization: 0.85` for single tasks
   - `gpu_memory_utilization: 0.70` if running alongside ComfyUI

2. **Preload models** to avoid cold-start latency

3. **Use tensor parallelism** for multi-GPU setups

4. **Enable continuous batching** for multiple concurrent requests

---

## Quick Reference Card

### Essential Commands

| Task | Command |
|------|---------|
| Start WSL | `wsl` |
| Shutdown WSL | `wsl --shutdown` |
| Check WSL status | `wsl --status` |
| Start Docker | Open Docker Desktop |
| Check Docker status | `docker info` |
| List containers | `docker ps -a` |
| Check GPU | `nvidia-smi` |
| Test GPU in Docker | `docker run --rm --gpus all nvidia/cuda:12.1-base-ubuntu22.04 nvidia-smi` |
| Clean Docker | `docker system prune -a` |

### Important File Locations

| File | Purpose |
|------|---------|
| `%USERPROFILE%\.wslconfig` | WSL2 resource configuration |
| `%APPDATA%\Docker\settings.json` | Docker Desktop settings |
| `\\wsl$\Ubuntu\home\<user>\` | WSL2 home directory |

### Recommended Settings Summary

| Setting | Recommended Value |
|---------|-------------------|
| WSL2 Memory | 50-75% of total RAM |
| WSL2 Processors | Total cores minus 2-4 |
| WSL2 Swap | 8-16GB |
| Docker Runtime | nvidia (default) |
| GPU Memory Utilization | 0.70-0.85 |

---

## Native vLLM Setup (Without Docker)

If you prefer running vLLM natively in WSL2 instead of using Docker, follow these additional steps.

> **Note**: This is for **Linux-native vLLM** on WSL2. Docker is generally easier for beginners, but native installation offers more control and potentially better performance.

### Step 1: Install Python and pip

Open WSL2 terminal (Ubuntu):

```bash
# Update package list
sudo apt update && sudo apt upgrade -y

# Install Python 3.10+ and pip
sudo apt install -y python3 python3-pip python3-venv

# Verify installation
python3 --version
pip3 --version
```

### Step 2: Install CUDA Toolkit in WSL2

Unlike Docker (which bundles CUDA), native vLLM requires CUDA toolkit:

```bash
# Install CUDA keyring
wget https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb

# Update and install CUDA toolkit
sudo apt update
sudo apt install -y cuda-toolkit-12-4

# Add CUDA to PATH (add to ~/.bashrc for persistence)
export PATH=/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH

# Verify CUDA
nvcc --version
```

### Step 3: Create Virtual Environment

Always use a virtual environment for Python projects:

```bash
# Create venv directory
mkdir -p ~/venvs

# Create vLLM virtual environment
python3 -m venv ~/venvs/vllm

# Activate the environment
source ~/venvs/vllm/bin/activate

# Upgrade pip
pip install --upgrade pip
```

### Step 4: Install PyTorch with CUDA

```bash
# Install PyTorch with CUDA 12.4 support
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Verify PyTorch can see GPU
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0)}')"
```

### Step 5: Install vLLM

```bash
# Install vLLM
pip install vllm

# Verify installation
python -c "import vllm; print(f'vLLM version: {vllm.__version__}')"
```

### Step 6: Test vLLM Server

```bash
# Start vLLM server with a small model for testing
python -m vllm.entrypoints.openai.api_server \
    --model microsoft/Phi-3-mini-4k-instruct \
    --trust-remote-code \
    --gpu-memory-utilization 0.8 \
    --port 8000
```

### Creating a Startup Script

Create a convenient script to activate the environment and start vLLM:

```bash
# Create script
cat << 'EOF' > ~/start_vllm.sh
#!/bin/bash
source ~/venvs/vllm/bin/activate
export CUDA_VISIBLE_DEVICES=0
python -m vllm.entrypoints.openai.api_server "$@"
EOF

# Make executable
chmod +x ~/start_vllm.sh

# Usage example:
# ~/start_vllm.sh --model <path_to_model> --port 8000
```

### Native vs Docker Comparison

| Aspect | Native WSL2 | Docker |
|--------|-------------|--------|
| Setup Complexity | Higher | Lower |
| Disk Usage | Lower | Higher (images) |
| Startup Time | Faster | Slower (container init) |
| Updates | Manual pip | Pull new image |
| Isolation | Less | Full container |
| Debugging | Easier | Requires docker exec |
| Performance | Slightly better | Very close |

### Useful WSL2 Commands for Native Setup

```bash
# Check GPU memory usage
nvidia-smi -l 1

# Monitor GPU in real-time
watch -n 1 nvidia-smi

# Check Python packages
pip list | grep -E "torch|vllm|transformers"

# Activate vLLM environment
source ~/venvs/vllm/bin/activate

# Deactivate environment
deactivate
```

---

## Additional Resources

- [Official Docker Documentation](https://docs.docker.com/)
- [WSL Documentation](https://docs.microsoft.com/en-us/windows/wsl/)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/overview.html)
- [vLLM Documentation](https://docs.vllm.ai/)
- [PyTorch Installation](https://pytorch.org/get-started/locally/)
- [CUDA Toolkit for WSL](https://developer.nvidia.com/cuda-downloads?target_os=Linux&target_arch=x86_64&Distribution=WSL-Ubuntu)

---

*Guide created for ComfyUI Eclipse*
*Last updated: February 2026*
