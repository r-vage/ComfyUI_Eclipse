import os
import time
import random
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import nodes #type: ignore
from server import PromptServer #type: ignore
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.file_cache import FileListCache
from ..core.logger import log

_LOG_PREFIX = "ReadPromptFiles"

# Some extension must be setting a seed as server-generated seeds were not random. We'll set a new
# seed and use that state going forward.
initial_random_state = random.getstate()
random.seed(datetime.now().timestamp())
eclipse_seed_random_state = random.getstate()
random.setstate(initial_random_state)

def new_random_seed():
    # Gets a new random seed from the eclipse_seed_random_state and resetting the previous state.
    global eclipse_seed_random_state
    prev_random_state = random.getstate()
    random.setstate(eclipse_seed_random_state)
    seed = random.randint(0, 2**64 - 1)
    eclipse_seed_random_state = random.getstate()
    random.setstate(prev_random_state)
    return seed

# Module-level state (replaces instance state)
_last_index = None
_last_final_index = None
_last_output = None
_last_prompt_count = None

def _parse_file_paths(file_paths_text: str) -> List[str]:
    # Parse multiline file paths, handle quoted paths
    if not file_paths_text or not file_paths_text.strip():
        return []
    
    paths = []
    for line in file_paths_text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        
        # Remove quotes if present
        if (line.startswith('"') and line.endswith('"')) or (line.startswith("'") and line.endswith("'")):
            line = line[1:-1]
        
        # Convert to absolute path
        path = Path(line).resolve()
        if path.exists() and path.is_file():
            paths.append(str(path))
        else:
            log.warning(_LOG_PREFIX, f"File not found: {line}")
    
    return paths

def _read_all_prompts(file_paths: List[str]) -> List[str]:
    # Read all lines from all files
    all_lines = []
    
    for file_path in file_paths:
        try:
            log.debug(_LOG_PREFIX, f"Reading file: {file_path}")
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines:
                    line = line.strip()
                    if line:  # Skip empty lines
                        all_lines.append(line)
            log.debug(_LOG_PREFIX, f"Read {len(lines)} lines from {os.path.basename(file_path)}")
        except Exception as e:
            log.error(_LOG_PREFIX, f"Error reading {file_path}: {e}")
            continue
    
    log.debug(_LOG_PREFIX, f"Total prompts loaded: {len(all_lines)}")
    return all_lines

def _get_cached_prompts(file_paths: List[str]) -> List[str]:
    # Generate cache key based on file paths and modification times
    cache_key_parts = []
    for file_path in file_paths:
        try:
            mtime = os.path.getmtime(file_path) if os.path.exists(file_path) else 0
            cache_key_parts.append(f"{file_path}:{mtime}")
        except Exception:
            cache_key_parts.append(f"{file_path}:0")
    
    cache_key = "prompts:" + "|".join(cache_key_parts)
    
    # Check cache
    cached_prompts = FileListCache.get_cached_list(cache_key)
    if cached_prompts is not None:
        log.debug(_LOG_PREFIX, f"Using cached prompts: {len(cached_prompts)} lines")
        return cached_prompts
    
    # Read prompts and cache them
    prompts = _read_all_prompts(file_paths)
    FileListCache.set_cached_list(cache_key, prompts, {"file_paths": file_paths})
    
    return prompts

class RvText_ReadPromptFiles(io.ComfyNode):
    # Read Prompt Files - Load text prompts from multiple files
    #
    # Features:
    # - Multiple file paths (one per line, quoted paths supported)
    # - Index-based prompt selection from all lines
    # - File modification detection and cache invalidation
    # - Index control for increment/decrement/random

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Read Prompt Files [Eclipse]",
            display_name="Read Prompt Files",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input("file_paths", default="", multiline=True, tooltip="File paths, one per line. Quotes are automatically removed."),
                io.Int.Input("index", default=0, min=-4, max=999999, tooltip="Prompt index: 0+ = fixed position, -1 = random, -2 = increment, -3 = decrement, -4 = shuffle (no repeat)"),
                io.Boolean.Input("stop_at_end", default=True, tooltip="Stop workflow when increment reaches end or decrement reaches start. Does not apply to random mode."),
                io.Boolean.Input("log_prompt", default=False, tooltip="Print the selected prompt to console for debugging"),
                io.Int.Input("seed_input", force_input=True, optional=True, tooltip="When connected, special index modes (-1/-2/-3/-4) only advance when this value changes. Keep the same seed to freeze prompt selection while tweaking other workflow settings."),
            ],
            outputs=[
                io.String.Output("prompt"),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo, io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs) -> Optional[float]:
        file_paths = kwargs.get("file_paths", "")
        index = kwargs.get("index", 0)

        # Forces a changed state if we happen to get a special index mode, as if from the API directly.
        if index in (-1, -2, -3, -4):
            # This isn't used, but a different value than previous will force it to be "changed"
            return new_random_seed()
            
        # Parse file paths
        paths = _parse_file_paths(file_paths)
        if not paths:
            return time.time()
        
        # Check modification time of all files
        max_mtime = 0.0
        for file_path in paths:
            try:
                if os.path.exists(file_path):
                    mtime = os.path.getmtime(file_path)
                    max_mtime = max(max_mtime, mtime)
            except Exception:
                pass
        
        # Create a hash that includes both file changes AND file_paths parameter changes
        # This ensures fingerprint_inputs detects when files are added/removed from file_paths
        import hashlib
        
        # Hash the file_paths string to detect when filenames are added/removed
        file_paths_hash = hashlib.md5(file_paths.encode('utf-8')).hexdigest()[:8]
        file_paths_numeric = int(file_paths_hash, 16) * 0.000001  # Convert to small float
        
        # Combine: file modification time + file_paths changes + index changes
        combined_hash = max_mtime + file_paths_numeric + (index * 0.0001)
        
        log.debug(_LOG_PREFIX, f"fingerprint_inputs: index={index}, max_mtime={max_mtime}, file_paths_hash={file_paths_hash}, combined={combined_hash}")
        
        return combined_hash

    @classmethod
    def execute(cls, file_paths: str, index: int, stop_at_end: bool = True, log_prompt: bool = False, seed_input=None) -> io.NodeOutput:
        global _last_index, _last_final_index, _last_output, _last_prompt_count
        import random
        
        # Parse file paths
        parsed_paths = _parse_file_paths(file_paths)
        
        if not parsed_paths:
            log.warning(_LOG_PREFIX, "No valid file paths provided")
            return io.NodeOutput("",)
        
        # Use the index from the widget (JavaScript handles special modes)
        original_index = index
            
        # Handle special index modes (-1, -2, -3, -4) when called from server/API
        if index in (-1, -2, -3, -4):
            if index in (-2, -3, -4):
                log.warning(_LOG_PREFIX, f'Cannot {"increment" if index == -2 else "decrement" if index == -3 else "shuffle"} index from ' +
                     'server, but will generate a new random seed.')

            seed = new_random_seed()
            log.msg(_LOG_PREFIX, f'Server-generated random seed {seed} used for random index selection.')

            # Note: Special index modes are primarily handled by JavaScript frontend
            # This server-side handling is mainly for API/direct calls
        
        log.debug(_LOG_PREFIX, f"Processing {len(parsed_paths)} files with index {index}")
        
        all_prompts = _get_cached_prompts(parsed_paths)
        
        if not all_prompts:
            log.warning(_LOG_PREFIX, "No prompts found in any files")
            return io.NodeOutput("",)
        
        max_index = len(all_prompts) - 1
        
        # Check for out-of-bounds and handle based on stop_at_end setting
        # This handles increment mode reaching past end (index > max_index)
        # and decrement mode reaching before start (index < 0)
        if stop_at_end:
            if original_index == -2 and index > max_index:
                # Increment mode reached the end
                log.msg(_LOG_PREFIX, f"Increment mode reached end of prompts ({max_index + 1} total). Stopping workflow and disabling auto-queue.")
                PromptServer.instance.send_sync("stop-iteration", {})
                nodes.interrupt_processing()
                return io.NodeOutput("",)
            elif original_index == -3 and index < 0:
                # Decrement mode reached the beginning
                log.msg(_LOG_PREFIX, f"Decrement mode reached beginning of prompts. Stopping workflow and disabling auto-queue.")
                PromptServer.instance.send_sync("stop-iteration", {})
                nodes.interrupt_processing()
                return io.NodeOutput("",)
            elif original_index == -4 and index > max_index:
                # Shuffle mode exhausted all prompts
                log.msg(_LOG_PREFIX, f"Shuffle mode used all prompts ({max_index + 1} total). Stopping workflow and disabling auto-queue.")
                PromptServer.instance.send_sync("stop-iteration", {})
                nodes.interrupt_processing()
                return io.NodeOutput("",)
        # Note: When stop_at_end=False, out-of-bounds indices are clamped below
        # This means increment mode will stick at max_index and decrement will stick at 0
        # TODO: Implement bounce behavior (auto-switch -2↔-3 at boundaries) for stop_at_end=False
        
        # Handle index selection based on special modes
        # Special modes (-1, -2, -3) are primarily handled by JavaScript
        # This backend logic is for server-side random generation when needed
        if original_index in (-1, -4):
            # This was a random/shuffle index request - use resolved seed for random index
            if max_index >= 0:
                random.seed(seed)  # seed is defined above in the special modes block
                final_index = random.randint(0, max_index)
                log.debug(_LOG_PREFIX, f"Random seed {seed} selected index {final_index} from range 0-{max_index}")
            else:
                final_index = 0
                log.debug(_LOG_PREFIX, f"No prompts available for random selection, using index 0")
        else:
            # Use the index as-is - JavaScript handles increment/decrement logic
            final_index = index
        
        # Check cache for consistent results with same index combination
        if (_last_index == index and 
            _last_final_index == final_index and 
            _last_output is not None and
            _last_prompt_count == len(all_prompts)):
            log.debug(_LOG_PREFIX, f"Using cached result for index={index}, final_index={final_index}")
            return io.NodeOutput(_last_output,)
        
        # Handle index bounds with proper logging
        if final_index < 0:
            log.debug(_LOG_PREFIX, f"Index {final_index} < 0, clamping to 0")
            final_index = 0
        elif final_index > max_index:
            log.debug(_LOG_PREFIX, f"Index {final_index} > max_index {max_index}, clamping to {max_index}")
            final_index = max_index
        
        log.msg(_LOG_PREFIX, f"Reading line {final_index} from {len(all_prompts)} total lines (index bounds: 0-{max_index})")
        
        # Get the prompt at the specified index
        selected_prompt = all_prompts[final_index]
        
        # Log the prompt if requested
        log.debug(_LOG_PREFIX, f"log_prompt parameter value: {log_prompt} (type: {type(log_prompt)})")
        if log_prompt:
            log.msg(_LOG_PREFIX, f"Selected line {final_index} of {max_index+1}: {selected_prompt}")
        else:
            log.debug(_LOG_PREFIX, f"Selected line {final_index} of {max_index+1}: {selected_prompt[:50]}...")
        
        # Cache the result
        _last_index = index
        _last_final_index = final_index  
        _last_output = selected_prompt
        _last_prompt_count = len(all_prompts)
        
        return io.NodeOutput(selected_prompt,)