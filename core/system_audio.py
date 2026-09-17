"""Process-wide system-output recording lifecycle for Eclipse."""

from __future__ import annotations

import atexit
import hashlib
import importlib
import math
import os
import secrets
import sys
import threading
import time
import wave
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import av  # type: ignore
import folder_paths  # type: ignore
import numpy as np  # type: ignore
import torch  # type: ignore

from .logger import log

_LOG_PREFIX = "SystemAudioRecorder"
SAMPLE_RATE = 48_000
CHANNELS = 2
SAMPLE_WIDTH = 2
CAPTURE_FRAMES = 4_096
CAPTURE_BLOCK_SIZE = 8_192
SCAN_WINDOW_FRAMES = SAMPLE_RATE // 100
TRIM_PREROLL_FRAMES = SAMPLE_RATE // 20
COPY_CHUNK_FRAMES = 65_536
FORMATS = {"wav", "wav + mp3", "mp3"}
BITRATES = {128, 192, 256, 320}
MIN_THRESHOLD_DB = -96.0
MAX_THRESHOLD_DB = -20.0
_STAGING_DIRECTORY = ".eclipse-system-audio-staging"
_FORBIDDEN_PATH_CHARS = set('<>:"|?*')
_MAX_SESSIONS = 16


class SystemAudioError(RuntimeError):
    """A user-facing recorder error with an HTTP-compatible status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ProcessingConfig:
    """Validated settings that uniquely identify a processed output version."""

    filename_prefix: str
    format: str
    bitrate: int
    strip_start_silence: bool
    silence_threshold_db: float


@dataclass(frozen=True)
class ProcessedRecording:
    """Published paths and source range for one processed output version."""

    config: ProcessingConfig
    start_frame: int
    frame_count: int
    wav_relative: str
    mp3_relative: str


@dataclass(frozen=True)
class ConsumedRecording:
    waveform: torch.Tensor
    sample_rate: int
    wav_path: str
    mp3_path: str
    duration: float


@dataclass
class _RecordingSession:
    token: str
    node_id: str
    device_id: str
    capture_part: Path
    source_wav: Path
    state: str = "starting"
    error: str = ""
    started_at: float = field(default_factory=time.monotonic)
    stopped_at: float | None = None
    frame_count: int = 0
    current: ProcessedRecording | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    started_event: threading.Event = field(default_factory=threading.Event)
    ready_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


def _supported_platform_error() -> str | None:
    if sys.platform == "darwin":
        return (
            "System-output loopback recording is unavailable on macOS because "
            "SoundCard does not provide native CoreAudio loopback capture."
        )
    if not (sys.platform.startswith("linux") or sys.platform == "win32"):
        return "System-output recording is supported only on Linux and Windows."
    return None


def _device_key(device: Any) -> str:
    identity = f"{sys.platform}\0{getattr(device, 'id', '')!s}\0{device.name!s}"
    return hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:24]


def _parse_bitrate(value: int | str) -> int:
    try:
        bitrate = int(str(value).lower().replace("kbps", "").strip())
    except (TypeError, ValueError) as error:
        raise SystemAudioError("Invalid MP3 bitrate.") from error
    if bitrate not in BITRATES:
        raise SystemAudioError("MP3 bitrate must be 128, 192, 256, or 320 kbps.")
    return bitrate


def _parse_threshold(value: float | str) -> float:
    if isinstance(value, bool):
        raise SystemAudioError("silence_threshold_db must be a number.")
    try:
        threshold = float(value)
    except (TypeError, ValueError) as error:
        raise SystemAudioError("silence_threshold_db must be a number.") from error
    if not math.isfinite(threshold) or not MIN_THRESHOLD_DB <= threshold <= MAX_THRESHOLD_DB:
        raise SystemAudioError("silence_threshold_db must be between -96 and -20 dBFS.")
    return threshold


def _validate_prefix(value: str) -> str:
    if not isinstance(value, str):
        raise SystemAudioError("filename_prefix must be a string.")
    normalized = value.strip().replace("\\", "/")
    if normalized.lower().endswith((".wav", ".mp3")):
        normalized = normalized.rsplit(".", 1)[0]
    if not normalized or len(normalized) > 240 or "\x00" in normalized:
        raise SystemAudioError("filename_prefix is empty or too long.")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise SystemAudioError("filename_prefix must be a safe output-relative path.")
    if any(any(char in _FORBIDDEN_PATH_CHARS for char in part) for part in candidate.parts):
        raise SystemAudioError("filename_prefix contains unsupported path characters.")
    return candidate.as_posix()


def _processing_config(
    *,
    filename_prefix: str,
    output_format: str,
    bitrate: int | str,
    strip_start_silence: bool,
    silence_threshold_db: float | str,
) -> ProcessingConfig:
    if not isinstance(output_format, str) or output_format not in FORMATS:
        raise SystemAudioError("format must be wav, wav + mp3, or mp3.")
    if not isinstance(strip_start_silence, bool):
        raise SystemAudioError("strip_start_silence must be a boolean.")
    return ProcessingConfig(
        filename_prefix=_validate_prefix(filename_prefix),
        format=output_format,
        bitrate=_parse_bitrate(bitrate),
        strip_start_silence=strip_start_silence,
        silence_threshold_db=_parse_threshold(silence_threshold_db),
    )


def _normalize_stereo(samples: Any) -> np.ndarray:
    data = np.asarray(samples)
    if data.ndim == 1:
        data = data[:, np.newaxis]
    if data.ndim != 2:
        raise ValueError("Capture backend returned an invalid audio block.")
    if data.shape[1] == 0:
        return np.empty((0, CHANNELS), dtype=np.float32)
    if data.shape[1] == 1:
        data = np.repeat(data, CHANNELS, axis=1)
    elif data.shape[1] > CHANNELS:
        data = data[:, :CHANNELS]
    return np.nan_to_num(data, nan=0.0, posinf=1.0, neginf=-1.0).astype(
        np.float32,
        copy=False,
    )


def _validate_source_format(wav_file: wave.Wave_read) -> None:
    if (
        wav_file.getnchannels() != CHANNELS
        or wav_file.getsampwidth() != SAMPLE_WIDTH
        or wav_file.getframerate() != SAMPLE_RATE
    ):
        raise SystemAudioError("The private recording source has an invalid format.", status=500)


def _leading_trim_frame(source_wav: Path, threshold_db: float) -> int:
    """Find the first active 10 ms window and retain 50 ms before it."""
    linear_threshold = 10.0 ** (threshold_db / 20.0)
    with wave.open(str(source_wav), "rb") as wav_file:
        _validate_source_format(wav_file)
        window_start = 0
        while True:
            raw = wav_file.readframes(SCAN_WINDOW_FRAMES)
            if not raw:
                return 0
            samples = np.frombuffer(raw, dtype="<i2")
            if samples.size % CHANNELS:
                raise SystemAudioError("The private recording source is truncated.", status=500)
            channels = samples.reshape(-1, CHANNELS).astype(np.float64) / 32768.0
            rms = np.sqrt(np.mean(np.square(channels), axis=0))
            if bool(np.any(rms >= linear_threshold)):
                return max(0, window_start - TRIM_PREROLL_FRAMES)
            window_start += channels.shape[0]


def _write_wav_range(source_wav: Path, target_wav: Path, start_frame: int) -> int:
    """Stream one source range to a new fixed-format WAV and return its frame count."""
    with wave.open(str(source_wav), "rb") as source:
        _validate_source_format(source)
        total_frames = source.getnframes()
        start_frame = min(max(0, start_frame), total_frames)
        source.setpos(start_frame)
        with wave.open(str(target_wav), "wb") as target:
            target.setnchannels(CHANNELS)
            target.setsampwidth(SAMPLE_WIDTH)
            target.setframerate(SAMPLE_RATE)
            while True:
                raw = source.readframes(COPY_CHUNK_FRAMES)
                if not raw:
                    break
                target.writeframesraw(raw)
    return total_frames - start_frame


def _decode_wav_range(source_wav: Path, start_frame: int) -> torch.Tensor:
    """Decode the selected private-source range to ``[channels, samples]``."""
    chunks: list[torch.Tensor] = []
    with wave.open(str(source_wav), "rb") as source:
        _validate_source_format(source)
        source.setpos(min(max(0, start_frame), source.getnframes()))
        while True:
            raw = source.readframes(COPY_CHUNK_FRAMES)
            if not raw:
                break
            pcm = np.frombuffer(raw, dtype="<i2")
            if pcm.size % CHANNELS:
                raise SystemAudioError("The private recording source is truncated.", status=500)
            chunk = torch.from_numpy(pcm.reshape(-1, CHANNELS).copy()).t()
            chunks.append(chunk.float() / 32768.0)
    if not chunks:
        return torch.empty((CHANNELS, 0), dtype=torch.float32)
    return torch.cat(chunks, dim=1)


def _encode_mp3(source_wav: Path, target_part: Path, bitrate: int) -> None:
    """Encode the fixed-rate processing WAV with PyAV/libmp3lame."""
    try:
        av.Codec("libmp3lame", "w")
    except Exception as error:
        raise SystemAudioError(
            "PyAV was built without the libmp3lame encoder; choose WAV output.",
            status=503,
        ) from error

    with av.open(str(source_wav)) as input_container, av.open(
        str(target_part),
        mode="w",
        format="mp3",
    ) as output_container:
        if not input_container.streams.audio:
            raise SystemAudioError("The processed WAV has no audio stream.", status=500)
        source_stream = input_container.streams.audio[0]
        output_stream = output_container.add_stream("libmp3lame", rate=SAMPLE_RATE)
        output_stream.bit_rate = bitrate * 1000
        output_stream.layout = "stereo"
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=SAMPLE_RATE)
        for source_frame in input_container.decode(streams=source_stream.index):
            source_frame.pts = None
            for frame in resampler.resample(source_frame):
                for packet in output_stream.encode(frame):
                    output_container.mux(packet)
        for frame in resampler.resample(None):
            for packet in output_stream.encode(frame):
                output_container.mux(packet)
        for packet in output_stream.encode(None):
            output_container.mux(packet)


class SystemAudioRecorderManager:
    """Own the singleton capture and retained private recording sources."""

    def __init__(
        self,
        *,
        backend_loader: Callable[[], Any] | None = None,
        output_directory: Callable[[], str] | None = None,
        mp3_encoder: Callable[[Path, Path, int], None] | None = None,
    ) -> None:
        self._backend_loader = backend_loader or (
            lambda: importlib.import_module("soundcard")
        )
        self._output_directory = output_directory or folder_paths.get_output_directory
        self._mp3_encoder = mp3_encoder or _encode_mp3
        self._lock = threading.RLock()
        self._action_lock = threading.Lock()
        self._sessions: OrderedDict[str, _RecordingSession] = OrderedDict()
        self._active_token: str | None = None

    def _backend(self) -> Any:
        unsupported = _supported_platform_error()
        if unsupported:
            raise SystemAudioError(unsupported, status=501)
        try:
            return self._backend_loader()
        except (ImportError, OSError) as error:
            raise SystemAudioError(
                "SoundCard is unavailable. Install Eclipse dependencies and restart ComfyUI.",
                status=503,
            ) from error

    def list_devices(self) -> dict[str, Any]:
        backend = self._backend()
        try:
            default_speaker = backend.default_speaker()
            speakers = list(backend.all_speakers())
        except Exception as error:
            raise SystemAudioError(
                f"Could not enumerate system output devices: {error}", status=503
            ) from error
        devices = [
            {
                "id": "default",
                "name": f"Default ({default_speaker.name})",
                "is_default": True,
            }
        ]
        seen = {"default"}
        for speaker in speakers:
            device_id = _device_key(speaker)
            if device_id in seen:
                continue
            seen.add(device_id)
            devices.append(
                {"id": device_id, "name": str(speaker.name), "is_default": False}
            )
        return {"supported": True, "devices": devices}

    def _resolve_loopback(self, backend: Any, device_id: str) -> Any:
        try:
            speakers = list(backend.all_speakers())
            speaker = backend.default_speaker() if device_id == "default" else next(
                (item for item in speakers if _device_key(item) == device_id),
                None,
            )
        except Exception as error:
            raise SystemAudioError(f"Could not open the output device: {error}") from error
        if speaker is None:
            raise SystemAudioError("The selected output device is no longer available.")

        candidates: list[Any] = []
        try:
            candidate = backend.get_microphone(
                getattr(speaker, "id", speaker.name), include_loopback=True
            )
            candidates.append(candidate)
        except Exception as error:  # noqa: BLE001 - optional discovery fallback
            log.debug(_LOG_PREFIX, f"Direct loopback lookup failed: {type(error).__name__}")
        try:
            candidates.extend(backend.all_microphones(include_loopback=True))
        except Exception as error:  # noqa: BLE001 - primary candidate may still work
            log.debug(_LOG_PREFIX, f"Loopback enumeration failed: {type(error).__name__}")

        speaker_id = str(getattr(speaker, "id", ""))
        speaker_name = str(speaker.name).casefold()
        loopbacks = [item for item in candidates if bool(getattr(item, "isloopback", False))]
        for microphone in loopbacks:
            microphone_id = str(getattr(microphone, "id", ""))
            microphone_name = str(getattr(microphone, "name", "")).casefold()
            if (
                microphone_id == speaker_id
                or (speaker_id and speaker_id in microphone_id)
                or (microphone_id and microphone_id in speaker_id)
                or speaker_name == microphone_name
                or speaker_name in microphone_name
                or microphone_name in speaker_name
            ):
                return microphone
        raise SystemAudioError("No loopback monitor was found for the selected output device.")

    def _staging_directory(self) -> Path:
        output_root = Path(self._output_directory()).resolve(strict=False)
        output_root.mkdir(parents=True, exist_ok=True)
        staging = output_root / _STAGING_DIRECTORY
        staging.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            staging.chmod(0o700)
        except OSError:
            pass
        return staging

    def _paths_for_session(self, token: str) -> tuple[Path, Path]:
        staging = self._staging_directory()
        return staging / f"{token}.capturing.wav.part", staging / f"{token}.source.wav"

    @staticmethod
    def _check_mp3_encoder(config: ProcessingConfig) -> None:
        if "mp3" not in config.format:
            return
        try:
            av.Codec("libmp3lame", "w")
        except Exception as error:
            raise SystemAudioError(
                "PyAV was built without the libmp3lame encoder; choose WAV output.",
                status=503,
            ) from error

    def start(
        self,
        *,
        node_id: str,
        filename_prefix: str,
        output_format: str,
        bitrate: int | str,
        device_id: str,
        strip_start_silence: bool,
        silence_threshold_db: float | str,
    ) -> dict[str, Any]:
        node_id = str(node_id).strip()
        if not node_id or len(node_id) > 128:
            raise SystemAudioError("A valid node_id is required.")
        config = _processing_config(
            filename_prefix=filename_prefix,
            output_format=output_format,
            bitrate=bitrate,
            strip_start_silence=strip_start_silence,
            silence_threshold_db=silence_threshold_db,
        )
        if not isinstance(device_id, str) or not device_id or len(device_id) > 64:
            raise SystemAudioError("Invalid output device.")
        backend = self._backend()
        self._check_mp3_encoder(config)

        with self._action_lock, self._lock:
            if self._active_token is not None:
                raise SystemAudioError(
                    "Another system-audio recording is already active.", status=409
                )
            replaced = [
                session
                for session in self._sessions.values()
                if secrets.compare_digest(session.node_id, node_id)
            ]
            for old_session in replaced:
                self._sessions.pop(old_session.token, None)
                self._cleanup_private(old_session)
            token = secrets.token_urlsafe(32)
            capture_part, source_wav = self._paths_for_session(token)
            session = _RecordingSession(
                token=token,
                node_id=node_id,
                device_id=device_id,
                capture_part=capture_part,
                source_wav=source_wav,
            )
            self._sessions[token] = session
            self._active_token = token
            self._trim_history()

        session.thread = threading.Thread(
            target=self._capture_worker,
            args=(session, backend),
            name="EclipseSystemAudioCapture",
            daemon=True,
        )
        session.thread.start()
        if not session.started_event.wait(timeout=10.0):
            self.abort(node_id=node_id, token=token)
            raise SystemAudioError("Timed out while opening the output device.", status=503)
        if session.error:
            raise SystemAudioError(session.error, status=503)
        status = self.status(node_id=node_id, token=token)
        status["token"] = token
        return status

    def _capture_worker(self, session: _RecordingSession, backend: Any) -> None:
        try:
            loopback = self._resolve_loopback(backend, session.device_id)
            with wave.open(str(session.capture_part), "wb") as wav_file:
                wav_file.setnchannels(CHANNELS)
                wav_file.setsampwidth(SAMPLE_WIDTH)
                wav_file.setframerate(SAMPLE_RATE)
                with loopback.recorder(
                    samplerate=SAMPLE_RATE,
                    blocksize=CAPTURE_BLOCK_SIZE,
                ) as recorder:
                    with self._lock:
                        session.state = "recording"
                    session.started_event.set()
                    while True:
                        block = _normalize_stereo(recorder.record(numframes=CAPTURE_FRAMES))
                        if block.shape[0] == 0:
                            if session.stop_event.is_set():
                                break
                            time.sleep(0.01)
                            continue
                        pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2", copy=False)
                        wav_file.writeframesraw(pcm.tobytes())
                        session.frame_count += int(pcm.shape[0])
                        if session.stop_event.is_set():
                            break
            if session.state != "aborted":
                os.replace(session.capture_part, session.source_wav)
                with self._lock:
                    session.state = "captured"
        except Exception as error:  # noqa: BLE001 - capture-thread boundary
            message = f"System audio capture failed: {error}"
            log.error(_LOG_PREFIX, f"Capture failed: {type(error).__name__}")
            with self._lock:
                session.error = message
                session.state = "error"
                session.stopped_at = time.monotonic()
                if self._active_token == session.token:
                    self._active_token = None
            self._cleanup_private(session)
            session.ready_event.set()
        finally:
            session.started_event.set()
            if session.state == "aborted":
                self._cleanup_private(session)

    def _reserve_output_paths(
        self, config: ProcessingConfig
    ) -> tuple[Path | None, Path | None, str, str]:
        output_root = Path(self._output_directory()).resolve(strict=False)
        prefix = PurePosixPath(config.filename_prefix)
        destination_dir = (output_root.joinpath(*prefix.parts[:-1])).resolve(strict=False)
        try:
            if os.path.commonpath((str(output_root), str(destination_dir))) != str(output_root):
                raise ValueError
        except ValueError as error:
            raise SystemAudioError("filename_prefix escapes ComfyUI's output directory.") from error
        destination_dir.mkdir(parents=True, exist_ok=True)
        if os.path.commonpath(
            (str(output_root), str(destination_dir.resolve(strict=False)))
        ) != str(output_root):
            raise SystemAudioError("filename_prefix resolves outside the output directory.")

        for counter in range(1, 1_000_000):
            stem = f"{prefix.name}_{counter:05d}"
            wav_path = destination_dir / f"{stem}.wav" if "wav" in config.format else None
            mp3_path = destination_dir / f"{stem}.mp3" if "mp3" in config.format else None
            if (wav_path is None or not wav_path.exists()) and (
                mp3_path is None or not mp3_path.exists()
            ):
                wav_relative = wav_path.relative_to(output_root).as_posix() if wav_path else ""
                mp3_relative = mp3_path.relative_to(output_root).as_posix() if mp3_path else ""
                return wav_path, mp3_path, wav_relative, mp3_relative
        raise SystemAudioError("Could not allocate a collision-free output filename.")

    def _prepare_output(
        self, session: _RecordingSession, config: ProcessingConfig
    ) -> ProcessedRecording:
        if session.current is not None and session.current.config == config:
            return session.current
        if not session.source_wav.is_file():
            raise SystemAudioError("The private recording source is unavailable.", status=409)
        self._check_mp3_encoder(config)

        start_frame = (
            _leading_trim_frame(session.source_wav, config.silence_threshold_db)
            if config.strip_start_silence
            else 0
        )
        nonce = secrets.token_hex(8)
        staging = self._staging_directory()
        stage_wav = staging / f"{session.token}.{nonce}.wav.part"
        stage_mp3 = staging / f"{session.token}.{nonce}.mp3.part"
        published: list[Path] = []
        try:
            frame_count = _write_wav_range(session.source_wav, stage_wav, start_frame)
            if "mp3" in config.format:
                self._mp3_encoder(stage_wav, stage_mp3, config.bitrate)
            wav_path, mp3_path, wav_relative, mp3_relative = self._reserve_output_paths(config)
            if mp3_path is not None:
                os.replace(stage_mp3, mp3_path)
                published.append(mp3_path)
            if wav_path is not None:
                os.replace(stage_wav, wav_path)
                published.append(wav_path)
            result = ProcessedRecording(
                config=config,
                start_frame=start_frame,
                frame_count=frame_count,
                wav_relative=wav_relative,
                mp3_relative=mp3_relative,
            )
        except Exception as error:
            for path in published:
                self._remove_file(path)
            if isinstance(error, SystemAudioError):
                raise
            raise SystemAudioError(f"Could not process the recording: {error}", status=500) from error
        finally:
            self._remove_file(stage_mp3)
            self._remove_file(stage_wav)

        with self._lock:
            session.current = result
            session.state = "ready"
            session.error = ""
            if session.stopped_at is None:
                session.stopped_at = time.monotonic()
            if self._active_token == session.token:
                self._active_token = None
        session.ready_event.set()
        return result

    def stop(
        self,
        *,
        node_id: str,
        token: str,
        filename_prefix: str,
        output_format: str,
        bitrate: int | str,
        strip_start_silence: bool,
        silence_threshold_db: float | str,
    ) -> dict[str, Any]:
        config = _processing_config(
            filename_prefix=filename_prefix,
            output_format=output_format,
            bitrate=bitrate,
            strip_start_silence=strip_start_silence,
            silence_threshold_db=silence_threshold_db,
        )
        with self._action_lock:
            session = self._owned_session(node_id=node_id, token=token)
            if session.state == "ready":
                if session.current is None or session.current.config != config:
                    raise SystemAudioError("Use Update Output to change a finished recording.", status=409)
                return self._status(session)
            if session.state in {"aborted", "error"}:
                raise SystemAudioError(session.error or "The recording is unavailable.", status=409)
            with self._lock:
                session.state = "stopping"
            session.stop_event.set()
            if session.thread is not None:
                session.thread.join(timeout=15.0)
                if session.thread.is_alive():
                    raise SystemAudioError(
                        "The audio backend did not stop within 15 seconds.", status=503
                    )
            if session.error:
                raise SystemAudioError(session.error, status=500)
            try:
                self._prepare_output(session, config)
            except Exception as error:
                with self._lock:
                    session.error = str(error)
                    session.state = "processing_error"
                    session.stopped_at = time.monotonic()
                    if self._active_token == session.token:
                        self._active_token = None
                session.ready_event.set()
                raise
            return self._status(session)

    def reprocess(
        self,
        *,
        node_id: str,
        token: str,
        filename_prefix: str,
        output_format: str,
        bitrate: int | str,
        strip_start_silence: bool,
        silence_threshold_db: float | str,
    ) -> dict[str, Any]:
        config = _processing_config(
            filename_prefix=filename_prefix,
            output_format=output_format,
            bitrate=bitrate,
            strip_start_silence=strip_start_silence,
            silence_threshold_db=silence_threshold_db,
        )
        with self._action_lock:
            session = self._owned_session(node_id=node_id, token=token)
            if session.state not in {"ready", "processing_error"}:
                raise SystemAudioError("The recording is not ready to reprocess.", status=409)
            self._prepare_output(session, config)
            return self._status(session)

    def abort(self, *, node_id: str, token: str) -> dict[str, Any]:
        with self._action_lock:
            session = self._owned_session(node_id=node_id, token=token)
            if session.state == "aborted":
                return self._status(session)
            session.stop_event.set()
            with self._lock:
                session.state = "aborted"
            if session.thread is not None and session.thread.is_alive():
                session.thread.join(timeout=10.0)
            self._cleanup_private(session)
            with self._lock:
                session.stopped_at = time.monotonic()
                if self._active_token == session.token:
                    self._active_token = None
            session.ready_event.set()
            return self._status(session)

    def release(self, *, node_id: str, token: str) -> dict[str, Any]:
        """Forget one retained source while preserving every published file."""
        with self._action_lock:
            session = self._owned_session(node_id=node_id, token=token)
            session.stop_event.set()
            with self._lock:
                session.state = "released"
            if session.thread is not None and session.thread.is_alive():
                session.thread.join(timeout=10.0)
            self._cleanup_private(session)
            with self._lock:
                self._sessions.pop(token, None)
                if self._active_token == token:
                    self._active_token = None
            return {"success": True, "state": "released"}

    def wait_and_consume(
        self,
        *,
        node_id: str,
        token: str,
        filename_prefix: str,
        output_format: str,
        bitrate: int | str,
        strip_start_silence: bool,
        silence_threshold_db: float | str,
        interrupt_check: Callable[[], None],
    ) -> ConsumedRecording:
        config = _processing_config(
            filename_prefix=filename_prefix,
            output_format=output_format,
            bitrate=bitrate,
            strip_start_silence=strip_start_silence,
            silence_threshold_db=silence_threshold_db,
        )
        session = self._owned_session(node_id=node_id, token=token)
        try:
            while not session.ready_event.wait(timeout=0.1):
                interrupt_check()
            interrupt_check()
        except BaseException:
            if session.state in {"starting", "recording", "stopping", "captured"}:
                self.abort(node_id=node_id, token=token)
            raise
        if session.state == "aborted":
            raise SystemAudioError("The recording was aborted.", status=409)
        if session.error or session.state == "error":
            raise SystemAudioError(session.error or "The recording failed.", status=500)
        current = session.current
        if current is None or current.config != config:
            raise SystemAudioError(
                "The requested output configuration has not been prepared. Use Update Output.",
                status=409,
            )
        waveform = _decode_wav_range(session.source_wav, current.start_frame)
        return ConsumedRecording(
            waveform=waveform,
            sample_rate=SAMPLE_RATE,
            wav_path=current.wav_relative,
            mp3_path=current.mp3_relative,
            duration=float(current.frame_count) / float(SAMPLE_RATE),
        )

    def status(self, *, node_id: str, token: str) -> dict[str, Any]:
        return self._status(self._owned_session(node_id=node_id, token=token))

    @staticmethod
    def _status(session: _RecordingSession) -> dict[str, Any]:
        ended = session.stopped_at or time.monotonic()
        current = session.current
        return {
            "success": True,
            "state": session.state,
            "recording": session.state in {"starting", "recording", "stopping", "captured"},
            "elapsed": max(0.0, ended - session.started_at),
            "duration": (
                float(current.frame_count) / float(SAMPLE_RATE)
                if current is not None
                else float(session.frame_count) / float(SAMPLE_RATE)
            ),
            "error": session.error,
            "wav_path": current.wav_relative if current is not None else "",
            "mp3_path": current.mp3_relative if current is not None else "",
        }

    def _owned_session(self, *, node_id: str, token: str) -> _RecordingSession:
        if not isinstance(token, str) or not token or len(token) > 128:
            raise SystemAudioError("A valid recording token is required.")
        node_id = str(node_id).strip()
        if not node_id or len(node_id) > 128:
            raise SystemAudioError("A valid node_id is required.")
        with self._lock:
            session = self._sessions.get(token)
        if session is None or not secrets.compare_digest(session.token, token):
            raise SystemAudioError("Recording session not found.", status=404)
        if not secrets.compare_digest(session.node_id, node_id):
            raise SystemAudioError("Recording token does not belong to this node.", status=403)
        return session

    def _cleanup_private(self, session: _RecordingSession) -> None:
        self._remove_file(session.capture_part)
        self._remove_file(session.source_wav)
        staging = session.source_wav.parent
        for path in staging.glob(f"{session.token}.*.part"):
            self._remove_file(path)

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _trim_history(self) -> None:
        while len(self._sessions) > _MAX_SESSIONS:
            removable = next(
                (
                    (token, session)
                    for token, session in self._sessions.items()
                    if token != self._active_token
                    and session.state
                    in {"ready", "processing_error", "aborted", "error", "released"}
                ),
                None,
            )
            if removable is None:
                break
            token, session = removable
            self._sessions.pop(token, None)
            self._cleanup_private(session)

    def shutdown(self) -> None:
        """Stop active capture and remove every retained private source."""
        with self._lock:
            sessions = list(self._sessions.values())
            for session in sessions:
                session.stop_event.set()
                session.state = "released"
        for session in sessions:
            if session.thread is not None and session.thread.is_alive():
                session.thread.join(timeout=5.0)
            self._cleanup_private(session)
        with self._lock:
            self._sessions.clear()
            self._active_token = None


system_audio_recorder = SystemAudioRecorderManager()
atexit.register(system_audio_recorder.shutdown)
