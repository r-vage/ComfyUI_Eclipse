#
# Credits to LAOGOU-666: https://github.com/LAOGOU-666/Comfyui-Memory_Cleanup.git
# improved and adapted for Comfyui_Eclipse
# Windows only — no useful RAM cleanup APIs exist on Linux/macOS.

import psutil
import ctypes
import time
import platform
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "RAM Cleanup"
_IS_WINDOWS = platform.system() == "Windows"

# Import Windows-specific modules only on Windows
if _IS_WINDOWS:
    from ctypes import wintypes


def _get_detailed_memory_info():
    # Get detailed memory information
    memory = psutil.virtual_memory()
    return {
        'total': memory.total / (1024 * 1024),  # MB
        'available': memory.available / (1024 * 1024),  # MB
        'used': memory.used / (1024 * 1024),  # MB
        'percent': memory.percent,
        'free': memory.free / (1024 * 1024)  # MB
    }


def _clear_file_cache():
    # Clear Windows file cache via SetSystemFileCacheSize
    try:
        result = ctypes.windll.kernel32.SetSystemFileCacheSize(-1, -1, 0)
        return result == 0
    except Exception:
        return False


def _clear_process_memory():
    # Clear working set of user processes (safely)
    cleaned_count = 0

    # System processes to avoid (case-insensitive)
    system_processes = {
        'system', 'system idle process', 'svchost.exe', 'csrss.exe', 'wininit.exe',
        'winlogon.exe', 'lsass.exe', 'services.exe', 'smss.exe', 'explorer.exe'
    }

    try:
        for process in psutil.process_iter(['pid', 'name']):
            try:
                process_name = process.info['name']
                if process_name and process_name.lower() in system_processes:
                    continue

                handle = ctypes.windll.kernel32.OpenProcess(
                    wintypes.DWORD(0x001F0FFF),  # PROCESS_ALL_ACCESS
                    wintypes.BOOL(False),
                    wintypes.DWORD(process.info['pid'])
                )

                if handle:
                    result = ctypes.windll.psapi.EmptyWorkingSet(handle)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    if result == 0:  # Success
                        cleaned_count += 1

            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
            except Exception:
                continue

        return cleaned_count

    except Exception:
        return 0


def _clear_working_set():
    # Clear current process working set
    try:
        result = ctypes.windll.kernel32.SetProcessWorkingSetSize(-1, -1, -1)
        return result == 0
    except Exception:
        return False


class RvTools_RAMCleanup(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="RAM Cleanup [Eclipse]",
            display_name="RAM Cleanup",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            description=(
                "Clears system RAM (Windows only). Clears file cache, process working sets, "
                "and DLL working sets. On non-Windows platforms the node passes input through unchanged."
            ),
            inputs=[
                io.AnyType.Input("anything"),
                io.Boolean.Input(
                    "clean_file_cache",
                    default=True,
                    tooltip="Clear Windows filesystem cache via SetSystemFileCacheSize."
                ),
                io.Boolean.Input(
                    "clean_processes",
                    default=True,
                    tooltip="Clear working set memory of user processes via EmptyWorkingSet."
                ),
                io.Boolean.Input(
                    "clean_dlls",
                    default=True,
                    tooltip="Clear current process working set via SetProcessWorkingSetSize."
                ),
                io.Int.Input(
                    "retry_times",
                    default=3,
                    min=1,
                    max=10,
                    step=1,
                    tooltip="Number of cleanup attempts."
                ),
            ],
            outputs=[
                io.AnyType.Output("output"),
            ],
        )

    @classmethod
    def execute(cls, anything, clean_file_cache, clean_processes, clean_dlls, retry_times):
        # Windows-only RAM cleanup — pass through on other platforms
        if not _IS_WINDOWS:
            log.warning(_LOG_PREFIX, "RAM Cleanup is Windows-only. Passing input through unchanged.")
            return io.NodeOutput(anything)

        if retry_times < 1 or retry_times > 10:
            log.warning(_LOG_PREFIX, f"Invalid retry_times value: {retry_times}. Using default of 3.")
            retry_times = 3

        try:
            initial_mem = _get_detailed_memory_info()

            log.msg(_LOG_PREFIX, "=== RAM Cleanup Started ===")
            log.msg(_LOG_PREFIX, f"Initial - Usage: {initial_mem['percent']:.1f}% | Available: {initial_mem['available']:.1f}MB | Total: {initial_mem['total']:.1f}MB")

            total_cleaned_processes = 0
            operations_completed = set()
            attempt_details = []

            for attempt in range(retry_times):
                attempt_operations = []

                # File cache cleanup
                if clean_file_cache:
                    if _clear_file_cache():
                        attempt_operations.append("Cache")
                        operations_completed.add("File Cache")

                # Process memory cleanup
                if clean_processes:
                    cleaned = _clear_process_memory()
                    if cleaned > 0:
                        total_cleaned_processes += cleaned
                        attempt_operations.append(f"Proc({cleaned})")
                        operations_completed.add("Processes")

                # DLL/working set cleanup
                if clean_dlls:
                    if _clear_working_set():
                        attempt_operations.append("DLL")
                        operations_completed.add("DLL Working Set")

                # Store attempt details
                current_mem = _get_detailed_memory_info()
                attempt_details.append({
                    'attempt': attempt + 1,
                    'operations': attempt_operations,
                    'usage': current_mem['percent'],
                    'available': current_mem['available']
                })

                # Brief pause between attempts
                if attempt < retry_times - 1:
                    time.sleep(0.5)

            # Final summary
            final_mem = _get_detailed_memory_info()
            memory_change = final_mem['available'] - initial_mem['available']

            summary_lines = ["\n=== RAM Cleanup Progress ==="]

            for detail in attempt_details:
                ops = ', '.join(detail['operations']) if detail['operations'] else 'None'
                summary_lines.append(
                    f"Attempt {detail['attempt']}/{retry_times}: [{ops}] → "
                    f"Usage: {detail['usage']:.1f}% | Available: {detail['available']:.1f}MB"
                )

            summary_lines.append("\n=== Cleanup Complete ===")
            summary_lines.append(f"Operations: {', '.join(sorted(operations_completed)) if operations_completed else 'None'}")
            summary_lines.append(f"Processes Cleaned: {total_cleaned_processes}")
            summary_lines.append(f"Memory Change: {memory_change:+.1f}MB")
            summary_lines.append(f"Final - Usage: {final_mem['percent']:.1f}% | Available: {final_mem['available']:.1f}MB")

            log.msg(_LOG_PREFIX, "\n".join(summary_lines))

        except Exception as e:
            log.error(_LOG_PREFIX, f"Critical error during RAM cleanup process: {e}")
            import traceback
            traceback.print_exc()

        return io.NodeOutput(anything)