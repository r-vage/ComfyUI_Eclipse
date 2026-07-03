# Prompt styles loading and caching.
# Used by RvText_PromptStyler node and server endpoints.

import csv
import json
import os
import re
import time

from typing import Any, Dict, List, Optional, Tuple, Set, cast
from .logger import log

_LOG_PREFIX = "Style Loader"

# Pre-compiled regex for token detection in style classification
_RE_STYLE_TOKEN = re.compile(r'"[^\"]+"|\'[^\']+\'|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*')

# Cache configuration
_STYLES_CACHE_TTL = 5.0  # seconds - check for file changes every 5 seconds
_styles_cache: Dict[str, Any] = {
    'styles_by_mode': {},
    'names_by_mode': {},
    'last_check': 0.0,
    'last_mtime': 0.0,
    'directory': None
}


def _get_styles_directory_mtime(directory: str) -> float:
    # Get the maximum mtime of style files in the directory.
    # Returns 0.0 if directory doesn't exist or has no style files.
    if not os.path.exists(directory):
        return 0.0
    
    max_mtime = 0.0
    try:
        for filename in os.listdir(directory):
            if filename.endswith(('.json', '.csv', '.txt')):
                filepath = os.path.join(directory, filename)
                if os.path.isfile(filepath):
                    mtime = os.path.getmtime(filepath)
                    if mtime > max_mtime:
                        max_mtime = mtime
    except OSError:
        return 0.0
    
    return max_mtime


def _load_styles_cached(directory: str) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[str]]]:
    # Load styles with TTL-based caching.
    # Only re-reads files if:
    # 1. Cache TTL has expired AND
    # 2. File modification times have changed
    global _styles_cache
    
    now = time.time()
    
    # If within TTL window and same directory, return cached data
    if (_styles_cache['directory'] == directory and 
        _styles_cache['styles_by_mode'] and
        (now - _styles_cache['last_check']) < _STYLES_CACHE_TTL):
        return _styles_cache['styles_by_mode'], _styles_cache['names_by_mode']
    
    # TTL expired - check if files have changed
    current_mtime = _get_styles_directory_mtime(directory)
    _styles_cache['last_check'] = now
    
    # If mtime unchanged and we have cached data, return it
    if (current_mtime == _styles_cache['last_mtime'] and 
        _styles_cache['styles_by_mode'] and
        _styles_cache['directory'] == directory):
        return _styles_cache['styles_by_mode'], _styles_cache['names_by_mode']
    
    # Files changed or first load - reload from disk
    log.debug(_LOG_PREFIX, f"Loading styles from {directory}")
    styles_by_mode, names_by_mode = load_styles_from_directory(directory)
    
    # Update cache
    _styles_cache['styles_by_mode'] = styles_by_mode
    _styles_cache['names_by_mode'] = names_by_mode
    _styles_cache['last_mtime'] = current_mtime
    _styles_cache['directory'] = directory
    
    return styles_by_mode, names_by_mode


def invalidate_styles_cache():
    # Invalidate the styles cache to force reload on next access.
    # Call this when style files are modified programmatically.
    global _styles_cache
    _styles_cache['last_mtime'] = 0.0
    _styles_cache['last_check'] = 0.0


def read_json_file(file_path: str) -> Optional[List[Dict[str, Any]]]:
    # Reads a JSON file's content and returns it.
    # Ensures content matches the expected format.
    if not os.access(file_path, os.R_OK):
        log.warning(_LOG_PREFIX, f"No read permissions for file {file_path}")
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = json.load(file)
            # Check if the content matches the expected format.
            if not isinstance(content, list):
                log.warning(_LOG_PREFIX, f"Invalid content in file {file_path}")
                return None
            items = cast(List[Dict[str, Any]], content)
            if not all(isinstance(item, dict) and 'name' in item and 'prompt' in item and 'negative_prompt' in item for item in items):  # type: ignore
                log.warning(_LOG_PREFIX, f"Invalid content in file {file_path}")
                return None
            return items
    except Exception as e:
        log.error(_LOG_PREFIX, f"An error occurred while reading {file_path}: {str(e)}")
        return None


def read_csv_file(file_path: str) -> Optional[List[Dict[str, Any]]]:
    # Reads a CSV file's content and returns it as a list of dicts.
    # Expected format: name,prompt,negative_prompt
    if not os.access(file_path, os.R_OK):
        log.warning(_LOG_PREFIX, f"No read permissions for file {file_path}")
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            content: List[Dict[str, Any]] = []
            for row in reader:
                # Ensure required fields exist
                name_val = row.get('name')
                prompt_val = row.get('prompt')
                if name_val is not None and prompt_val is not None:
                    content.append({
                        'name': str(name_val).strip('"').strip(),
                        'prompt': str(prompt_val).strip('"').strip() if prompt_val else '{prompt}',
                        'negative_prompt': str(row.get('negative_prompt', '') or '').strip('"').strip()
                    })
            return content if content else None
    except Exception as e:
        log.error(_LOG_PREFIX, f"An error occurred while reading CSV {file_path}: {str(e)}")
        return None


def read_style_file(file_path: str) -> Optional[List[Dict[str, Any]]]:
    # Reads a style file (JSON, CSV, or TXT) and returns the content.
    # TXT files are expected to have CSV format (name,prompt,negative_prompt).
    if file_path.endswith('.json'):
        return read_json_file(file_path)
    elif file_path.endswith('.csv') or file_path.endswith('.txt'):
        return read_csv_file(file_path)
    else:
        log.warning(_LOG_PREFIX, f"Unsupported file format: {file_path}")
        return None


def get_all_style_files(directory: str) -> List[str]:
    # Returns all style files (JSON, CSV, TXT) from the specified directory.
    return [os.path.join(directory, file) for file in os.listdir(directory) 
            if (file.endswith('.json') or file.endswith('.csv') or file.endswith('.txt')) 
            and os.path.isfile(os.path.join(directory, file))]


def detect_style_type(prompt: str) -> Optional[str]:
    # Suffix-first detection (inspect only the part AFTER {prompt} when present).
    #
    # Returns:
    #   - 'tag_based' or 'natural_language' when a decision can be made
    #   - None when the template is explicitly the base placeholder ('{prompt}')
    #     which should be ignored by auto-detection.
    #
    # Rules:
    # - If there's no suffix (nothing after {prompt}) -> 'tag_based'.
    # - If suffix contains punctuation (.,!?), return 'natural_language'.
    # - If suffix contains strong markers ('with', 'featuring', 'depicting', 'showing'), return 'natural_language'.
    # - Otherwise split suffix on commas and count "short" segments (1-4 tokens). If >=50% short -> 'tag_based'.
    # - Fallback: classify by token count threshold (<=4 tokens -> tag_based).
    if not prompt:
        return 'tag_based'

    # If the template is exactly the placeholder, don't attempt to classify it here
    if prompt.strip() == '{prompt}':
        return None

    # Extract suffix only (ignore prefix)
    if '{prompt}' in prompt:
        suffix = prompt.split('{prompt}', 1)[1].strip() if len(prompt.split('{prompt}', 1)) > 1 else ''
    else:
        suffix = ''

    # If no suffix, nothing to classify -> default to tag_based
    if not suffix:
        return 'tag_based'

    s = suffix
    # punctuation is a strong NL signal
    if any(p in s for p in ('.', '!', '?')):
        return 'natural_language'

    s_low = s.lower()
    strong_markers = (' with ', ' featuring ', ' depicting ', ' showing ')
    if any(m in s_low for m in strong_markers):
        return 'natural_language'

    segments = [seg.strip() for seg in s.split(',') if seg.strip()]
    if segments:
        short = 0
        weak_markers = (' and ', ' in ', ' by ', ' for ')
        for seg in segments:
            seg_low = f" {seg.lower()} "
            toks = _RE_STYLE_TOKEN.findall(seg)
            # treat segments with weak markers as NL only when long
            if any(marker in seg_low for marker in weak_markers) and len(toks) > 2:
                continue
            if seg_low.startswith(' and ') and len(toks) > 1:
                continue
            if 1 <= len(toks) <= 4:
                short += 1
        return 'tag_based' if (short / len(segments)) >= 0.5 else 'natural_language'

    # No comma segments: fallback by token count
    toks = _RE_STYLE_TOKEN.findall(s)
    return 'tag_based' if len(toks) <= 4 else 'natural_language'


def load_styles_from_directory(directory: str) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[str]]]:
    # Loads styles from style files in the directory, organized by mode.
    # Files starting with 'tag_based' go to 'tag_based' mode.
    # Files starting with 'natural_lang' go to 'natural_language' mode.
    # Custom files are auto-detected and added to appropriate mode + 'custom' mode.
    # Duplicates are filtered out (existing styles in a mode are not overwritten).
    styles_by_mode: Dict[str, List[Dict[str, Any]]] = {'tag_based': [], 'natural_language': [], 'custom': []}
    names_by_mode: Dict[str, List[str]] = {'tag_based': [], 'natural_language': [], 'custom': []}
    
    all_files = get_all_style_files(directory)
    custom_styles: List[Dict[str, Any]] = []  # Collect custom styles for processing after main files
    
    # First pass: load tag_based and natural_lang files
    for style_file in all_files:
        filename = os.path.basename(style_file)
        style_data = read_style_file(style_file)
        
        if not style_data:
            continue
            
        # Determine which mode this file belongs to
        if filename.startswith('tag_based'):
            mode = 'tag_based'
        elif filename.startswith('natural_lang'):
            mode = 'natural_language'
        else:
            # Custom file - save for later processing
            custom_styles.extend(style_data)
            continue
        
        # Add styles to the appropriate mode. Special-case 'base' entries so they
        # are stored in 'custom' only once and not duplicated across modes.
        seen = set(names_by_mode[mode])
        for item in style_data:
            style_name = item['name']
            prompt = item.get('prompt', '')
            # If this is an explicit base/placeholder entry, move it to custom (once)
            if style_name.strip().lower() == 'base' and (prompt or '').strip() == '{prompt}':
                if 'base' not in names_by_mode['custom']:
                    styles_by_mode['custom'].append(item)
                    names_by_mode['custom'].append('base')
                continue
            if style_name not in seen:
                seen.add(style_name)
                styles_by_mode[mode].append(item)
                names_by_mode[mode].append(style_name)
    
    # Second pass: process custom styles
    # 1. Add all to 'custom' mode
    # 2. Auto-detect type and add to tag_based/natural_language if not duplicate
    custom_seen: Set[str] = set()
    for item in custom_styles:
        style_name = item['name']
        prompt = item.get('prompt', '')
        
        # Add to custom mode (filter duplicates within custom)
        if style_name not in custom_seen:
            custom_seen.add(style_name)
            styles_by_mode['custom'].append(item)
            names_by_mode['custom'].append(style_name)
        
        # Ignore explicit 'base' entries or empty placeholder prompts when auto-detecting
        # These are commonly used to disable a style and should not be auto-categorized
        if (prompt or '').strip() == '{prompt}' or style_name.strip().lower() == 'base':
            continue

        # Auto-detect style type and add to appropriate mode if not duplicate
        detected_mode = detect_style_type(prompt)
        if detected_mode not in ('tag_based', 'natural_language'):
            continue
        if style_name not in names_by_mode[detected_mode]:
            styles_by_mode[detected_mode].append(item.copy())
            names_by_mode[detected_mode].append(style_name)
    
    # Ensure 'base' style exists in ALL modes (pass-through for original prompt)
    # This allows users to select 'base' regardless of which style_mode is active
    base_style = {'name': 'base', 'prompt': '{prompt}', 'negative_prompt': ''}
    for mode in ('tag_based', 'natural_language', 'custom'):
        if 'base' not in names_by_mode[mode]:
            styles_by_mode[mode].insert(0, base_style.copy())
            names_by_mode[mode].insert(0, 'base')
    
    return styles_by_mode, names_by_mode


def validate_json_data(json_data: List[Dict[str, Any]]) -> bool:
    # Validates the structure of the JSON data.
    if not isinstance(json_data, list):  # type: ignore
        return False
    for template in json_data:
        if 'name' not in template or 'prompt' not in template:
            return False
    return True


def find_template_by_name(json_data: List[Dict[str, Any]], template_name: str) -> Optional[Dict[str, Any]]:
    # Returns a template from the JSON data by name or None if not found.
    for template in json_data:
        if template['name'] == template_name:
            return template
    return None


def get_styles_directory() -> str:
    # Get the styles directory path.
    # Uses the repo's styles/ folder directly.
    parent_directory = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    return os.path.join(parent_directory, "styles")


# ============================================================================
# Public API for server endpoints
# ============================================================================

def reload_styles() -> Dict[str, Any]:
    # Reload styles from disk. Call this when style files are modified.
    # Returns a dict with success status and counts.
    invalidate_styles_cache()
    styles_directory = get_styles_directory()
    _, names_by_mode = _load_styles_cached(styles_directory)
    total = sum(len(v) for v in names_by_mode.values())
    return {
        "success": True,
        "total_styles": total,
        "tag_based": len(names_by_mode.get('tag_based', [])),
        "natural_language": len(names_by_mode.get('natural_language', [])),
        "custom": len(names_by_mode.get('custom', []))
    }


def get_styles_for_mode(mode: str) -> List[str]:
    # Get style names for a specific mode.
    styles_directory = get_styles_directory()
    _, names_by_mode = _load_styles_cached(styles_directory)
    return names_by_mode.get(mode, [])


def get_all_styles() -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[str]]]:
    # Get all loaded styles (with caching).
    styles_directory = get_styles_directory()
    return _load_styles_cached(styles_directory)
