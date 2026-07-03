# Nunchaku Installation Guide for ComfyUI

## Table of Contents
- [What is Nunchaku?](#what-is-nunchaku)
- [Why This Guide?](#why-this-guide)
- [System Requirements](#system-requirements)
- [GPU Compatibility](#gpu-compatibility)
- [Installation Steps](#installation-steps)
- [Verification](#verification)
- [Understanding Performance](#understanding-performance)
- [Troubleshooting](#troubleshooting)
- [Additional Resources](#additional-resources)

---

## What is Nunchaku?

Nunchaku is an advanced optimization system for Flux models in ComfyUI that applies **SVDQuant** technology to dramatically reduce VRAM usage while preserving image quality. It's not just another quantization tool—it represents a paradigm shift in how Flux models can run efficiently on consumer hardware.

### Key Benefits:
- **Lower VRAM Usage**: Run Flux models on GPUs with limited memory
- **Maintained Quality**: Preserves original Flux1-dev quality through intelligent mixed-precision processing
- **Single File Model**: Use the optimized `svdq-int4_r32-flux.1-dev.safetensors` model
- **Extended Features**: Includes PuLID nodes for facial feature control

---

## Why This Guide?

The official Nunchaku PyPI release can be outdated and cause dependency conflicts, especially with `filterpy` and PyTorch versions. This guide uses a **specific development release** that resolves these issues for ComfyUI portable installations.

**Important**: This is a community-driven guide based on extensive troubleshooting ([original article](https://civitai.com/articles/15932/installing-nunchaku-for-comfyui-portable-a-survivors-guide)). Always back up your files before making changes.

---

## System Requirements

### Required Environment:
- **ComfyUI**: Portable installation (with embedded Python)
- **Python**: 3.10, 3.11, 3.12, or 3.13
- **PyTorch**: 2.7.1+, 2.8.0+, or 2.10.0+ (with CUDA 12.x support)
- **GPU**: NVIDIA GPU (see compatibility section below)

### Target Directory Structure:
```
ComfyUI_windows_portable/
├── python_embeded/        # Your Python installation
├── ComfyUI/
│   └── custom_nodes/
│       └── ComfyUI-nunchaku/
└── run_nvidia_gpu.bat     # ComfyUI launcher
```

---

## GPU Compatibility

NVIDIA categorizes GPU compatibility by **architecture**, not just series numbers. Understanding this helps set realistic performance expectations.

### Blackwell Architecture (RTX 50 Series - Future)
**Status**: Expected in upcoming RTX 50 series  
**FP4 Support**: ✅ Native hardware acceleration via 5th-gen Tensor Cores  
**Performance**: Full benefits of FP4-optimized models with maximum speed

### Ada Lovelace (RTX 40 Series) & Ampere (RTX 30 Series)
**Status**: Current generation GPUs  
**Native Support**: FP8, BF16, FP16 (Tensor Cores)  
**FP4 Support**: ⚠️ Emulated (converts to FP16/BF16 for calculation)  
**Performance**: 
- **Primary Benefit**: Significantly lower VRAM consumption
- **Speed**: No major FP4 speed gains (processes via BF16/FP16)
- **Quality**: Excellent via mixed-precision (INT4 for storage + BF16 for critical layers)

**Why svdq-int4_r32-flux.1-dev is Ideal for RTX 30/40 Series:**
- Uses INT4 for model size reduction and VRAM savings
- Integrates BF16 layers for critical processing (fine details, character generation)
- Leverages your GPU's excellent native BF16/FP16 support
- Maintains high visual quality without requiring FP4 hardware
- Enables higher resolutions or batch sizes on limited VRAM

### Older Architectures (RTX 20 Series, GTX 16 Series)
**Status**: Legacy GPUs  
**Support**: Limited or no INT4/FP4 compatibility  
**Processing**: Typically requires FP32 or FP16  
**Recommendation**: Test compatibility; results may vary

---

## Installation Steps

### Step 1: Close ComfyUI
Ensure your ComfyUI application is **completely shut down** before starting installation.

### Step 2: Open Embedded Python Terminal
Navigate to your ComfyUI portable installation's Python directory.

**Example Path:**
```powershell
cd E:\ComfyUI_windows_portable\python_embeded
```

Replace `E:\ComfyUI_windows_portable` with your actual ComfyUI installation path.

### Step 3: Clean Previous Dependencies
Remove any conflicting or outdated packages from previous installation attempts.

```powershell
python.exe -m pip uninstall nunchaku insightface facexlib filterpy diffusers accelerate onnxruntime -y
```

**Note**: You may see "Skipping" messages for packages that aren't installed—this is normal.

### Step 4: Install Nunchaku Development Wheel
This is the **most critical step**. Install the specific pre-built package that matches your PyTorch version and Python version.

**First, check your Python and PyTorch versions:**
```powershell
python.exe --version
python.exe -c "import torch; print(torch.__version__)"
```

**Then install the matching wheel:**

The wheel filename pattern is: `nunchaku-{VERSION}+torch{PYTORCH_VER}-cp{PYTHON_VER}-cp{PYTHON_VER}-win_amd64.whl`
- `cp310` = Python 3.10
- `cp311` = Python 3.11
- `cp312` = Python 3.12
- `cp313` = Python 3.13

**Examples for Python 3.12:**

**For PyTorch 2.10:**
```powershell
python.exe -m pip install https://github.com/nunchaku-tech/nunchaku/releases/download/v1.0.2/nunchaku-1.0.2+torch2.10-cp312-cp312-win_amd64.whl
```

**For PyTorch 2.8:**
```powershell
python.exe -m pip install https://github.com/nunchaku-tech/nunchaku/releases/download/v1.0.1/nunchaku-1.0.1+torch2.8-cp312-cp312-win_amd64.whl
```

**For PyTorch 2.7:**
```powershell
python.exe -m pip install https://github.com/nunchaku-tech/nunchaku/releases/download/v0.3.1dev20250609/nunchaku-0.3.1.dev20250609+torch2.7-cp312-cp312-win_amd64.whl
```

**For other Python versions (3.10, 3.11, 3.13) or newer PyTorch versions:**
- Check the [Nunchaku releases page](https://github.com/nunchaku-tech/nunchaku/releases) for the latest wheels
- Look for files matching: `nunchaku-*+torch{YOUR_PYTORCH}-cp{YOUR_PYTHON}-cp{YOUR_PYTHON}-win_amd64.whl`
- Replace `cp312` with your Python version code (cp310, cp311, cp313)

**Important Notes**:
- Match BOTH your PyTorch version AND Python version to the wheel filename
- `win_amd64` refers to 64-bit Windows, **not AMD CPUs**
- This wheel works correctly on Intel CPUs with 64-bit Windows
- This bypasses common compilation issues

### Step 5: Install facexlib
Required for optional nodes like PuLID (facial features).

```powershell
python.exe -m pip install facexlib
```

### Step 6: Install insightface
Critical dependency for Nunchaku's facial feature processing.

```powershell
python.exe -m pip install insightface
```

### Step 7: Install onnxruntime
Required by insightface to run ONNX models.

```powershell
python.exe -m pip install onnxruntime
```

---

## Verification

### Step 1: Close Terminal
Exit the PowerShell or command prompt window.

### Step 2: Start ComfyUI
Launch ComfyUI using your usual startup script:
- `run_nvidia_gpu.bat`, or
- `run_nvidia_gpu_fast_fp16_accumulation.bat`

### Step 3: Check Console Output
Watch the console window for errors. There should be **no** `ModuleNotFoundError` or `ImportError` messages related to Nunchaku or its dependencies.

✅ **Good Output**: ComfyUI starts without errors  
❌ **Bad Output**: Error messages about missing modules

### Step 4: Verify Nodes in GUI
1. Open the ComfyUI interface
2. Click **"Add Nodes"** (right-click on canvas or use the menu)
3. Look for **Nunchaku** nodes in the node list
4. You should see **9 Nunchaku nodes**, including:
   - NunchakuPulidApply
   - NunchakuPulidLoader
   - Other Nunchaku processing nodes

✅ **Success**: All 9 nodes are visible and can be added to workflows  
❌ **Problem**: Nodes are missing or show errors when added

---

## Understanding Performance

### What to Expect on RTX 30/40 Series

**VRAM Benefits** (Primary Advantage):
- Significantly reduced memory footprint
- Ability to run Flux models on GPUs with 8-12GB VRAM
- Higher resolution generations possible
- Larger batch sizes if VRAM was previously limiting

**Speed Considerations**:
- No major speed improvements from FP4 (requires Blackwell architecture)
- INT4 layers reduce memory transfer overhead (modest speed gain)
- BF16 processing ensures quality remains high
- Overall workflow may feel smoother due to lower VRAM pressure

**Quality**:
- Maintains original Flux1-dev quality
- Mixed-precision approach (INT4 + BF16) preserves fine details
- No visible degradation in character generation, textures, or composition

### Future-Proofing for Blackwell (RTX 50 Series)

When Blackwell GPUs become available:
- Same installation and models will work
- FP4 hardware acceleration will activate automatically
- Expect significant additional speed improvements
- Your existing workflows remain compatible

---

## Troubleshooting

### Installation Issues

**Problem**: `ModuleNotFoundError: No module named 'nunchaku'`  
**Solution**: 
1. Repeat Step 4 (install development wheel)
2. Ensure you're using the embedded Python's pip
3. Verify path: `python.exe -m pip list | findstr nunchaku`

**Problem**: `ImportError: DLL load failed` or similar  
**Solution**:
1. Verify PyTorch version: `python.exe -c "import torch; print(torch.__version__)"`
2. Should show `2.7.x`, `2.8.x`, or `2.10.x` with CUDA support
3. Ensure you installed the matching Nunchaku wheel (2.7 wheel for PyTorch 2.7, 2.8 wheel for PyTorch 2.8, 2.10 wheel for PyTorch 2.10)
4. Reinstall PyTorch or Nunchaku if version mismatch

**Problem**: `filterpy` conflicts  
**Solution**:
1. Uninstall filterpy: `python.exe -m pip uninstall filterpy -y`
2. Reinstall Nunchaku wheel (Step 4)
3. Do NOT manually install filterpy

### Node Issues

**Problem**: Nunchaku nodes don't appear in GUI  
**Solution**:
1. Check ComfyUI custom_nodes folder
2. Verify `ComfyUI-nunchaku` folder exists
3. Restart ComfyUI completely
4. Check console for initialization errors

**Problem**: PuLID nodes missing  
**Solution**:
1. Verify facexlib installation: `python.exe -m pip list | findstr facexlib`
2. Verify insightface installation: `python.exe -m pip list | findstr insightface`
3. Reinstall if missing (Steps 5-7)

### Performance Issues

**Problem**: VRAM usage still high  
**Solution**:
1. Ensure you're using the correct model: `svdq-int4_r32-flux.1-dev.safetensors`
2. Check that Nunchaku nodes are properly connected in workflow
3. Monitor VRAM in Task Manager during generation

**Problem**: Poor image quality  
**Solution**:
1. Verify model file integrity (redownload if needed)
2. Check workflow parameters (sampler settings, steps)
3. Ensure BF16 layers aren't being bypassed

### General Debugging

**Check Installed Packages**:
```powershell
python.exe -m pip list | findstr "nunchaku insightface facexlib onnxruntime"
```

**Check Python Version**:
```powershell
python.exe --version
```
Should show: `Python 3.10.x`, `3.11.x`, `3.12.x`, or `3.13.x`

**Check PyTorch**:
```powershell
python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```
Should show version and `True` for CUDA availability

---

## Additional Resources

### Example Workflows
Nunchaku includes example workflows to help you get started:

**Location**: `ComfyUI_windows_portable\ComfyUI\custom_nodes\ComfyUI-nunchaku\workflows_examples\`

**Usage**:
1. Navigate to the folder
2. Find `.json` workflow files
3. Drag and drop into ComfyUI to load
4. These demonstrate proper node connections and settings

### Future Updates

**Nunchaku Wheel Installer Node**:
- Now included in ComfyUI-Nunchaku
- Can update Nunchaku directly from ComfyUI GUI
- Simplifies future maintenance
- Look for the installer node in your node list

### Related Documentation

For more information about model loading and ComfyUI features:
- [Smart Loaders Guide](Smart_Loaders.md) - Multi-format model loading including Nunchaku GGUF support
- [Checkpoint Loaders Guide](Checkpoint_Loaders.md) - Basic model loading concepts

### External Resources

- **Nunchaku GitHub**: https://github.com/nunchaku-tech/nunchaku
- **Nunchaku Releases**: https://github.com/nunchaku-tech/nunchaku/releases (Find wheels for newer PyTorch versions)
- **SVDQuant Research**: MIT-HAN Lab's optimization technology
- **ComfyUI Documentation**: Official ComfyUI guides

---

## Quick Reference Card

### Installation Commands (Copy-Paste Ready)

**Note**: These examples use Python 3.12 and PyTorch 2.10. Adjust the wheel URL if you have a different Python version (see Step 4 for details).

```powershell
# Navigate to embedded Python
cd E:\ComfyUI_windows_portable\python_embeded

# Check your Python and PyTorch versions
python.exe --version
python.exe -c "import torch; print(torch.__version__)"

# Clean previous installations
python.exe -m pip uninstall nunchaku insightface facexlib filterpy diffusers accelerate onnxruntime -y

# Install Nunchaku (Python 3.12 examples - adjust cpXXX for your Python version)
# For PyTorch 2.10 + Python 3.12:
python.exe -m pip install https://github.com/nunchaku-tech/nunchaku/releases/download/v1.0.2/nunchaku-1.0.2+torch2.10-cp312-cp312-win_amd64.whl

# For PyTorch 2.8 + Python 3.12:
# python.exe -m pip install https://github.com/nunchaku-tech/nunchaku/releases/download/v1.0.1/nunchaku-1.0.1+torch2.8-cp312-cp312-win_amd64.whl

# For PyTorch 2.7 + Python 3.12:
# python.exe -m pip install https://github.com/nunchaku-tech/nunchaku/releases/download/v0.3.1dev20250609/nunchaku-0.3.1.dev20250609+torch2.7-cp312-cp312-win_amd64.whl

# For other Python versions: Replace cp312 with cp310, cp311, or cp313
# Check releases: https://github.com/nunchaku-tech/nunchaku/releases

# Install dependencies
python.exe -m pip install facexlib
python.exe -m pip install insightface
python.exe -m pip install onnxruntime
```

**Remember**: 
- Replace `E:\ComfyUI_windows_portable` with your actual installation path!
- Uncomment the line matching your PyTorch version and comment out the others
- If using Python 3.10, 3.11, or 3.13, replace `cp312` with `cp310`, `cp311`, or `cp313` in the wheel URL

### Verification Checklist

- [ ] ComfyUI starts without ModuleNotFoundError
- [ ] Console shows no Nunchaku-related errors
- [ ] 9 Nunchaku nodes visible in node menu
- [ ] PuLID nodes available (NunchakuPulidApply, NunchakuPulidLoader)
- [ ] Example workflows load successfully

---

**Last Updated**: Based on Nunchaku v1.0.2 (PyTorch 2.10), v1.0.1 (PyTorch 2.8), and v0.3.1dev20250609 (PyTorch 2.7)  
**Tested Environment**: ComfyUI Portable, Python 3.12, PyTorch 2.8 with CUDA 12.x, Windows 64-bit
