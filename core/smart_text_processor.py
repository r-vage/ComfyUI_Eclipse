import os
import json
import re
from typing import Dict, List, Optional, Any, Tuple

from .logger import log

_LOG_PREFIX = "SmartTextProcessor"


class SmartTextProcessor:
    """Pattern-based text processor using JSON template presets.
    
    Loads pattern files from templates/patterns/ with structure:
    - components: word lists for pattern building
    - pattern_presets: templates that reference components
    - sentence_patterns: regex patterns for prose sentence removal
    - soften_map: replacement mappings for NSFW softening
    
    API:
    - detect(text, categories) → list of match dicts
    - remove_matches(text, matches, preserve_categories) → cleaned text
    - soften_matches(text, matches, soften_map) → softened text
    """

    def __init__(self, patterns_dir: Optional[str] = None):
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        repo_patterns_dir = os.path.join(base, 'patterns')
        
        # Use explicit param if provided, otherwise repo patterns/ folder
        if patterns_dir:
            self.patterns_dir = patterns_dir
        else:
            self.patterns_dir = repo_patterns_dir
        
        self.index: Dict[str, Any] = {}
        self.raw_data: Dict[str, Dict] = {}  # Raw JSON data per category
        self.compiled: Dict[str, re.Pattern] = {}  # Compiled regex per category
        self.protected_patterns: Dict[str, re.Pattern] = {}  # Protected patterns (detect but don't remove)
        self.sentence_patterns: Dict[str, List[re.Pattern]] = {}  # Sentence patterns per category
        self.soften_maps: Dict[str, Dict[str, str]] = {}  # Soften maps per category
        self.cleanup_rules: Dict[str, List[Dict]] = {}  # Cleanup patterns from JSON
        
        log.debug(_LOG_PREFIX, f"Initializing with patterns_dir: {self.patterns_dir}")
        self._load_index()
        self._load_all_patterns()
        self._load_cleanup_patterns()
        self._compile_all_patterns()
        log.debug(_LOG_PREFIX, f"Initialized with {len(self.compiled)} compiled patterns")

    # =========================================================================
    # Loading
    # =========================================================================
    
    def _load_json(self, path: str) -> Any:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)

    def _load_index(self) -> None:
        index_path = os.path.join(self.patterns_dir, 'index.json')
        if not os.path.exists(index_path):
            raise FileNotFoundError(f"Pattern index not found: {index_path}")
        self.index = self._load_json(index_path)
        log.debug(_LOG_PREFIX, f"Loaded index with {len(self.index.get('files', {}))} files")

    def _load_all_patterns(self) -> None:
        files = self.index.get('files', {})
        for category, filename in files.items():
            path = os.path.join(self.patterns_dir, filename)
            if os.path.exists(path):
                self.raw_data[category] = self._load_json(path)
                log.debug(_LOG_PREFIX, f"Loaded {category} from {filename}")
            else:
                log.warning(_LOG_PREFIX, f"Pattern file not found: {path}")
                self.raw_data[category] = {}

    def _load_cleanup_patterns(self) -> None:
        """Load grammar cleanup patterns from cleanup.json."""
        cleanup_file = self.index.get('cleanup_file', 'cleanup.json')
        cleanup_path = os.path.join(self.patterns_dir, cleanup_file)
        
        if os.path.exists(cleanup_path):
            cleanup_data = self._load_json(cleanup_path)
            # Load each cleanup category
            for key in ['whitespace_cleanup', 'punctuation_cleanup', 'article_cleanup', 
                        'orphan_cleanup', 'tag_specific_cleanup', 'final_cleanup']:
                if key in cleanup_data:
                    self.cleanup_rules[key] = cleanup_data[key]
            log.debug(_LOG_PREFIX, f"Loaded cleanup patterns: {list(self.cleanup_rules.keys())}")
        else:
            log.warning(_LOG_PREFIX, f"Cleanup file not found: {cleanup_path}, using defaults")
            self.cleanup_rules = {}

    # =========================================================================
    # Template Parser
    # =========================================================================
    
    def _get_component(self, data: Dict, component_name: str) -> List[str]:
        """Get component array from data, checking both 'components' and root level."""
        components = data.get('components', {})
        if component_name in components:
            return components[component_name]
        # Fallback to root level for backward compatibility
        if component_name in data:
            val = data[component_name]
            if isinstance(val, list):
                return val
        return []

    def _escape_terms(self, terms: List[str]) -> List[str]:
        """Escape terms for regex, sort by length descending."""
        sorted_terms = sorted(set(terms), key=lambda s: -len(s))
        return [re.escape(t) for t in sorted_terms if t]

    def _build_alternation(self, terms: List[str]) -> str:
        """Build regex alternation from terms."""
        escaped = self._escape_terms(terms)
        if not escaped:
            return ''
        return '(?:' + '|'.join(escaped) + ')'

    def _parse_template(self, template: str, data: Dict) -> str:
        """Convert template string to regex pattern.
        
        Template syntax:
        - [component] → required, expands to alternation
        - [component?] → optional (zero or one)
        - [component+] → one or more with spaces
        - [component*] → zero or more with spaces
        - [,?] → optional comma
        - literal text → matched as-is
        """
        result = template
        
        # Handle [component*] (zero or more with spaces between)
        star_pattern = re.compile(r'\[(\w+)\*\]')
        for match in star_pattern.finditer(template):
            comp_name = match.group(1)
            terms = self._get_component(data, comp_name)
            if terms:
                alt = self._build_alternation(terms)
                # Zero or more: matches "", "beautiful", "beautiful stunning", etc.
                replacement = rf'(?:(?:{alt})\s+)*'
                result = result.replace(match.group(0), replacement)
            else:
                result = result.replace(match.group(0), '')
        
        # Handle [component+] (one or more with spaces between)
        plus_pattern = re.compile(r'\[(\w+)\+\]')
        for match in plus_pattern.finditer(template):
            comp_name = match.group(1)
            terms = self._get_component(data, comp_name)
            if terms:
                alt = self._build_alternation(terms)
                # One or more: matches "shot", "shot view", "shot view angle", etc.
                replacement = rf'(?:\s+{alt})+'
                result = result.replace(match.group(0), replacement)
            else:
                result = result.replace(match.group(0), '')
        
        # Handle [component?] (optional)
        optional_pattern = re.compile(r'\[(\w+)\?\]')
        for match in optional_pattern.finditer(template):
            comp_name = match.group(1)
            terms = self._get_component(data, comp_name)
            if terms:
                alt = self._build_alternation(terms)
                # Optional: zero or one with trailing space
                replacement = rf'(?:{alt}\s+)?'
                result = result.replace(match.group(0), replacement)
            else:
                result = result.replace(match.group(0), '')
        
        # Handle [component] (required)
        required_pattern = re.compile(r'\[(\w+)\]')
        for match in required_pattern.finditer(template):
            comp_name = match.group(1)
            terms = self._get_component(data, comp_name)
            if terms:
                alt = self._build_alternation(terms)
                result = result.replace(match.group(0), alt)
            else:
                # Required component missing - return empty to skip this preset
                return ''
        
        # Handle [,?] (optional comma)
        result = result.replace('[,?]', r'(?:,\s*)?')
        
        # Handle literal spaces → flexible whitespace
        # First, collapse multiple spaces that might occur after optional components
        result = re.sub(r'\)\? ', r')?', result)  # Remove space immediately after optional group
        # Make space before optional group optional too
        result = re.sub(r' \(\?:', r'(?:\\s+)?(?:', result)  # " (?: " → "(?:\s+)?(?: "
        result = re.sub(r'(?<!\\) ', r'\\s+', result)
        
        return result

    def _apply_lookarounds(self, pattern: str, preset: Dict, data: Dict) -> str:
        """Apply negative lookahead and lookbehind from preset config."""
        
        # Negative lookahead
        if 'negative_lookahead' in preset:
            lookaheads = preset['negative_lookahead']
            for la in lookaheads:
                # If lookahead contains template syntax, parse it
                if '[' in la:
                    la_pattern = self._parse_template(la, data)
                    if la_pattern:
                        pattern = rf'(?!{la_pattern}){pattern}'
                else:
                    # Literal lookahead
                    pattern = rf'(?!\s*{re.escape(la)}){pattern}'
        
        # Negative lookbehind - more complex due to fixed-width requirement
        if 'negative_lookbehind' in preset:
            lookbehinds = preset['negative_lookbehind']
            for lb in lookbehinds:
                # For lookbehind, we use simpler fixed-width patterns
                if '[' in lb:
                    # Template in lookbehind - expand but warn about limitations
                    lb_terms = []
                    lb_match = re.search(r'\[(\w+)\]', lb)
                    if lb_match:
                        comp_name = lb_match.group(1)
                        lb_terms = self._get_component(data, comp_name)
                    if lb_terms:
                        # Create individual lookbehinds for each term (fixed width)
                        for term in lb_terms:
                            pattern = rf'(?<!{re.escape(term)}\s){pattern}'
                else:
                    # Literal lookbehind
                    pattern = rf'(?<!{re.escape(lb)}\s){pattern}'
        
        return pattern

    # =========================================================================
    # Pattern Compilation
    # =========================================================================
    
    def _compile_all_patterns(self) -> None:
        """Compile all pattern categories."""
        for category, data in self.raw_data.items():
            self._compile_category(category, data)

    def _compile_category(self, category: str, data: Dict) -> None:
        """Compile a single category from its JSON data."""
        if not data:
            return
        
        # Extract presets
        presets = data.get('pattern_presets', [])
        
        if presets:
            # New preset-based compilation
            self._compile_from_presets(category, data, presets)
        else:
            # Fallback: flat terms list (legacy support)
            self._compile_from_terms(category, data)
        
        # Compile sentence patterns if present
        sentence_pats = data.get('sentence_patterns', [])
        if sentence_pats:
            self.sentence_patterns[category] = []
            log.debug(_LOG_PREFIX, f"Raw sentence_pats for {category}: {len(sentence_pats)} patterns")
            if sentence_pats:
                log.debug(_LOG_PREFIX, f"First raw pattern: {sentence_pats[0][:100]}...")
            for pat in sentence_pats:
                try:
                    # Use MULTILINE so ^ matches at line starts, not just string start
                    compiled = re.compile(pat, re.IGNORECASE | re.MULTILINE)
                    self.sentence_patterns[category].append(compiled)
                except re.error as e:
                    log.warning(_LOG_PREFIX, f"Invalid sentence pattern in {category}: {e}")
            log.debug(_LOG_PREFIX, f"Compiled {category} with {len(self.sentence_patterns[category])} sentence patterns")
        
        # Store soften_map if present
        soften_map = data.get('soften_map', {})
        if soften_map:
            self.soften_maps[category] = soften_map

    def _compile_from_presets(self, category: str, data: Dict, presets: List[Dict]) -> None:
        """Compile patterns from preset templates.
        
        Presets with "protected": true are compiled separately into self.protected_patterns.
        Protected patterns will be detected but not removed by remove_matches().
        
        Also supports raw_patterns: list of raw regex strings to include directly.
        """
        # Sort by priority (highest first)
        sorted_presets = sorted(presets, key=lambda p: -p.get('priority', 0))
        
        # Separate protected vs non-protected presets
        regular_patterns = []
        protected_patterns = []
        
        for preset in sorted_presets:
            template = preset.get('template', '')
            if not template:
                continue
            
            # Parse template to regex
            pattern = self._parse_template(template, data)
            if not pattern:
                continue
            
            # Apply lookarounds
            pattern = self._apply_lookarounds(pattern, preset, data)
            
            # Always wrap pattern with word boundaries to prevent partial word matches
            # E.g., prevent "tense" from matching inside "Intense"
            # Also add hyphen lookarounds to prevent partial matching of
            # hyphenated compounds (e.g., "animated" inside "animated-style")
            pattern = rf'(?<!-)\b(?:{pattern})\b(?!-)'
            
            # Route to protected or regular list based on flag
            if preset.get('protected', False):
                protected_patterns.append(pattern)
                log.debug(_LOG_PREFIX, f"  Protected preset '{preset.get('name', 'unnamed')}': {pattern[:80]}...")
            else:
                regular_patterns.append(pattern)
                log.debug(_LOG_PREFIX, f"  Preset '{preset.get('name', 'unnamed')}': {pattern[:80]}...")
        
        # Add raw_patterns if present (direct regex patterns without template processing)
        raw_pats = data.get('raw_patterns', [])
        for raw_pat in raw_pats:
            # Raw patterns are included directly (they define their own boundaries)
            regular_patterns.append(raw_pat)
            log.debug(_LOG_PREFIX, f"  Raw pattern: {raw_pat[:80]}...")
        
        # Compile regular patterns
        if regular_patterns:
            combined = '|'.join(regular_patterns)
            try:
                self.compiled[category] = re.compile(rf'(?P<term>{combined})', re.IGNORECASE)
                log.debug(_LOG_PREFIX, f"Compiled {category} with {len(regular_patterns)} preset patterns")
            except re.error as e:
                log.error(_LOG_PREFIX, f"Failed to compile {category}: {e}")
                log.error(_LOG_PREFIX, f"Pattern was: {combined[:500]}...")
        
        # Compile protected patterns separately
        if protected_patterns:
            combined = '|'.join(protected_patterns)
            try:
                self.protected_patterns[category] = re.compile(rf'(?P<term>{combined})', re.IGNORECASE)
                log.debug(_LOG_PREFIX, f"Compiled {category} with {len(protected_patterns)} PROTECTED patterns")
            except re.error as e:
                log.error(_LOG_PREFIX, f"Failed to compile protected {category}: {e}")
                log.error(_LOG_PREFIX, f"Pattern was: {combined[:500]}...")

    def _compile_from_terms(self, category: str, data: Dict) -> None:
        """Compile from flat terms list (legacy fallback)."""
        terms = []
        
        # Collect terms from various possible structures
        if 'terms' in data:
            terms = data['terms']
        else:
            # Flatten all arrays in the data
            for key, val in data.items():
                if key in ('description', 'soften_map', 'sentence_patterns', 'pattern_presets', 'components'):
                    continue
                if isinstance(val, list):
                    terms.extend(val)
                elif isinstance(val, dict):
                    for subval in val.values():
                        if isinstance(subval, list):
                            terms.extend(subval)
        
        if terms:
            escaped = self._escape_terms(terms)
            alternation = '|'.join(escaped)
            try:
                self.compiled[category] = re.compile(rf'(?P<term>(?<!-)\b(?:{alternation})\b(?!-))', re.IGNORECASE)
                log.debug(_LOG_PREFIX, f"Compiled {category} with {len(terms)} flat terms")
            except re.error as e:
                log.error(_LOG_PREFIX, f"Failed to compile {category}: {e}")

    # =========================================================================
    # Detection API
    # =========================================================================
    
    def detect(self, text: str, categories: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Detect pattern matches in text.
        
        Args:
            text: Input text to analyze
            categories: List of categories to check (None = all)
        
        Returns:
            List of match dicts: {category, text, span, score, protected}
            Matches from protected patterns have protected=True
        """
        if not text:
            return []
        
        cats = categories if categories else list(self.compiled.keys())
        matches = []
        
        # Check regular patterns
        for cat in cats:
            if cat not in self.compiled:
                continue
            
            pattern = self.compiled[cat]
            for m in pattern.finditer(text):
                match_text = m.group('term') if 'term' in m.groupdict() else m.group(0)
                matches.append({
                    'category': cat,
                    'text': match_text,
                    'span': (m.start(), m.end()),
                    'score': 1.0,
                    'protected': False
                })
        
        # Check protected patterns
        for cat in cats:
            if cat not in self.protected_patterns:
                continue
            
            pattern = self.protected_patterns[cat]
            for m in pattern.finditer(text):
                match_text = m.group('term') if 'term' in m.groupdict() else m.group(0)
                matches.append({
                    'category': cat,
                    'text': match_text,
                    'span': (m.start(), m.end()),
                    'score': 1.0,
                    'protected': True  # Mark as protected - will not be removed
                })
        
        log.debug(_LOG_PREFIX, f"detect() found {len(matches)} matches in {len(cats)} categories")
        if matches:
            for m in matches:
                log.debug(_LOG_PREFIX, f"  match: '{m['text']}' cat={m['category']} span={m['span']} protected={m.get('protected', False)}")
        return matches

    def detect_sentences(self, text: str, categories: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Detect sentence-level patterns for prose removal.
        
        Args:
            text: Input text to analyze
            categories: List of categories to check
        
        Returns:
            List of match dicts for full sentences
        """
        if not text:
            return []
        
        cats = categories if categories else list(self.sentence_patterns.keys())
        matches = []
        
        # Debug: show what categories have sentence patterns
        log.debug(_LOG_PREFIX, f"detect_sentences() requested cats: {cats}")
        log.debug(_LOG_PREFIX, f"detect_sentences() available sentence_patterns keys: {list(self.sentence_patterns.keys())}")
        
        for cat in cats:
            if cat not in self.sentence_patterns:
                log.debug(_LOG_PREFIX, f"detect_sentences() category '{cat}' NOT in sentence_patterns, skipping")
                continue
            
            pattern_count = len(self.sentence_patterns[cat])
            log.debug(_LOG_PREFIX, f"detect_sentences() checking {pattern_count} patterns for category '{cat}'")
            
            # Debug: check if "The lighting" exists in text
            import re as re_debug
            lighting_pos = text.lower().find('the lighting')
            if lighting_pos >= 0:
                # Show context around "The lighting"
                start = max(0, lighting_pos - 10)
                end = min(len(text), lighting_pos + 80)
                context = text[start:end]
                log.debug(_LOG_PREFIX, f"detect_sentences() Found 'The lighting' at pos {lighting_pos}")
                log.debug(_LOG_PREFIX, f"detect_sentences() Context: ...{repr(context)}...")
                # Check char before "The lighting"
                if lighting_pos > 0:
                    char_before = text[lighting_pos - 1]
                    log.debug(_LOG_PREFIX, f"detect_sentences() Char before 'The lighting': {repr(char_before)} (ord={ord(char_before)})")
                
                # Test simple pattern without lookbehind
                simple_pat = re_debug.compile(r'The (?:light|lighting|illumination) (?:is|appears)', re_debug.IGNORECASE)
                simple_match = simple_pat.search(text)
                log.debug(_LOG_PREFIX, f"detect_sentences() Simple pattern test: {'MATCH' if simple_match else 'NO MATCH'}")
            else:
                log.debug(_LOG_PREFIX, f"detect_sentences() 'The lighting' NOT found in text!")
            
            # Debug: test first pattern directly
            if self.sentence_patterns[cat]:
                first_pat = self.sentence_patterns[cat][0]
                log.debug(_LOG_PREFIX, f"detect_sentences() First pattern for {cat}: {first_pat.pattern[:100]}...")
                test_match = first_pat.search(text)
                log.debug(_LOG_PREFIX, f"detect_sentences() First pattern direct test: {'MATCH' if test_match else 'NO MATCH'}")
                if test_match:
                    log.debug(_LOG_PREFIX, f"detect_sentences() Match text: {test_match.group()[:60]}...")
            
            for i, pattern in enumerate(self.sentence_patterns[cat]):
                found = list(pattern.finditer(text))
                if found:
                    log.debug(_LOG_PREFIX, f"detect_sentences() pattern {i} matched {len(found)} times")
                for m in found:
                    matches.append({
                        'category': cat,
                        'text': m.group(0),
                        'span': (m.start(), m.end()),
                        'score': 1.0,
                        'type': 'sentence'
                    })
        
        log.debug(_LOG_PREFIX, f"detect_sentences() found {len(matches)} sentence matches")
        return matches

    def remove_prefixes(self, text: str, categories: Optional[List[str]] = None) -> str:
        """Remove prefix patterns from text (e.g., 'The image depicts ').
        
        Unlike sentence patterns which remove to the period, prefix patterns
        only remove the matched prefix itself, preserving the content after it.
        
        This method uses pattern_presets with ^ anchored templates to detect
        prefixes at the start of text, then removes only the prefix portion.
        
        Args:
            text: Input text to process
            categories: List of categories to check
        
        Returns:
            Text with prefixes removed
        """
        if not text:
            return text
        
        result = text
        cats = categories if categories else list(self.compiled.keys())
        
        for cat in cats:
            # Use detect() to find matches, then filter for prefix matches (start near position 0)
            matches = self.detect(result, categories=[cat])
            prefix_matches = [m for m in matches if m['span'][0] <= 2]
            
            if prefix_matches:
                # Sort by length descending to get longest match
                prefix_matches.sort(key=lambda m: m['span'][1] - m['span'][0], reverse=True)
                best = prefix_matches[0]
                # Remove the prefix
                result = result[best['span'][1]:].lstrip(' ,')
        
        # Capitalize first letter if text starts lowercase after removal
        result = result.strip()
        if result and result[0].islower():
            result = result[0].upper() + result[1:]
        
        return result

    # =========================================================================
    # Removal API
    # =========================================================================
    
    def remove_matches(self, text: str, matches: List[Dict[str, Any]], 
                       preserve_categories: Optional[List[str]] = None) -> str:
        """Remove matched spans from text with grammar cleanup.
        
        Args:
            text: Input text
            matches: List of match dicts from detect()
            preserve_categories: Categories to keep (their spans won't be removed)
        
        Returns:
            Cleaned text with matches removed
        
        Note:
            Matches with protected=True are automatically preserved regardless
            of preserve_categories. This allows subject emotions to be detected
            but never removed during mood/atmosphere cleanup.
        """
        if not matches:
            return text
        
        preserve_categories = preserve_categories or []
        
        # Separate matches into remove vs preserve
        # Protected matches are always preserved
        removal_spans = []
        preserve_spans = []
        
        for m in matches:
            span = m['span']
            if m.get('protected', False) or m['category'] in preserve_categories:
                preserve_spans.append(span)
            else:
                removal_spans.append(span)
        
        if not removal_spans:
            return text
        
        # Sort and merge overlapping removal spans
        removal_spans.sort()
        merged = [removal_spans[0]]
        for start, end in removal_spans[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        removal_spans = merged
        
        # Subtract preserved spans from removal spans
        if preserve_spans:
            adjusted = []
            for rem_start, rem_end in removal_spans:
                fragments = [(rem_start, rem_end)]
                
                for pres_start, pres_end in preserve_spans:
                    new_fragments = []
                    for frag_start, frag_end in fragments:
                        if frag_end <= pres_start or frag_start >= pres_end:
                            new_fragments.append((frag_start, frag_end))
                        elif pres_start <= frag_start and pres_end >= frag_end:
                            pass  # Fully covered by preserve
                        elif pres_start <= frag_start < pres_end < frag_end:
                            new_fragments.append((pres_end, frag_end))
                        elif frag_start < pres_start < frag_end <= pres_end:
                            new_fragments.append((frag_start, pres_start))
                        elif frag_start < pres_start and pres_end < frag_end:
                            new_fragments.append((frag_start, pres_start))
                            new_fragments.append((pres_end, frag_end))
                    fragments = new_fragments
                
                adjusted.extend(fragments)
            removal_spans = adjusted
        
        # Remove spans from back to front, preserving space when needed
        s = text
        for start, end in sorted(removal_spans, reverse=True):
            # If span starts and ends mid-sentence (not at text boundaries),
            # and text has non-space chars on both sides, leave a space
            needs_space = (
                start > 0 and end < len(s) and  # Not at boundaries
                s[start-1:start] not in (' ', '\t', '\n', '') and  # Char before span
                s[end:end+1] not in (' ', '\t', '\n', '')  # Char after span
            )
            replacement = ' ' if needs_space else ''
            s = s[:start] + replacement + s[end:]
        
        # Grammar cleanup
        s = self._cleanup_grammar(s)
        
        return s

    def _apply_cleanup_rules(self, text: str, rule_category: str) -> str:
        """Apply cleanup rules from a specific category."""
        rules = self.cleanup_rules.get(rule_category, [])
        s = text
        
        for rule in rules:
            pattern = rule.get('pattern', '')
            replacement = rule.get('replacement', '')
            flags_str = rule.get('flags', '')
            
            if not pattern:
                continue
            
            # Parse flags
            flags = 0
            if 'IGNORECASE' in flags_str:
                flags |= re.IGNORECASE
            if 'MULTILINE' in flags_str:
                flags |= re.MULTILINE
            
            try:
                s = re.sub(pattern, replacement, s, flags=flags)
            except re.error as e:
                log.warning(_LOG_PREFIX, f"Invalid cleanup pattern '{pattern}': {e}")
        
        return s

    def _cleanup_grammar(self, text: str) -> str:
        """Clean up grammar artifacts after removal using JSON-defined patterns."""
        s = text
        
        # If cleanup rules loaded from JSON, use them
        if self.cleanup_rules:
            # Apply in order: whitespace → punctuation → articles → orphans → tags → final
            s = self._apply_cleanup_rules(s, 'whitespace_cleanup')
            s = self._apply_cleanup_rules(s, 'punctuation_cleanup')
            s = self._apply_cleanup_rules(s, 'article_cleanup')
            # Normalize whitespace again after article cleanup
            s = re.sub(r'\s{2,}', ' ', s)
            s = self._apply_cleanup_rules(s, 'orphan_cleanup')
            s = self._apply_cleanup_rules(s, 'tag_specific_cleanup')
            s = self._apply_cleanup_rules(s, 'final_cleanup')
        
        return s.strip()


    # =========================================================================
    # Softening API
    # =========================================================================
    
    def soften_matches(self, text: str, matches: List[Dict[str, Any]], 
                       soften_map: Optional[Dict[str, str]] = None) -> str:
        """Replace matched spans using soften_map.
        
        Args:
            text: Input text
            matches: List of match dicts from detect()
            soften_map: Dict mapping lowercase terms to replacements
        
        Returns:
            Text with matches replaced according to soften_map
        """
        if not matches:
            return text
        
        # Use provided map or try to get from category
        if soften_map is None:
            # Try to get from first match's category
            if matches:
                cat = matches[0].get('category', '')
                soften_map = self.soften_maps.get(cat, {})
        
        if not soften_map:
            return text
        
        s = text
        for m in sorted(matches, key=lambda x: x['span'][0], reverse=True):
            start, end = m['span']
            key = m['text'].lower().strip()
            if key in soften_map:
                replacement = soften_map[key]
                s = s[:start] + replacement + s[end:]
        
        # Cleanup
        s = self._cleanup_grammar(s)
        
        return s

    def get_soften_map(self, category: str) -> Dict[str, str]:
        """Get soften_map for a category."""
        return self.soften_maps.get(category, {})

    # =========================================================================
    # Utility
    # =========================================================================
    
    def get_categories(self) -> List[str]:
        """Get list of available detection categories."""
        return list(self.compiled.keys())

    def has_category(self, category: str) -> bool:
        """Check if category exists."""
        return category in self.compiled

    def export_debug(self, path: str, data: Any) -> None:
        """Export data to JSON for debugging."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)


# =============================================================================
# Singleton
# =============================================================================

_default_processor: Optional[SmartTextProcessor] = None
_pattern_file_mtimes: Dict[str, float] = {}


def get_default_processor() -> SmartTextProcessor:
    """Get or create the default processor singleton."""
    global _default_processor
    
    if _default_processor is None:
        log.debug(_LOG_PREFIX, "Creating default processor (first access)")
        _default_processor = SmartTextProcessor()
        _cache_pattern_mtimes(_default_processor.patterns_dir)
    elif _patterns_have_changed(_default_processor.patterns_dir):
        log.debug(_LOG_PREFIX, "Pattern files changed, reloading processor")
        _default_processor = SmartTextProcessor()
        _cache_pattern_mtimes(_default_processor.patterns_dir)
    
    return _default_processor


def invalidate_processor() -> None:
    """Force reload of pattern processor on next access."""
    global _default_processor, _pattern_file_mtimes
    log.debug(_LOG_PREFIX, "Invalidating processor cache")
    _default_processor = None
    _pattern_file_mtimes.clear()


def _cache_pattern_mtimes(patterns_dir: str) -> None:
    """Cache modification times of all pattern files."""
    global _pattern_file_mtimes
    _pattern_file_mtimes.clear()
    
    index_path = os.path.join(patterns_dir, 'index.json')
    if os.path.exists(index_path):
        _pattern_file_mtimes[index_path] = os.path.getmtime(index_path)
        
        try:
            with open(index_path, 'r', encoding='utf-8') as fh:
                index_data = json.load(fh)
            for fname in index_data.get('files', {}).values():
                pattern_file = os.path.join(patterns_dir, fname)
                if os.path.exists(pattern_file):
                    _pattern_file_mtimes[pattern_file] = os.path.getmtime(pattern_file)
        except Exception:
            pass


def _patterns_have_changed(patterns_dir: str) -> bool:
    """Check if any pattern files have been modified."""
    global _pattern_file_mtimes
    
    index_path = os.path.join(patterns_dir, 'index.json')
    if not os.path.exists(index_path):
        return False
    
    if index_path not in _pattern_file_mtimes:
        return True
    if os.path.getmtime(index_path) != _pattern_file_mtimes[index_path]:
        return True
    
    try:
        with open(index_path, 'r', encoding='utf-8') as fh:
            index_data = json.load(fh)
        for fname in index_data.get('files', {}).values():
            pattern_file = os.path.join(patterns_dir, fname)
            if os.path.exists(pattern_file):
                cached = _pattern_file_mtimes.get(pattern_file)
                current = os.path.getmtime(pattern_file)
                if cached is None or current != cached:
                    return True
    except Exception:
        return False
    
    return False
