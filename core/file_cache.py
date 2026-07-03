# File list cache for consistent ordering across executions.
# Used by nodes that iterate over files (LoadImageFromFolder, etc.).

from typing import Any, Dict, List, Optional
from .logger import log

_LOG_PREFIX = "FileListCache"


class FileListCache:
    # Cache for file lists to ensure consistent ordering across executions.
    # This prevents the issue where os.listdir/os.walk return files in different
    # orders between calls, causing images to repeat or skip.
    
    _instance = None
    _cache: Dict[str, List[str]] = {}
    _cache_params: Dict[str, Dict[str, Any]] = {}
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def get_cache_key(cls, folder_path: str, include_subfolders: bool, sort_by: str, sort_order: str) -> str:
        # Generate a unique cache key for the given parameters.
        return f"{folder_path}|{include_subfolders}|{sort_by}|{sort_order}"
    
    @classmethod
    def get_cached_list(cls, cache_key: str) -> Optional[List[str]]:
        # Get cached file list if available.
        return cls._cache.get(cache_key)
    
    @classmethod
    def set_cached_list(cls, cache_key: str, file_list: List[str], params: Dict[str, Any]) -> None:
        # Cache a file list.
        cls._cache[cache_key] = file_list.copy()  # Store a copy to prevent modification
        cls._cache_params[cache_key] = params.copy()
    
    @classmethod
    def invalidate(cls, folder_path: Optional[str] = None) -> None:
        # Invalidate cache for a specific folder or all caches.
        if folder_path is None:
            cls._cache.clear()
            cls._cache_params.clear()
            log.msg(_LOG_PREFIX, "File list cache cleared")
        else:
            keys_to_remove = [k for k in cls._cache.keys() if k.startswith(folder_path + "|")]
            for key in keys_to_remove:
                del cls._cache[key]
                if key in cls._cache_params:
                    del cls._cache_params[key]
            if keys_to_remove:
                log.msg(_LOG_PREFIX, f"File list cache cleared for: {folder_path}")
    
    @classmethod
    def get_cache_info(cls, cache_key: str) -> Optional[Dict[str, Any]]:
        # Get info about a cached list.
        if cache_key in cls._cache:
            return {
                "count": len(cls._cache[cache_key]),
                "params": cls._cache_params.get(cache_key, {})
            }
        return None
