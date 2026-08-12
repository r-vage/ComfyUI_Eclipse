# Compatibility re-export. CivitAI acquisition now lives under core.model_loader.

from .model_loader.acquisition import (
    MAX_DOWNLOAD_BYTES,
    MAX_REDIRECTS,
    CivitaiResolvedFile,
    CivitaiSelectionError,
    DownloadCancelled,
    DownloadDestinationBusy,
    cancel_active_download,
    download_file,
    get_model_version,
    get_model_version_by_hash,
    parse_air,
    release_download_id,
    reserve_download_id,
    resolve_civitai_version_filenames,
    resolve_file_for_download,
)

__all__ = [
    "MAX_DOWNLOAD_BYTES",
    "MAX_REDIRECTS",
    "CivitaiResolvedFile",
    "CivitaiSelectionError",
    "DownloadCancelled",
    "DownloadDestinationBusy",
    "cancel_active_download",
    "download_file",
    "get_model_version",
    "get_model_version_by_hash",
    "parse_air",
    "release_download_id",
    "reserve_download_id",
    "resolve_civitai_version_filenames",
    "resolve_file_for_download",
]
