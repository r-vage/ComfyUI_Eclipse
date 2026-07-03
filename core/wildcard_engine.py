# Wildcard Processing Engine for Eclipse
#
# Processes text with wildcard syntax supporting:
# - {option1|option2|option3} - random selection
# - __keyword__ - wildcard reference
# - 2$$opt1|opt2 - quantity selection
# - 1-3$$items - range selection
# - 1.0::item1|2.0::item2 - probability weights
# - 3#__keyword__ - quantifier

import re
import random
import os
import threading
import yaml  # type: ignore[import-untyped]
import numpy as np # type: ignore
from typing import Dict, List, Optional, Tuple
from .logger import log


_LOG_PREFIX = "Wildcard"


# Global state for wildcard dictionary
wildcard_lock = threading.Lock()
wildcard_dict: Dict[str, List[str]] = {}

# Pre-compiled regex patterns for performance
RE_WildCardQuantifier = re.compile(
    r"(?P<quantifier>\d+)#__(?P<keyword>[\w.\-+/*\\]+?)__",
    re.IGNORECASE
)
RE_NUMERIC_STRING = re.compile(r'^-?(\d*\.?\d+|\d+\.?\d*)$')
RE_RANGE_PATTERN = re.compile(r'(\d+)(-(\d+))?')
RE_RANGE_PATTERN2 = re.compile(r'-(\d+)')
RE_WILDCARD_PATTERN = re.compile(r"__([\w.\-+/*\\]+?)__")
RE_OPTIONS_PATTERN = re.compile(r'(?<!\\)\{((?:[^{}]|(?<=\\)[{}])*?)(?<!\\)\}')
RE_PROBABILITY_PREFIX = re.compile(r'^\s*[0-9.]+::')
# Raffle-style taglist prefix: "post_id, score, tag1, tag2, ..."
RE_RAFFLE_PREFIX = re.compile(r'^\d+,\s*\d+,\s*')


def strip_raffle_prefix(text: str) -> str:
    # Strip leading "post_id, score, " prefix from Raffle taglist lines.
    return RE_RAFFLE_PREFIX.sub('', text)


def wildcard_normalize(x: str) -> str:
    # Normalize wildcard keywords to lowercase with / separator.
    return x.replace("\\", "/").replace(' ', '-').lower()


def get_wildcard_list() -> List[str]:
    # Get list of available wildcards in format: ['__keyword1__', '__keyword2__']
    with wildcard_lock:
        return [f"__{x}__" for x in sorted(wildcard_dict.keys())]


def get_wildcard_dict() -> Dict[str, List[str]]:
    # Get a copy of the wildcard dictionary (thread-safe).
    with wildcard_lock:
        return wildcard_dict.copy()


def is_numeric_string(input_str: str) -> bool:
    # Check if string represents a number.
    return RE_NUMERIC_STRING.match(input_str) is not None


def safe_float(x: str) -> float:
    # Safely convert string to float, default 1.0 if not numeric.
    if is_numeric_string(x):
        return float(x)
    return 1.0


def process_comment_out(text: str) -> str:
    # Remove comment lines (lines starting with #) and merge continuations.
    lines = text.split('\n')
    lines0: list[str] = []
    flag = False

    for line in lines:
        if line.lstrip().startswith('#'):
            flag = True
            continue

        if len(lines0) == 0:
            lines0.append(line)
        elif flag:
            lines0[-1] += ' ' + line
            flag = False
        else:
            lines0.append(line)

    return '\n'.join(lines0)


def read_wildcard(k: str, v) -> None:
    # Recursively read wildcard structures from YAML.
    if isinstance(v, list):
        k = wildcard_normalize(k)
        wildcard_dict[k] = v
    elif isinstance(v, dict):
        for k2, v2 in v.items():
            new_key = f"{k}/{k2}"
            read_wildcard(new_key, v2)
    elif isinstance(v, str):
        k = wildcard_normalize(k)
        wildcard_dict[k] = [v]
    elif isinstance(v, (int, float)):
        k = wildcard_normalize(k)
        wildcard_dict[k] = [str(v)]


def read_wildcard_dict(wildcard_path: str) -> Dict[str, List[str]]:
    # Load all wildcards from directory of .txt and .yaml files.
    if not os.path.exists(wildcard_path):
        log.warning(_LOG_PREFIX, f"Path does not exist: {wildcard_path}")
        return wildcard_dict

    for root, directories, files in os.walk(wildcard_path, followlinks=True):
        # Read .txt files (one line per item)
        for file in files:
            if file.endswith('.txt'):
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, wildcard_path)
                key = wildcard_normalize(os.path.splitext(rel_path)[0])

                try:
                    with open(file_path, 'r', encoding="UTF-8") as f:
                        lines = f.read().splitlines()
                        # Skip comment lines and empty lines
                        wildcard_dict[key] = [
                            x.strip() for x in lines 
                            if x.strip() and not x.strip().startswith('#')
                        ]
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"Error reading {file_path}: {e}")

        # Read .yaml/.yml files (structured format)
        for file in files:
            if file.endswith('.yaml') or file.endswith('.yml'):
                file_path = os.path.join(root, file)

                try:
                    with open(file_path, 'r', encoding="UTF-8") as f:
                        yaml_data = yaml.load(f, Loader=yaml.SafeLoader)
                        if yaml_data:
                            for k, v in yaml_data.items():
                                read_wildcard(k, v)
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"Error reading {file_path}: {e}")

    return wildcard_dict


def wildcard_load(wildcard_path: str) -> None:
    # Load all wildcards from disk (thread-safe).
    global wildcard_dict

    with wildcard_lock:
        wildcard_dict = {}
        read_wildcard_dict(wildcard_path)
        log.msg(_LOG_PREFIX, f"Loaded {len(wildcard_dict)} wildcard groups")


def process(text: str, seed: Optional[int] = None) -> str:
    # Process wildcard text with all supported syntax patterns.
    #
    # Supports:
    # - {option1|option2|option3} - random selection
    # - __keyword__ - wildcard reference
    # - 2$$opt1|opt2 - quantity selection
    # - 1-3$$items - range selection
    # - 1.0::item1|2.0::item2 - probability weights
    # - 3#__keyword__ - quantifier
    text = process_comment_out(text)

    if seed is not None:
        random.seed(seed)
    random_gen = np.random.default_rng(seed)

    local_wildcard_dict = get_wildcard_dict()

    def replace_options(string: str) -> Tuple[str, bool]:
        # Replace {option1|option2|...} patterns.
        replacements_found = False

        def replace_option(match):
            nonlocal replacements_found
            options = match.group(1).split('|')

            # Parse multi-select pattern like 2$$opt1|opt2 or 1-3$$opt1|opt2|...
            multi_select_pattern = options[0].split('$$')
            select_range = None
            select_sep = ' '

            if len(multi_select_pattern) > 1:
                # Try to parse range like 1-3 or single number like 2
                r = RE_RANGE_PATTERN.match(options[0])

                if r is None:
                    r = RE_RANGE_PATTERN2.match(options[0])
                    a = '1'
                    b = r.group(1).strip() if r else None
                else:
                    a = r.group(1).strip() if r else '1'
                    b = r.group(3).strip() if r and r.group(3) else a

                if r is not None and a is not None and is_numeric_string(a) and b is not None and is_numeric_string(b):
                    select_range = (int(a), int(b))

                    # Handle $$pattern syntax
                    if len(multi_select_pattern) == 2:
                        # count$$opt1|opt2|...
                        options_pattern = multi_select_pattern[1]
                        matches = RE_WILDCARD_PATTERN.findall(options_pattern)
                        if len(options) == 1 and matches:
                            options = get_wildcard_options(options_pattern)
                        else:
                            options[0] = options_pattern
                    elif len(multi_select_pattern) == 3:
                        # count$$sep$$opt1|opt2|...
                        select_sep = multi_select_pattern[1]
                        options_pattern = multi_select_pattern[2]
                        matches = RE_WILDCARD_PATTERN.findall(options_pattern)
                        if len(options) == 1 and matches:
                            options = get_wildcard_options(options_pattern)
                        else:
                            options[0] = options_pattern

            # Parse probability weights
            adjusted_probabilities = []
            total_prob = 0.0

            for option in options:
                parts = option.split('::', 1) if isinstance(option, str) else f"{option}".split('::', 1)

                if len(parts) == 2 and is_numeric_string(parts[0].strip()):
                    config_value = float(parts[0].strip())
                else:
                    config_value = 1.0

                adjusted_probabilities.append(config_value)
                total_prob += config_value

            normalized_probabilities = [prob / total_prob for prob in adjusted_probabilities]

            # Calculate how many to select
            select_count: int
            if select_range is None:
                select_count = 1
            else:
                max_val = min(select_range[1] + 1, len(options) + 1) if select_range[1] > 0 else len(options) + 1
                min_val = select_range[0]
                
                if max_val <= 0:
                    select_count = 0
                elif max_val == min_val:
                    select_count = min_val
                else:
                    select_count = int(random_gen.integers(min_val, max_val))

            if select_count > len(options) or total_prob <= 1:
                random_gen.shuffle(options)
                selected_items = options
            else:
                selected_items = random_gen.choice(options, p=normalized_probabilities, size=select_count, replace=False)

            # Remove probability prefixes
            selected_items2 = [RE_PROBABILITY_PREFIX.sub('', str(x), count=1) for x in selected_items]
            replacement = select_sep.join(selected_items2)

            replacements_found = True
            return replacement

        replaced_string = RE_OPTIONS_PATTERN.sub(replace_option, string)

        return replaced_string, replacements_found

    def get_wildcard_options(string: str) -> List[str]:
        # Get options from wildcard references in a string.
        matches = RE_WILDCARD_PATTERN.findall(string)

        options = []

        for match in matches:
            keyword = match.lower()
            keyword = wildcard_normalize(keyword)
            if keyword in local_wildcard_dict:
                options.extend(local_wildcard_dict[keyword])
            elif '*' in keyword:
                subpattern = keyword.replace('*', '.*').replace('+', '\\+')
                total_patterns = []
                found = False
                for k, v in local_wildcard_dict.items():
                    if re.match(subpattern, k) is not None or re.match(subpattern, k + '/') is not None:
                        total_patterns.extend(v)
                        found = True

                if found:
                    options.extend(total_patterns)
            elif '/' not in keyword:
                string_fallback = string.replace(f"__{match}__", f"__*/{match}__", 1)
                options.extend(get_wildcard_options(string_fallback))

        return options

    def replace_wildcard(string: str) -> Tuple[str, bool]:
        # Replace __keyword__ patterns.
        matches = RE_WILDCARD_PATTERN.findall(string)

        replacements_found = False

        for match in matches:
            keyword = match.lower()
            keyword = wildcard_normalize(keyword)
            if keyword in local_wildcard_dict:
                # Parse probability weights
                adjusted_probabilities = []
                total_prob = 0.0
                options = local_wildcard_dict[keyword]
                for option in options:
                    parts = option.split('::', 1)
                    if len(parts) == 2 and is_numeric_string(parts[0].strip()):
                        config_value = float(parts[0].strip())
                    else:
                        config_value = 1.0

                    adjusted_probabilities.append(config_value)
                    total_prob += config_value

                normalized_probabilities = [prob / total_prob for prob in adjusted_probabilities]
                selected_item = random_gen.choice(options, p=normalized_probabilities, replace=False)
                replacement = RE_PROBABILITY_PREFIX.sub('', selected_item, count=1)
                replacement = strip_raffle_prefix(replacement)
                replacements_found = True
                string = string.replace(f"__{match}__", replacement, 1)
            elif '*' in keyword:
                subpattern = keyword.replace('*', '.*').replace('+', '\\+')
                total_patterns = []
                found = False
                for k, v in local_wildcard_dict.items():
                    if re.match(subpattern, k) is not None or re.match(subpattern, k + '/') is not None:
                        total_patterns.extend(v)
                        found = True

                if found:
                    replacement = random_gen.choice(total_patterns)
                    replacement = strip_raffle_prefix(replacement)
                    replacements_found = True
                    string = string.replace(f"__{match}__", replacement, 1)
            elif '/' not in keyword:
                string_fallback = string.replace(f"__{match}__", f"__*/{match}__", 1)
                string, replacements_found = replace_wildcard(string_fallback)

        return string, replacements_found

    # Main replacement loop with depth limit to prevent infinite loops
    replace_depth = 100
    stop_unwrap = False
    while not stop_unwrap and replace_depth > 1:
        replace_depth -= 1

        # Handle quantifiers like 3#__keyword__
        option_quantifier = [e.groupdict() for e in RE_WildCardQuantifier.finditer(text)]
        for match in option_quantifier:
            keyword = match['keyword'].lower()
            quantifier = int(match['quantifier']) if match['quantifier'] else 1
            replacement = '__|__'.join([keyword] * quantifier)
            wilder_keyword = keyword.replace('*', '\\*')
            RE_TEMP = re.compile(fr"(?P<quantifier>\d+)#__(?P<keyword>{wilder_keyword})__", re.IGNORECASE)
            text = RE_TEMP.sub(f"__{replacement}__", text)

        # Pass 1: replace options {a|b|c}
        pass1, is_replaced1 = replace_options(text)

        while is_replaced1:
            pass1, is_replaced1 = replace_options(pass1)

        # Pass 2: replace wildcards __keyword__
        text, is_replaced2 = replace_wildcard(pass1)
        stop_unwrap = not is_replaced1 and not is_replaced2

    return text



