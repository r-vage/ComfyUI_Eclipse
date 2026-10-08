# Integrated upscale backends and a bounded decode/infer/encode pipeline.

import os
import tempfile
from itertools import islice
from pathlib import Path

import folder_paths
import numpy as np
import torch
from comfy import model_management as mm
from comfy import utils
from torch.nn import functional

from .frame_timeline import raise_storage_error
from .logger import log
from .video_helpers import TemporaryVideo
from .video_stream import video_frame_source
from .video_timing import check_media_interrupt, encode_timed_video

_LOG_PREFIX = "UpscaleVideo"
NO_MODELS = "No models found — add an upscale model"
RESIZE_MODES = ["model", "2x", "3x", "4x", "1080p", "2K", "4K", "custom"]


def model_files():
    # Discover without importing TensorRT, touching CUDA, or installing anything.
    result = {}
    for name in folder_paths.get_filename_list("upscale_models"):
        path = folder_paths.get_full_path("upscale_models", name)
        if path:
            result[f"PyTorch: {name}"] = ("pytorch", path)
    root = Path(folder_paths.models_dir) / "tensorrt" / "upscaler"
    if root.is_dir():
        for directory, _dirs, files in os.walk(root):
            for name in sorted(files):
                path = Path(directory) / name
                if path.suffix.lower() in {".trt", ".engine", ".plan"}:
                    result[f"TensorRT: {path.relative_to(root).as_posix()}"] = ("tensorrt", str(path))
    return dict(sorted(result.items()))


def resolve_model(name):
    try:
        return model_files()[name]
    except KeyError as error:
        raise ValueError("Select an installed upscale model. Use models/upscale_models for PyTorch or models/tensorrt/upscaler for TensorRT engines; refresh the model list after adding files.") from error


def model_fingerprint(name):
    _kind, path = resolve_model(name)
    stat = os.stat(path)
    return os.path.realpath(path), stat.st_mtime_ns, stat.st_size


class PyTorchUpscaler:
    def __init__(self, path, width, height, batch_size, device, tile_size):
        from spandrel import MAIN_REGISTRY, ImageModelDescriptor, ModelLoader

        try:
            from spandrel_extra_arches import EXTRA_REGISTRY
        except ImportError:
            pass
        else:
            MAIN_REGISTRY.add(*EXTRA_REGISTRY, ignore_duplicates=True)
        self.model = None
        self.device = torch.device("cpu") if device == "cpu" else mm.get_torch_device()
        self.tile_size = tile_size
        state = utils.load_torch_file(path, safe_load=True)
        if "module.layers.0.residual_group.blocks.0.norm1.weight" in state:
            state = utils.state_dict_prefix_replace(state, {"module.": ""})
        model = ModelLoader().load_from_state_dict(state).eval()
        if not isinstance(model, ImageModelDescriptor) or model.input_channels != 3 or model.output_channels != 3:
            raise ValueError("Choose a single-image RGB upscale model with three input and output channels")
        self.scale = model.scale
        self.model = model
        del state
        try:
            if self.device.type != "cpu":
                required = mm.module_size(model.model) + tile_size ** 2 * 3 * 4 * max(self.scale, 1) * 384
                required += batch_size * width * height * 3 * 4
                mm.free_memory(required, self.device)
            model.to(device=self.device, dtype=torch.float32)
        except BaseException:
            self.close()
            raise

    @torch.inference_mode()
    def upscale(self, images):
        inputs = images.movedim(-1, 1).to(self.device)

        def infer(tile):
            check_media_interrupt()
            return self.model(tile)

        while True:
            try:
                result = utils.tiled_scale(inputs, infer, tile_x=self.tile_size, tile_y=self.tile_size,
                                           overlap=min(32, self.tile_size // 4), upscale_amount=self.scale,
                                           output_device="cpu")
                return result.movedim(1, -1)
            except Exception as error:
                mm.raise_non_oom(error)
                if self.tile_size <= 128:
                    raise
                self.tile_size //= 2
                log.warning(_LOG_PREFIX, f"Reducing PyTorch tile size to {self.tile_size} after an out-of-memory error")

    def close(self):
        if self.model is not None:
            self.model.to("cpu")
            self.model = None


class TensorRTUpscaler:
    def __init__(self, path, width, height, batch_size, device, tile_size):
        del tile_size
        self.context = self.engine = self.runtime = None
        self.stream = None
        self.device = mm.get_torch_device()
        if device == "cpu" or self.device.type != "cuda" or torch.version.hip or not torch.cuda.is_available():
            raise ValueError("TensorRT engines require NVIDIA CUDA. Select a PyTorch model for CPU or other supported devices.")
        try:
            import tensorrt as trt
        except (ImportError, OSError) as error:
            raise RuntimeError("The TensorRT Python library is unavailable. Install a compatible NVIDIA TensorRT runtime or select a PyTorch model; Auto-TensorRT-Upscaler is not required.") from error
        if int(trt.__version__.split(".")[0]) < 10:
            raise ValueError("Video upscaling requires TensorRT 10 or newer for its tensor-address API")
        self.trt = trt
        self.width, self.height = width, height
        self.logger = trt.Logger(trt.Logger.WARNING)
        try:
            with torch.cuda.device(self.device):
                mm.free_memory(os.path.getsize(path) * 4 + 1024 ** 3, self.device)
                trt.init_libnvinfer_plugins(self.logger, "")
                self.runtime = trt.Runtime(self.logger)
                self.engine = self.runtime.deserialize_cuda_engine(Path(path).read_bytes())
                if self.engine is None:
                    raise ValueError("Cannot load TensorRT engine. Rebuild it for this GPU and installed TensorRT version, or select a PyTorch model.")
                names = [self.engine.get_tensor_name(index) for index in range(self.engine.num_io_tensors)]
                inputs = [name for name in names if self.engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT]
                outputs = [name for name in names if self.engine.get_tensor_mode(name) == trt.TensorIOMode.OUTPUT]
                if len(inputs) != 1 or len(outputs) != 1:
                    raise ValueError("TensorRT upscalers must have one RGB NCHW input and one RGB NCHW output")
                self.input_name, self.output_name = inputs[0], outputs[0]
                dtypes = {trt.float32: torch.float32, trt.float16: torch.float16}
                if any(self.engine.get_tensor_dtype(name) not in dtypes
                       or self.engine.get_tensor_location(name) != trt.TensorLocation.DEVICE
                       or self.engine.get_tensor_format(name) != trt.TensorFormat.LINEAR for name in names):
                    raise ValueError("TensorRT upscalers need linear CUDA float32/float16 input and output tensors")
                self.input_dtype = dtypes[self.engine.get_tensor_dtype(self.input_name)]
                self.output_dtype = dtypes[self.engine.get_tensor_dtype(self.output_name)]
                profile = self._select_profile(width, height, batch_size)
                self.context = self.engine.create_execution_context()
                if self.context is None:
                    raise RuntimeError("Cannot allocate TensorRT execution context; reduce GPU memory use")
                # TensorRT adds synchronization overhead on CUDA's default
                # stream. Keep profile changes and batch work on our own stream.
                self.stream = torch.cuda.Stream(device=self.device)
                if not self.context.set_optimization_profile_async(profile, self.stream.cuda_stream):
                    raise ValueError("Could not activate the TensorRT optimization profile")
                output = self._output_shape(batch_size)
                if output[2] % height or output[3] % width or output[2] // height != output[3] // width:
                    raise ValueError("TensorRT model must apply the same integer scale to width and height")
                self.scale = output[2] // height
                self.stream.synchronize()
        except BaseException:
            self.close()
            raise

    def _select_profile(self, width, height, batch_size):
        shape = tuple(self.engine.get_tensor_shape(self.input_name))
        if len(shape) != 4:
            raise ValueError("TensorRT upscalers require NCHW image input")
        for index in range(max(1, self.engine.num_optimization_profiles)):
            bounds = self.engine.get_tensor_profile_shape(self.input_name, index) if -1 in shape else (shape, shape, shape)
            low, _opt, high = (tuple(item) for item in bounds)
            requested = (batch_size, 3, height, width)
            if all(minimum <= actual <= maximum for actual, minimum, maximum in zip(requested, low, high)):
                self.min_batch = low[0]
                return index
        raise ValueError(f"TensorRT engine does not support batch={batch_size}, RGB {width}x{height}. Reduce batch size or video dimensions, select another engine, or use a PyTorch model with tiling.")

    def _output_shape(self, count):
        if not self.context.set_input_shape(self.input_name, (count, 3, self.height, self.width)):
            raise ValueError("TensorRT rejected the input image shape")
        shape = tuple(self.context.get_tensor_shape(self.output_name))
        if len(shape) != 4 or shape[:2] != (count, 3) or min(shape) <= 0:
            raise ValueError("TensorRT output must contain one RGB image per input frame")
        return shape

    @torch.inference_mode()
    def upscale(self, images):
        count = len(images)
        with torch.cuda.device(self.device):
            self.stream.wait_stream(torch.cuda.current_stream(self.device))
            with torch.cuda.stream(self.stream):
                try:
                    inputs = images.movedim(-1, 1).to(device=self.device, dtype=self.input_dtype).contiguous()
                    if count < self.min_batch:
                        # Fixed/minimum-batch engines may pad inference, never the video.
                        inputs = torch.cat((inputs, inputs[-1:].expand(self.min_batch - count, -1, -1, -1)))
                    output = torch.empty(self._output_shape(len(inputs)), device=self.device, dtype=self.output_dtype)
                    if not self.context.set_tensor_address(self.input_name, inputs.data_ptr()) or not self.context.set_tensor_address(self.output_name, output.data_ptr()):
                        raise RuntimeError("Could not bind TensorRT image buffers")
                    if not self.context.execute_async_v3(self.stream.cuda_stream):
                        raise RuntimeError("TensorRT inference failed")
                    # This blocking copy completes inference on the same stream
                    # before returning CPU frames or releasing either buffer.
                    return output[:count].movedim(1, -1).to(device="cpu", dtype=torch.float32)
                except BaseException:
                    # A failed enqueue can still leave GPU work in flight.
                    self.stream.synchronize()
                    raise

    def close(self):
        try:
            if self.stream is not None:
                self.stream.synchronize()
        finally:
            self.context = self.engine = self.runtime = None
            self.stream = None


def create_upscaler(kind, path, width, height, batch_size, device, tile_size):
    cls = TensorRTUpscaler if kind == "tensorrt" else PyTorchUpscaler
    return cls(path, width, height, batch_size, device, tile_size)


def output_dimensions(width, height, scale, resize_to, resize_width, resize_height):
    if resize_to == "model":
        return width * scale, height * scale
    if resize_to in ("2x", "3x", "4x"):
        return width * int(resize_to[0]), height * int(resize_to[0])
    if resize_to in ("1080p", "2K", "4K"):
        long_edge = {"1080p": 1920, "2K": 2560, "4K": 3840}[resize_to]
        ratio = long_edge / max(width, height)
        return max(1, round(width * ratio)), max(1, round(height * ratio))
    if resize_to == "custom" and 1 <= resize_width <= 16384 and 1 <= resize_height <= 16384:
        return resize_width, resize_height
    raise ValueError("Choose a supported resize mode and positive custom dimensions")


def _processed_frames(source, backend, batch_size, width, height):
    iterator = iter(source.frames())
    try:
        while True:
            # Only this bounded list exists; no whole-video tensor or output list.
            frames = list(islice(iterator, batch_size))
            if not frames:
                break
            batch = torch.empty((len(frames), source.height, source.width, 3), dtype=torch.float32)
            for index, frame in enumerate(frames):
                if not isinstance(frame, torch.Tensor):
                    frame = torch.from_numpy(np.ascontiguousarray(frame))
                batch[index].copy_(frame)
                if frame.dtype == torch.uint8:
                    batch[index].div_(255)
            del frames, frame
            check_media_interrupt()
            result = backend.upscale(batch)
            del batch
            if result.ndim != 4 or result.shape[-1] != 3 or result.shape[0] != index + 1:
                raise ValueError("Upscaler changed the frame count or returned non-RGB images")
            if result.shape[1:3] != (height, width):
                result = functional.interpolate(result.movedim(-1, 1), size=(height, width),
                                                mode="bicubic", align_corners=False, antialias=True).movedim(1, -1)
            for frame in result:
                check_media_interrupt()
                # Quantize once to the SDR video representation; the intermediate
                # codec preserves these RGB bytes without chroma subsampling.
                yield frame.detach().clamp(0, 1).mul(255).round().to(device="cpu", dtype=torch.uint8).numpy()
            del frame, result
    finally:
        close = getattr(iterator, "close", None)
        if close:
            close()


def upscale_video(video, model_name, batch_size=2, resize_to="model", resize_width=1920,
                  resize_height=1080, device="auto", tile_size=512):
    if not isinstance(batch_size, int) or not 1 <= batch_size <= 64:
        raise ValueError("Batch size must be between 1 and 64")
    if device not in ("auto", "cpu") or not isinstance(tile_size, int) or not 64 <= tile_size <= 2048:
        raise ValueError("Choose auto/CPU device and a tile size between 64 and 2048")
    kind, model_path = resolve_model(model_name)
    check_media_interrupt()
    source = video_frame_source(video)
    backend = create_upscaler(kind, model_path, source.width, source.height, batch_size, device, tile_size)
    path = None
    try:
        width, height = output_dimensions(source.width, source.height, backend.scale, resize_to, resize_width, resize_height)
        if max(width, height) > 16384:
            raise ValueError("Upscaled video dimensions exceed 16384 pixels")
        # Include input, native model output, resize output, encoder RGB and PCM.
        frame_bytes = batch_size * 3 * (source.width * source.height * 4 * (1 + backend.scale ** 2) + width * height * 5)
        audio_bytes = round(source.timing.duration * 48000) * 2 * 4 if source.has_audio else 0
        if frame_bytes + audio_bytes * 2 > mm.get_free_memory(torch.device("cpu")) * .8:
            raise ValueError("The selected upscale batch and audio exceed the available RAM budget. Reduce batch size or video dimensions.")
        log.msg(_LOG_PREFIX, f"{kind} on {backend.device}: {len(source.timing)} frames, {source.width}x{source.height} → {width}x{height}, batch {batch_size}")
        audio = source.audio()
        directory = folder_paths.get_temp_directory()
        fd, path = tempfile.mkstemp(prefix="EclipseUpscale_", suffix=".mov", dir=directory)
        os.close(fd)
        encode_timed_video(_processed_frames(source, backend, batch_size, width, height), source.timing, audio,
                           path, width=width, height=height, preset="veryfast", lossless=True)
        output = TemporaryVideo(path, preview_compatible=False)
        report = (f"Model: {model_name}\nBackend: {kind}; device: {backend.device}; batch size: {batch_size}\n"
                  f"Frames: {len(source.timing)}; {source.width}x{source.height} → {width}x{height}; "
                  f"duration: {float(source.timing.duration):.9f}s\n"
                  "Source frame timing/order retained; no FPS conversion.\n"
                  f"Audio: {'aligned 48 kHz stereo PCM' if source.has_audio else 'no source soundtrack'}.\n"
                  f"Batch frame storage estimate: {frame_bytes / 2**20:.1f} MiB, excluding model/runtime/decoder overhead.\n"
                  f"Temporary lossless RGB8 video: {os.path.getsize(path) / 2**20:.1f} MiB. Save Video performs the final encode.")
        return output, report
    except BaseException as error:
        if path is not None:
            TemporaryVideo._remove(path)
        raise_storage_error(error, folder_paths.get_temp_directory())
        raise
    finally:
        backend.close()
