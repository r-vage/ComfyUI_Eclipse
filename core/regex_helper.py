# Regex helper utilities for text pattern analysis and content normalization

import re
import os
import json
from typing import Optional, Tuple, List, Dict, Any
from .logger import log

_LOG_PREFIX = "RegexHelper"

# ============================================================================
# NSFW Detection - Loaded from nsfw.json
# ============================================================================

# Cache for NSFW patterns (loaded once from JSON)
_nsfw_patterns_cache: Optional[Dict[str, Any]] = None
_nsfw_json_mtime: float = 0.0


def _get_nsfw_patterns() -> Dict[str, Any]:
    # Load NSFW patterns from nsfw.json with caching.
    # Returns dict with 'x_rated' and 'mature' lists of terms.
    global _nsfw_patterns_cache, _nsfw_json_mtime
    
    # Find nsfw.json path — uses repo patterns/ folder directly
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    json_path = os.path.join(repo_dir, "patterns", "nsfw.json")
    
    if not os.path.exists(json_path):
        log.warning(_LOG_PREFIX, "nsfw.json not found, using empty patterns")
        return {'x_rated': [], 'mature': []}
    
    # Check if file changed (reload if needed)
    try:
        current_mtime = os.path.getmtime(json_path)
    except OSError:
        current_mtime = 0.0
    
    if _nsfw_patterns_cache is not None and current_mtime == _nsfw_json_mtime:
        return _nsfw_patterns_cache
    
    # Load and parse JSON
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        components = data.get('components', {})
        detection_levels = data.get('detection_levels', {})
        
        # Build term lists from detection_levels mapping
        x_rated_terms = []
        mature_terms = []
        
        for component_name in detection_levels.get('x_rated', []):
            x_rated_terms.extend(components.get(component_name, []))
        
        for component_name in detection_levels.get('mature', []):
            mature_terms.extend(components.get(component_name, []))
        
        _nsfw_patterns_cache = {
            'x_rated': x_rated_terms,
            'mature': mature_terms
        }
        _nsfw_json_mtime = current_mtime
        
        log.debug(_LOG_PREFIX, f"Loaded NSFW patterns from {json_path}: {len(x_rated_terms)} X-rated, {len(mature_terms)} Mature terms")
        return _nsfw_patterns_cache
        
    except Exception as e:
        log.error(_LOG_PREFIX, f"Error loading nsfw.json: {e}")
        return {'x_rated': [], 'mature': []}

# ============================================================================
# Age Adjustment Patterns - Hardcoded (no user modification)
# ============================================================================

# Numeric age patterns with capture groups
RE_NUMERIC_AGE_HYPHENATED = re.compile(r'\b(\d{1,2})-year-old\b', re.IGNORECASE)
RE_NUMERIC_AGE_SPACED = re.compile(r'\b(\d{1,2})\s+years?\s+old\b', re.IGNORECASE)
RE_NUMERIC_AGE_YO = re.compile(r'\b(\d{1,2})yo\b', re.IGNORECASE)
RE_NUMERIC_AGE_AROUND = re.compile(r'\baround\s+(\d{1,2})\b', re.IGNORECASE)
RE_NUMERIC_AGE_AGED = re.compile(r'\baged\s+(\d{1,2})\b', re.IGNORECASE)
RE_NUMERIC_AGE_ABOUT = re.compile(r'\babout\s+(\d{1,2})\b', re.IGNORECASE)

# Age range patterns - capture pronoun for grammar preservation
RE_AGE_RANGE_POSSESSIVE = re.compile(
    r'\bin\s+(her|his|their)\s+(early|mid|late)\s+(teens|twenties|thirties|forties|fifties|sixties|seventies|eighties|nineties)\b',
    re.IGNORECASE
)
RE_AGE_RANGE_SIMPLE = re.compile(
    r'\b(early|mid|late)\s+(teens|twenties|thirties|forties|fifties|sixties|seventies|eighties|nineties)\b',
    re.IGNORECASE
)
RE_AGE_RANGE_HYPHENATED = re.compile(
    r'\b(early|mid|late)-(teens|twenties|thirties|forties|fifties|sixties|seventies|eighties|nineties)\b',
    re.IGNORECASE
)
RE_AGE_RANGE_OR = re.compile(
    r'\b(late\s+teens)\s+or\s+(early\s+twenties)\b',
    re.IGNORECASE
)
RE_AGE_RANGE_NUMERIC = re.compile(
    r'\bin\s+(?:her|his|their)\s+(early|mid|late)\s+(\d{2})s\b',
    re.IGNORECASE
)

# Contextual age phrases
RE_AGE_APPEARS_RANGE = re.compile(
    r'\bwho\s+appears\s+to\s+be\s+in\s+(?:her|his|their)\s+(early|mid|late)\s+(teens|twenties|thirties)\b',
    re.IGNORECASE
)
RE_AGE_APPEARS_DESCRIPTOR = re.compile(
    r'\bwho\s+appears\s+to\s+be\s+(?:a\s+)?(?:young\s+)?(?:adult|teenager|teen)\b',
    re.IGNORECASE
)

# Underage terms (match including 'young/cute/adorable' prefix to avoid double replacement)
RE_UNDERAGE_GIRL = re.compile(
    r'\b(?:young|cute|adorable)\s+(?:girl|girls)\b|\b(?:girl|girls)\b',
    re.IGNORECASE
)
RE_UNDERAGE_BOY = re.compile(
    r'\b(?:young|cute|adorable)\s+(?:boy|boys)\b|\b(?:boy|boys)\b',
    re.IGNORECASE
)
RE_UNDERAGE_CHILD = re.compile(
    r'\b(?:young|cute|adorable)\s+(?:baby|infant|toddler|child|children|kid|kids|preteen|preteens)\b|\b(?:baby|infant|toddler|child|children|kid|kids|preteen|preteens)\b',
    re.IGNORECASE
)
RE_UNDERAGE_TEEN = re.compile(
    r'\b(?:teen|teenager|teenagers|teenage)\b',
    re.IGNORECASE
)

# Adult terms for validation (don't replace these)
ADULT_TERMS = {
    'woman', 'women', 'man', 'men', 'lady', 'ladies', 'gentleman', 'gentlemen',
    'adult', 'adults', 'person', 'people', 'individual', 'individuals'
}

# ============================================================================
# Text Format Detection
# ============================================================================


def is_tags_format(text: str) -> bool:
    """
    Detect if text is in tag format (comma-separated) vs prose format.
    
    Tag format examples:
    - "1girl, long hair, blue eyes, standing"
    - "masterpiece, best quality, outdoor, sunset"
    
    Prose format examples:
    - "A young woman with long hair and blue eyes standing in a field."
    - "The image shows a beautiful sunset over the mountains."
    
    Args:
        text: Input text to analyze
    
    Returns:
        True if tag format, False if prose format
    """
    if not text or len(text.strip()) < 10:
        return False
    
    # Count commas vs periods
    comma_count = text.count(',')
    period_count = text.count('.')
    
    # Tag format typically has many commas and few/no periods
    # Prose has more periods and fewer commas
    if comma_count > 3 and period_count <= 1:
        return True
    
    # Check for common tag-style patterns
    # Tags are typically short phrases without articles
    has_articles = bool(re.search(r'\b(a|an|the)\s+', text, re.IGNORECASE))
    has_verbs = bool(re.search(r'\b(is|are|was|were|has|have|shows|depicts)\s+', text, re.IGNORECASE))
    
    # If has many commas but no articles/verbs, likely tags
    if comma_count > 2 and not has_articles and not has_verbs:
        return True
    
    # If has articles and verbs, likely prose
    if has_articles and has_verbs:
        return False
    
    # Default to prose if unclear
    return False


def smart_phrase_removal(text: str, patterns: list, removal_type: str) -> str:
    """
    Remove phrases while maintaining grammar and readability.
    
    Handles:
    - Comma cleanup after removal
    - Leading/trailing punctuation
    - Extra whitespace
    - Grammar preservation (e.g., "A image of X" removal leaves proper grammar)
    
    Args:
        text: Input text
        patterns: List of compiled regex patterns to remove
        removal_type: Type of removal for logging (e.g., 'image_styles', 'nsfw')
    
    Returns:
        Text with phrases removed and grammar cleaned up
    """
    if not text:
        return text
    
    original = text
    
    # Apply all pattern removals
    for pattern in patterns:
        text = pattern.sub('', text)
    
    # Grammar cleanup
    # Remove double commas
    text = re.sub(r',\s*,', ',', text)
    # Remove comma before period
    text = re.sub(r',\s*\.', '.', text)
    # Remove leading comma/space
    text = re.sub(r'^\s*,\s*', '', text)
    # Remove trailing comma
    text = re.sub(r',\s*$', '', text)
    # Collapse multiple spaces
    text = re.sub(r'\s{2,}', ' ', text)
    # Fix spacing around punctuation
    text = re.sub(r'\s+([,.;:!?])', r'\1', text)
    
    # Trim
    text = text.strip()
    
    if text != original:
        log.debug(_LOG_PREFIX, f"Smart removal ({removal_type}): {len(original) - len(text)} chars removed")
    
    return text


def detect_nsfw_level(text: str) -> str:
    # Detect NSFW level based on content keywords from nsfw.json.
    #
    # Levels:
    # - "X": Explicit adult content (sexual acts, genitalia, explicit nudity)
    # - "Mature": Suggestive/mature content (revealing clothing, nudity, suggestive poses)
    # - "None": Safe for work content
    #
    # Args:
    #     text: Input text to analyze
    #
    # Returns:
    #     One of: "X", "Mature", "None"
    if not text:
        return "None"
    
    text_lower = text.lower()
    
    # Load patterns from nsfw.json (cached)
    patterns = _get_nsfw_patterns()
    
    # Check for X-rated content first (explicit)
    for term in patterns.get('x_rated', []):
        # Escape special regex chars and add word boundaries
        escaped = re.escape(term)
        pattern = rf'\b{escaped}\b'
        if re.search(pattern, text_lower):
            log.debug(_LOG_PREFIX, f"Detected X-rated content: matched '{term}'")
            return "X"
    
    # Check for Mature content (suggestive)
    for term in patterns.get('mature', []):
        escaped = re.escape(term)
        pattern = rf'\b{escaped}\b'
        if re.search(pattern, text_lower):
            log.debug(_LOG_PREFIX, f"Detected Mature content: matched '{term}'")
            return "Mature"
    
    # No NSFW content detected
    return "None"


def validate_nsfw_level(level: str) -> str:
    """
    Validate and normalize NSFW level string.
    
    Args:
        level: Input level string
    
    Returns:
        Normalized level: "X", "Mature", or "None"
    """
    if not level:
        return "None"
    
    level_upper = level.upper()
    
    if level_upper in ("X", "EXPLICIT", "ADULT"):
        return "X"
    elif level_upper in ("MATURE", "M", "SUGGESTIVE"):
        return "Mature"
    else:
        return "None"


# ============================================================================
# Age Adjustment Functions
# ============================================================================

def get_age_descriptor(target_age: int, gender: str) -> str:
    """
    Get age-appropriate descriptor based on target age and gender.
    
    Args:
        target_age: Target age (18-99)
        gender: 'female', 'male', or 'neutral'
    
    Returns:
        Age-appropriate descriptor (e.g., 'young woman', 'mature man')
    """
    if gender == 'female':
        if 18 <= target_age <= 25:
            return 'young woman'
        elif 26 <= target_age <= 35:
            return 'woman'
        elif 36 <= target_age <= 50:
            return 'mature woman'
        else:
            return 'woman'
    elif gender == 'male':
        if 18 <= target_age <= 25:
            return 'young man'
        elif 26 <= target_age <= 35:
            return 'man'
        elif 36 <= target_age <= 50:
            return 'mature man'
        else:
            return 'man'
    else:  # neutral
        if 18 <= target_age <= 25:
            return 'young adult'
        elif 26 <= target_age <= 35:
            return 'adult'
        else:
            return 'mature adult'


def detect_gender_context(text: str, match_start: int, match_end: int, window: int = 50) -> str:
    """
    Detect gender from surrounding context.
    
    Args:
        text: Full text
        match_start: Start position of match
        match_end: End position of match
        window: Characters to search before/after match
    
    Returns:
        'female', 'male', or 'neutral'
    """
    # Extract context window
    start = max(0, match_start - window)
    end = min(len(text), match_end + window)
    context = text[start:end].lower()
    
    # Female indicators
    female_terms = ['she', 'her', 'woman', 'lady', 'girl', 'female', 'dress', 'skirt']
    # Male indicators
    male_terms = ['he', 'him', 'his', 'man', 'gentleman', 'boy', 'male']
    
    female_count = sum(1 for term in female_terms if term in context)
    male_count = sum(1 for term in male_terms if term in context)
    
    if female_count > male_count:
        return 'female'
    elif male_count > female_count:
        return 'male'
    else:
        return 'neutral'


def replace_underage_terms(text: str, target_age: int) -> str:
    """
    Replace underage terms (girl, boy, child, teen) with age-appropriate adult terms.
    
    Args:
        text: Input text
        target_age: Target age for replacement
    
    Returns:
        Text with underage terms replaced
    """
    # First pass: Handle "teenage + noun" combinations to avoid double replacement
    # teenage boy/girl/child → young man/woman/adult
    text = re.sub(
        r'\bteenage\s+(girl|girls)\b',
        lambda m: get_age_descriptor(target_age, 'female') + ('s' if m.group(1).endswith('s') else ''),
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(
        r'\bteenage\s+(boy|boys)\b',
        lambda m: get_age_descriptor(target_age, 'male') + ('s' if m.group(1).endswith('s') else ''),
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(
        r'\bteenage\s+(child|children|kid|kids)\b',
        lambda m: get_age_descriptor(target_age, 'neutral') + ('s' if m.group(1) in ('children', 'kids') else ''),
        text,
        flags=re.IGNORECASE
    )
    
    # Replace "young girl(s)" and "girl(s)"
    for match in reversed(list(RE_UNDERAGE_GIRL.finditer(text))):
        matched_text = match.group(0)
        # Skip if part of compound like "girlfriend"
        if match.start() > 0 and text[match.start()-1:match.start()] in 'abcdefghijklmnopqrstuvwxyz':
            continue
        if match.end() < len(text) and text[match.end():match.end()+1] in 'abcdefghijklmnopqrstuvwxyz':
            continue
        
        gender = 'female'
        descriptor = get_age_descriptor(target_age, gender)
        text = text[:match.start()] + descriptor + text[match.end():]
    
    # Replace "young boy(s)" and "boy(s)"
    for match in reversed(list(RE_UNDERAGE_BOY.finditer(text))):
        matched_text = match.group(0)
        # Skip if part of compound like "boyfriend", "cowboy"
        if match.start() > 0 and text[match.start()-1:match.start()] in 'abcdefghijklmnopqrstuvwxyz':
            continue
        if match.end() < len(text) and text[match.end():match.end()+1] in 'abcdefghijklmnopqrstuvwxyz':
            continue
        
        gender = 'male'
        descriptor = get_age_descriptor(target_age, gender)
        text = text[:match.start()] + descriptor + text[match.end():]
    
    # Replace child/kid/baby terms (gender from context)
    for match in reversed(list(RE_UNDERAGE_CHILD.finditer(text))):
        gender = detect_gender_context(text, match.start(), match.end())
        descriptor = get_age_descriptor(target_age, gender)
        text = text[:match.start()] + descriptor + text[match.end():]
    
    # Replace teen/teenager terms (standalone only, teenage+noun already handled above)
    for match in reversed(list(RE_UNDERAGE_TEEN.finditer(text))):
        matched_text = match.group(0).lower()
        
        # Skip "teenage" if followed by noun (already handled in first pass)
        if matched_text == 'teenage':
            lookahead = text[match.end():match.end()+15].lower().strip()
            if re.match(r'^(girl|girls|boy|boys|child|children|kid|kids)\b', lookahead):
                continue
        
        gender = detect_gender_context(text, match.start(), match.end())
        
        # If used as adjective (teenage X), use descriptor
        # If standalone (teenager, teen), use age-year-old descriptor
        if matched_text in ('teen', 'teenager', 'teenagers'):
            descriptor = f'{target_age}-year-old {get_age_descriptor(target_age, gender)}'
        else:  # 'teenage' as adjective (but not before noun we already handled)
            descriptor = get_age_descriptor(target_age, gender)
        
        text = text[:match.start()] + descriptor + text[match.end():]
    
    return text


def adjust_age(text: str, target_age: int) -> str:
    """
    Adjust all age references in text to target age (18+).
    
    Replaces:
    - Numeric ages: "16-year-old" → "18-year-old"
    - Age ranges: "in her mid-twenties" → "25-year-old"
    - Underage terms: "young girl" → "young woman", "teenage boy" → "young man"
    
    Args:
        text: Input text to process
        target_age: Target age (must be 18-99)
    
    Returns:
        Text with age references adjusted
    """
    if not text:
        return text
    
    if not (18 <= target_age <= 99):
        log.warning(_LOG_PREFIX, f"Invalid target age {target_age}, must be 18-99. Using default 25.")
        target_age = 25
    
    original_text = text
    
    # Note: Word boundaries (\b) in patterns protect tags like "1girl", "2boys" from replacement
    # since there's no word boundary between the digit and the word
    
    # 1. Replace numeric ages (with capture groups)
    # "16-year-old" → "18-year-old"
    text = RE_NUMERIC_AGE_HYPHENATED.sub(f'{target_age}-year-old', text)
    # "16 years old" → "18 years old"
    text = RE_NUMERIC_AGE_SPACED.sub(f'{target_age} years old', text)
    # "16yo" → "18yo"
    text = RE_NUMERIC_AGE_YO.sub(f'{target_age}yo', text)
    # "around 16" → "around 18"
    text = RE_NUMERIC_AGE_AROUND.sub(f'around {target_age}', text)
    # "aged 16" → "aged 18"
    text = RE_NUMERIC_AGE_AGED.sub(f'aged {target_age}', text)
    # "about 16" → "about 18"
    text = RE_NUMERIC_AGE_ABOUT.sub(f'about {target_age}', text)
    
    # 2. Replace age range descriptions
    # "in her mid-twenties" → "25-year-old" (cleaner, prevents "in her 25 yo old" issues)
    text = RE_AGE_RANGE_POSSESSIVE.sub(f'{target_age}-year-old', text)
    # "mid-twenties" → "25-year-old"
    text = RE_AGE_RANGE_SIMPLE.sub(f'{target_age}-year-old', text)
    # "mid-twenties" (hyphenated) → "25-year-old"
    text = RE_AGE_RANGE_HYPHENATED.sub(f'{target_age}-year-old', text)
    # "late teens or early twenties" → "18-year-old"
    text = RE_AGE_RANGE_OR.sub(f'{target_age}-year-old', text)
    # "in her mid-20s" → "25-year-old"
    text = RE_AGE_RANGE_NUMERIC.sub(f'{target_age}-year-old', text)
    
    # 3. Replace contextual age phrases
    # "who appears to be in her late teens" → "who appears to be 18 years old"
    text = RE_AGE_APPEARS_RANGE.sub(f'who appears to be {target_age} years old', text)
    # "who appears to be a young adult" → "who appears to be 18 years old"
    text = RE_AGE_APPEARS_DESCRIPTOR.sub(f'who appears to be {target_age} years old', text)
    
    # 4. Replace underage terms (girl, boy, child, teen)
    text = replace_underage_terms(text, target_age)
    
    # Log if changes were made
    if text != original_text:
        changes = sum(1 for a, b in zip(original_text, text) if a != b)
        log.debug(_LOG_PREFIX, f"Adjusted age to {target_age}, {changes} characters changed")
    
    return text


def validate_age_adjustment(text: str) -> Tuple[bool, str]:
    """
    Validate that text doesn't contain underage references.
    
    Args:
        text: Text to validate
    
    Returns:
        (is_valid, reason) - True if no underage content detected
    """
    # Check for numeric ages < 18
    for pattern in [RE_NUMERIC_AGE_HYPHENATED, RE_NUMERIC_AGE_SPACED, RE_NUMERIC_AGE_YO]:
        match = pattern.search(text)
        if match:
            age_str = match.group(1)
            try:
                age = int(age_str)
                if age < 18:
                    return False, f"Found underage numeric reference: {match.group(0)}"
            except ValueError:
                pass
    
    # Check for underage terms
    if RE_UNDERAGE_GIRL.search(text):
        return False, "Found 'girl' reference"
    if RE_UNDERAGE_BOY.search(text):
        return False, "Found 'boy' reference"
    if RE_UNDERAGE_CHILD.search(text):
        return False, "Found child-related term"
    if RE_UNDERAGE_TEEN.search(text):
        return False, "Found teen-related term"
    
    return True, "No underage content detected"
