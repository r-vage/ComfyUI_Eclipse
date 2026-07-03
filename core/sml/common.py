import re
import ipaddress
import socket
from types import ModuleType
from typing import Optional
from urllib.parse import urlparse

# Import log from logger (centralized location)
from .logger import log


# ============================================================================
# Pre-compiled regex patterns for strip_thinking_tags()
# These patterns are used during LLM inference - compiling once saves ~10ms per call
# ============================================================================

# Wrapper tags that should have their entire content removed
_THINKING_WRAPPER_TAGS = ['think', 'thinking', 'reasoning', 'summary']

# Pre-compiled patterns for each wrapper tag (XML and bracket styles)
_RE_THINKING_XML_BLOCK = {
    tag: re.compile(rf'<{tag}>.*?</{tag}>\s*', re.DOTALL | re.IGNORECASE)
    for tag in _THINKING_WRAPPER_TAGS
}
_RE_THINKING_BRACKET_BLOCK = {
    tag: re.compile(rf'\[{tag.upper()}\].*?\[/{tag.upper()}\]\s*', re.DOTALL)
    for tag in _THINKING_WRAPPER_TAGS
}

# Orphan tag patterns (opening without closing, or closing without opening)
_RE_THINKING_XML_OPEN = {
    tag: re.compile(rf'<{tag}>', re.IGNORECASE)
    for tag in _THINKING_WRAPPER_TAGS
}
_RE_THINKING_XML_CLOSE = {
    tag: re.compile(rf'</{tag}>', re.IGNORECASE)
    for tag in _THINKING_WRAPPER_TAGS
}
_RE_THINKING_XML_ORPHAN_CLOSE = {
    tag: re.compile(rf'^.*?</{tag}>\s*', re.DOTALL | re.IGNORECASE)
    for tag in _THINKING_WRAPPER_TAGS
}
_RE_THINKING_XML_ORPHAN_OPEN = {
    tag: re.compile(rf'<{tag}>.*$', re.DOTALL | re.IGNORECASE)
    for tag in _THINKING_WRAPPER_TAGS
}
_RE_THINKING_BRACKET_OPEN = {
    tag: re.compile(rf'\[{tag.upper()}\]')
    for tag in _THINKING_WRAPPER_TAGS
}
_RE_THINKING_BRACKET_CLOSE = {
    tag: re.compile(rf'\[/{tag.upper()}\]')
    for tag in _THINKING_WRAPPER_TAGS
}
_RE_THINKING_BRACKET_ORPHAN_CLOSE = {
    tag: re.compile(rf'^.*?\[/{tag.upper()}\]\s*', re.DOTALL)
    for tag in _THINKING_WRAPPER_TAGS
}
_RE_THINKING_BRACKET_ORPHAN_OPEN = {
    tag: re.compile(rf'\[{tag.upper()}\].*$', re.DOTALL)
    for tag in _THINKING_WRAPPER_TAGS
}

# Generic tag cleanup patterns
_RE_XML_ANY_TAG = re.compile(r'</?[a-zA-Z_][a-zA-Z0-9_]*\s*/?>')
_RE_BRACKET_ANY_TAG = re.compile(r'\[/?[A-Z_][A-Z0-9_]*\]')
_RE_CODE_FENCE_OPEN = re.compile(r'^```[a-zA-Z]*\n?')
_RE_CODE_FENCE_CLOSE = re.compile(r'\n?```\s*$')

# Chat template tokens that sometimes leak into model output
_RE_IM_TOKEN = re.compile(
    r'(?i)<\|?im_(start|end)\|?>|<im_(start|end)>|<\|endoftext\|>'
)

# ============================================================================
# Pre-compiled regex patterns for strip_llm_prefixes()
# These remove common LLM output preambles that don't belong in the output
# Note: Input is normalized (curly quotes → ASCII) before matching, so patterns use ASCII only
# ============================================================================

# Pattern: "Here's a thorough, uncensored description of everything visible in the image:"
# Also matches: "Here is a detailed description:", "Here's the description:", etc.
_RE_PREFIX_HERES = re.compile(
    r"^Here(?:'s| is) (?:a |the )?(?:[a-z]+,? )*(?:description|caption|summary|analysis)[^:]*:\s*",
    re.IGNORECASE
)

# Pattern: "The image shows...", "This picture depicts...", "In this image, we see..."
_RE_PREFIX_IMAGE_VERB = re.compile(
    r"^(?:The|This|In this) (?:[a-z]+ )*(?:image|picture|photo|photograph|illustration|artwork|scene)"
    r"(?: (?:shows|depicts|features|presents|displays|is of|captures|conveys)[,:]?| (?:we see|there's|there is|you can see))\s*",
    re.IGNORECASE
)

# Pattern: "Based on what you've described...", "From the description..."
_RE_PREFIX_BASED_ON = re.compile(
    r"^(?:Based on|From) (?:the |what you(?:'ve)? )?(?:description|image|picture)[^:]*[,:.]?\s*",
    re.IGNORECASE
)

# Pattern: "Let me describe...", "I'll describe...", "I will describe..."
_RE_PREFIX_LET_ME = re.compile(
    r"^(?:Let me|I(?:'ll| will)) (?:describe|analyze|break down|explain)[^:]*[,:.]?\s*",
    re.IGNORECASE
)

# Pattern: "Certainly!", "Sure!", "Of course!" at start (common politeness)
_RE_PREFIX_POLITENESS = re.compile(
    r"^(?:Certainly|Sure|Of course|Absolutely)[!.]?\s*",
    re.IGNORECASE
)

# Pattern: "Here is a rewritten version of the text in a more creative and engaging style:"
# Also matches: "Here's a revised version...", "Here is a rephrased version...",
# "Here is the rewritten text in a more creative and engaging style:", etc.
_RE_PREFIX_REWRITTEN = re.compile(
    r"^Here(?:'s| is) (?:a |the )?(?:[a-z]+[,]? )*(?:version|rendition|rewrite|text)[^:]*:\s*",
    re.IGNORECASE
)

_LLM_PREFIX_PATTERNS = [
    _RE_PREFIX_HERES,
    _RE_PREFIX_IMAGE_VERB,
    _RE_PREFIX_BASED_ON,
    _RE_PREFIX_LET_ME,
    _RE_PREFIX_POLITENESS,
    _RE_PREFIX_REWRITTEN,
]

# Role prefixes that LLMs sometimes prepend (e.g. "assistant: ...", "Output: ...")
_RE_ROLE_PREFIX = re.compile(
    r'^\s*(assistant|final|output|response|result|prompt)\s*:\s*', re.IGNORECASE
)

# Marker labels that LLMs insert mid-output (e.g. "Final answer:", "Result:")
_RE_MARKER_LINE = re.compile(
    r'(?im)^\s*(final|final answer|answer|output|result|prompt)\s*[:\-]\s*'
)

# Planning language that leaks from thinking models even after <think> removal
_RE_PLANNING = re.compile(
    r'(?is)\b('
    r'i\s+(should|need|must|will|want|am\s+going\s+to|have\s+to)\b|'
    r"let's\b|"
    r'first\b|next\b|then\b|'
    r'wait\b|'
    r'so\s+i\s+need\s+to\b|'
    r'i\s+should\s+focus\s+on\b'
    r')'
)


def is_safe_url(url: str) -> bool:
    # Validate URL to prevent SSRF attacks.
    # Blocks private IP ranges and localhost to prevent internal network access.
    #
    # Returns:
    #     True if URL is safe to fetch, False otherwise.
    if not url:
        log.warning("Security", "Blocked empty URL")
        return False
    
    try:
        parsed = urlparse(url)
        
        # Only allow http/https
        if parsed.scheme not in ('http', 'https'):
            log.warning("Security", f"Blocked non-http(s) URL scheme: {parsed.scheme}")
            return False
        
        hostname = parsed.hostname
        if not hostname:
            log.warning("Security", f"Blocked URL with no hostname: {url}")
            return False
        
        # Block localhost variants
        if hostname.lower() in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
            log.warning("Security", f"Blocked localhost URL: {url}")
            return False
        
        # Try to resolve hostname and check if it's a private IP
        try:
            ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(ip)
            
            # Block private, loopback, link-local, and reserved ranges
            if (ip_obj.is_private or ip_obj.is_loopback or 
                ip_obj.is_link_local or ip_obj.is_reserved):
                log.warning("Security", f"Blocked private/reserved IP URL: {url} (resolved to {ip})")
                return False
        except (socket.gaierror, ValueError):
            # Could not resolve - allow (might be valid external domain)
            pass
        
        return True
    except Exception as e:
        log.warning("Security", f"Blocked URL due to parse error: {url} ({e})")
        return False


def cleanup_memory_before_load(aggressive: bool = True) -> None:
    # Clean up memory before loading a new model.
    #
    # Parameters:
    #     aggressive: If True (default), performs full multi-device CUDA cleanup with
    #                 ipc_collect and verbose logging. Used by Smart Loaders.
    #                 If False, performs gentle cleanup that only clears unused cache
    #                 without disrupting loaded models. Used by SmartLM nodes.
    #
    # Note: Neither mode unloads models - use purge_vram() for that.
    import gc
    torch_mod: Optional[ModuleType]
    try:
        import torch as torch_mod #type: ignore
    except ImportError:
        torch_mod = None
    
    if aggressive:
        log.msg("Memory Cleanup", "Starting pre-load memory cleanup...")
    
    gc.collect()
    
    if torch_mod is not None and torch_mod.cuda.is_available():
        if aggressive:
            # Full multi-device cleanup with ipc_collect
            device_count = torch_mod.cuda.device_count()
            log.msg("Memory Cleanup", f"Clearing CUDA cache on {device_count} device(s)")
            for i in range(device_count):
                with torch_mod.cuda.device(i):
                    torch_mod.cuda.empty_cache()
                    if hasattr(torch_mod.cuda, 'ipc_collect'):
                        torch_mod.cuda.ipc_collect()
        else:
            # Gentle cleanup - just clear cache on current device
            torch_mod.cuda.empty_cache()
    
    if aggressive and torch_mod is not None and hasattr(torch_mod, 'mps') and hasattr(torch_mod.mps, 'empty_cache'):
        try:
            torch_mod.mps.empty_cache()
            log.msg("Memory Cleanup", "Cleared MPS cache")
        except Exception:
            pass
    
    try:
        import comfy.model_management as mm #type: ignore
        if hasattr(mm, 'soft_empty_cache'):
            mm.soft_empty_cache()
    except Exception:
        pass
    
    if aggressive:
        log.msg("Memory Cleanup", "✓ Memory cleanup complete")


def strip_thinking_tags(text: str) -> tuple[str, str]:
    # Strip XML-style and bracket-style tags from model output.
    #
    # Models like Qwen3-VL-Thinking, DeepSeek-R1, MiroThinker output
    # various tags like <think>, <summary>, <output>, [THINK], [/THINK], etc.
    # These wrap reasoning/planning that should be removed from final output.
    #
    # If stripping would result in empty output, return original text unchanged.
    #
    # Uses pre-compiled regex patterns defined at module level for performance.
    #
    # Args:
    #     text: Raw model output text
    #
    # Returns:
    #     Tuple of (cleaned_text, raw_text) where cleaned_text has all tags removed
    raw_text = text.strip() if text else ""
    if not raw_text:
        return "", ""
    
    cleaned_text = raw_text

    # Remove leaked chat template tokens (<|im_start|>, <|im_end|>, <|endoftext|>)
    cleaned_text = _RE_IM_TOKEN.sub('', cleaned_text).strip()
    
    # Remove all wrapper tag blocks and handle orphan tags
    for tag in _THINKING_WRAPPER_TAGS:
        # Remove complete <tag>...</tag> blocks (XML-style)
        cleaned_text = _RE_THINKING_XML_BLOCK[tag].sub('', cleaned_text).strip()
        
        # Remove complete [TAG]...[/TAG] blocks (bracket-style)
        cleaned_text = _RE_THINKING_BRACKET_BLOCK[tag].sub('', cleaned_text).strip()
        
        # Handle orphan XML tags (closing without opening)
        if _RE_THINKING_XML_CLOSE[tag].search(cleaned_text) and not _RE_THINKING_XML_OPEN[tag].search(cleaned_text):
            cleaned_text = _RE_THINKING_XML_ORPHAN_CLOSE[tag].sub('', cleaned_text).strip()
        # Handle orphan XML tags (opening without closing)
        if _RE_THINKING_XML_OPEN[tag].search(cleaned_text) and not _RE_THINKING_XML_CLOSE[tag].search(cleaned_text):
            cleaned_text = _RE_THINKING_XML_ORPHAN_OPEN[tag].sub('', cleaned_text).strip()
        
        # Handle orphan bracket tags (closing without opening)
        if _RE_THINKING_BRACKET_CLOSE[tag].search(cleaned_text) and not _RE_THINKING_BRACKET_OPEN[tag].search(cleaned_text):
            cleaned_text = _RE_THINKING_BRACKET_ORPHAN_CLOSE[tag].sub('', cleaned_text).strip()
        # Handle orphan bracket tags (opening without closing)
        if _RE_THINKING_BRACKET_OPEN[tag].search(cleaned_text) and not _RE_THINKING_BRACKET_CLOSE[tag].search(cleaned_text):
            cleaned_text = _RE_THINKING_BRACKET_ORPHAN_OPEN[tag].sub('', cleaned_text).strip()
    
    # Safety check: if stripping left us with nothing, return original
    if not cleaned_text:
        return raw_text, raw_text
    
    # Remove any remaining XML-style tags (but keep their content)
    cleaned_text = _RE_XML_ANY_TAG.sub('', cleaned_text).strip()
    
    # Remove any remaining bracket-style tags (but keep their content)
    cleaned_text = _RE_BRACKET_ANY_TAG.sub('', cleaned_text).strip()
    
    # Remove markdown code fences that some models add
    cleaned_text = _RE_CODE_FENCE_OPEN.sub('', cleaned_text).strip()
    cleaned_text = _RE_CODE_FENCE_CLOSE.sub('', cleaned_text).strip()

    # Second pass for leaked tokens exposed after tag/fence removal
    cleaned_text = _RE_IM_TOKEN.sub('', cleaned_text).strip()
    
    return cleaned_text, raw_text


def strip_llm_prefixes(text: str) -> str:
    # Strip common LLM output artifacts from model output.
    #
    # Handles (in order):
    # 1. Role prefixes: "assistant:", "output:", "response:" at start of first line
    # 2. JSON wrappers: {"prompt": "actual text"} → "actual text"
    # 3. Conversational preambles: "Here's a description:", "The image shows...", etc.
    # 4. Planning paragraphs: leading "I should...", "Let's...", "First..." paragraphs
    # 5. Marker labels: "Final answer:", "Result:" mid-output
    # 6. First-letter capitalization after prefix removal
    #
    # Uses pre-compiled regex patterns defined at module level for performance.
    #
    # Args:
    #     text: Raw model output text
    #
    # Returns:
    #     Text with artifacts stripped (or original if nothing matched)
    if not text:
        return text
    
    original = text.strip()
    
    # Normalize Unicode typography marks to ASCII equivalents for pattern matching
    # This handles curly quotes from different LLMs without complicating patterns
    cleaned = original
    cleaned = cleaned.replace('\u2019', "'")  # Right single quote → apostrophe
    cleaned = cleaned.replace('\u2018', "'")  # Left single quote → apostrophe
    cleaned = cleaned.replace('\u201c', '"')  # Left double quote → straight double
    cleaned = cleaned.replace('\u201d', '"')  # Right double quote → straight double
    cleaned = cleaned.replace('\u2014', '-')  # Em dash → hyphen
    cleaned = cleaned.replace('\u2013', '-')  # En dash → hyphen

    # Strip role prefixes from first line (e.g. "assistant: ...", "Output: ...")
    lines = cleaned.splitlines()
    if lines:
        lines[0] = _RE_ROLE_PREFIX.sub('', lines[0])
        cleaned = '\n'.join(lines).strip()

    # Extract text from JSON wrappers (e.g. {"prompt": "actual text"})
    json_extracted = _extract_from_json_wrapper(cleaned)
    if json_extracted is not None:
        cleaned = json_extracted.strip()
    
    # Debug: show first 20 chars as hex to detect invisible characters
    first_chars = cleaned[:20]
    hex_repr = ' '.join(f'{ord(c):02x}' for c in first_chars)
    log.debug("StripPrefix", f"Input first 20 chars: [{first_chars}] hex: [{hex_repr}]")
    
    # Try each prefix pattern
    for i, pattern in enumerate(_LLM_PREFIX_PATTERNS):
        match = pattern.match(cleaned)
        if match:
            log.debug("StripPrefix", f"Pattern {i} matched: [{match.group()[:40]}...]")
        new_cleaned = pattern.sub('', cleaned).strip()
        if new_cleaned != cleaned:
            log.debug("StripPrefix", f"Pattern {i} removed prefix. Before: {cleaned[:60]}... After: {new_cleaned[:60]}...")
        cleaned = new_cleaned

    # Strip leading planning paragraphs ("I should...", "Let's...", "First...")
    # These leak from thinking models even after <think> tag removal
    without_planning = _strip_planning_paragraphs(cleaned)
    if without_planning:
        cleaned = without_planning

    # Remove marker labels ("Final answer:", "Result:", etc.)
    cleaned = _RE_MARKER_LINE.sub('', cleaned).strip()
    
    # Capitalize first letter if we removed a prefix and first char is lowercase
    if cleaned and cleaned != original and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    
    if cleaned != original:
        log.debug("StripPrefix", f"Prefix stripped successfully")
    else:
        log.debug("StripPrefix", f"No pattern matched - output unchanged")

    # Auto-detect Song Lyrics output and normalise its formatting (no-op otherwise)
    cleaned = clean_song_lyrics(cleaned) if cleaned else cleaned

    return cleaned if cleaned else original


def _extract_from_json_wrapper(text: str) -> Optional[str]:
    # Extract text content from JSON wrappers that some models produce.
    #
    # Some models wrap their output in JSON like:
    #   {"prompt": "actual text here"}
    #   {"output": "the real content"}
    #
    # This extracts the value from known keys, returning None if not a JSON wrapper.
    import json as _json
    candidate = text.strip()
    if not candidate or not (candidate.startswith('{') and candidate.endswith('}')):
        return None
    try:
        payload = _json.loads(candidate)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ('prompt', 'text', 'content', 'output', 'final', 'result', 'response'):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _strip_planning_paragraphs(text: str) -> str:
    # Strip leading paragraphs that contain planning/reasoning language.
    #
    # Thinking models sometimes leak planning text even after <think> removal:
    #   "I should focus on the lighting and composition.\n\nA balanced American shot..."
    #
    # This drops consecutive leading paragraphs that match planning patterns,
    # keeping everything from the first non-planning paragraph onwards.
    # Returns original text if ALL paragraphs are planning (safety check).
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', (text or '').strip()) if p.strip()]
    if not paragraphs:
        return ''
    kept: list[str] = []
    dropping = True
    for p in paragraphs:
        if dropping and _RE_PLANNING.search(p):
            continue
        dropping = False
        kept.append(p)
    if not kept:
        return text.strip()
    return '\n\n'.join(kept).strip()


# ============================================================================
# Song Lyrics output cleanup
# Auto-detected by presence of at least 2 distinct section labels (Verse/Chorus/
# Pre-Chorus/Bridge/Intro/Outro/Solo/Final Chorus/Hook/Refrain) — the model
# already produced song lyrics, we just normalise its formatting.
# ============================================================================

# Section names that mark a lyric block. Used both for detection and for
# converting round-bracket / bold-wrapped variants to canonical [Section].
_LYRIC_SECTION_NAMES = (
    'Intro', 'Verse', 'Pre-Chorus', 'Pre Chorus', 'Chorus',
    'Final Chorus', 'Bridge', 'Outro', 'Hook', 'Refrain',
    'Guitar Solo', 'Solo', 'Breakdown', 'Interlude',
)
# Build alternation pattern for label content (allows trailing number / dash hint)
_LYRIC_LABEL_INNER = (
    r'(?:' + '|'.join(re.escape(n) for n in _LYRIC_SECTION_NAMES) + r')'
    r'(?:\s*\d+)?'                  # "Verse 1", "Chorus 2"
    r'(?:\s*[\u2014\-][^\n\]\)]+)?' # " — half-time, heavy"
)

# Detect at least 2 lyric section labels in any common form ([X], (X), **X**, **(X)**)
_RE_LYRIC_DETECT = re.compile(
    r'(?:^|\n)\s*(?:\*\*)?[\[\(]?\s*' + _LYRIC_LABEL_INNER + r'\s*[\]\)]?(?:\*\*)?\s*$',
    re.IGNORECASE | re.MULTILINE,
)

# Round-bracket label on its own line → square-bracket
_RE_LYRIC_ROUND_LABEL = re.compile(
    r'^[ \t]*\((' + _LYRIC_LABEL_INNER + r')\)[ \t]*$',
    re.IGNORECASE | re.MULTILINE,
)

# Markdown bold (** or __) anywhere — strip the markers, keep the content
_RE_MD_BOLD_STAR = re.compile(r'\*\*([^*\n]+?)\*\*')
_RE_MD_BOLD_UND = re.compile(r'__([^_\n]+?)__')
# Markdown italic via single * or _ around a phrase (avoid hitting standalone *)
_RE_MD_ITALIC_STAR = re.compile(r'(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])')
_RE_MD_ITALIC_UND = re.compile(r'(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])')

# Heading markers `# `, `## `, etc. at start of line — drop the marker, keep text
_RE_MD_HEADING = re.compile(r'^[ \t]*#{1,6}[ \t]+', re.MULTILINE)

# Line-prefix labels we don't want: "Title:", "Style:", "Genre:", "Song:", "Tempo:"
_RE_LYRIC_LINE_LABEL = re.compile(
    r'^[ \t]*(?:Title|Style|Genre|Song|Tempo)\s*:\s*',
    re.IGNORECASE | re.MULTILINE,
)

# Collapse 3+ blank lines down to 2
_RE_TRIPLE_BLANK = re.compile(r'\n{3,}')


def clean_song_lyrics(text: str) -> str:
    # Auto-detect Song Lyrics output and normalise its formatting:
    #   - strip Markdown bold/italic (** __ * _) wrappers, keep content
    #   - strip leading `#` heading markers, keep text
    #   - convert round-bracket section labels "(Verse 1)" → "[Verse 1]"
    #   - strip line prefixes "Title:", "Style:", "Genre:", "Song:", "Tempo:"
    #   - collapse 3+ blank lines to 2
    #
    # Detection: at least 2 distinct lyric section labels somewhere in the text.
    # If the heuristic doesn't fire the original text is returned untouched, so
    # this is safe to call on every LLM output.
    if not text:
        return text
    matches = _RE_LYRIC_DETECT.findall(text)
    if len(matches) < 2:
        return text

    out = text
    # Strip Markdown bold/italic wrappers (keep inner content)
    out = _RE_MD_BOLD_STAR.sub(r'\1', out)
    out = _RE_MD_BOLD_UND.sub(r'\1', out)
    out = _RE_MD_ITALIC_STAR.sub(r'\1', out)
    out = _RE_MD_ITALIC_UND.sub(r'\1', out)
    # Strip leading `# ` heading markers
    out = _RE_MD_HEADING.sub('', out)
    # Convert "(Verse 1)" / "(Chorus)" lines → "[Verse 1]" / "[Chorus]"
    out = _RE_LYRIC_ROUND_LABEL.sub(r'[\1]', out)
    # Strip "Title:" / "Style:" / etc. line prefixes
    out = _RE_LYRIC_LINE_LABEL.sub('', out)
    # Collapse blank-line runs
    out = _RE_TRIPLE_BLANK.sub('\n\n', out)
    return out.strip()


def to_posix_path(path: str) -> str:
    # Convert a path-like object or string to a POSIX-style path string.
    # Useful when passing host paths to Docker (always expects forward slashes).
    # Works with Windows (converts "C:\\path" -> "C:/path") and POSIX paths unchanged.
    from pathlib import Path
    if path is None:
        return path
    s = path
    # Replace literal backslashes with forward slashes first (covers POSIX hosts receiving Windows-like strings)
    s = s.replace('\\', '/')
    return Path(s).as_posix()

