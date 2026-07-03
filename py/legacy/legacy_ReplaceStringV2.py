# ReplaceStringV2 - Simplified version of V3 with core options only.
# Uses the same tag/prose detection and processing logic as V3.
# For advanced options (shot_style, lighting, age, nsfw_handling, watermark), use V3.

import re
from comfy_api.latest import io #type: ignore
from ...core import CATEGORY
from ...core.logger import log

# Lazy import SmartTextProcessor inside execute to avoid heavy imports during node registration

class RvText_ReplaceStringV2(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Replace String v2 [Eclipse]",
            display_name="⚠ Replace String v2",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.String.Input("string", default="", tooltip="Input string to process."),
                io.String.Input("regex", default="", tooltip="Regular expression pattern to match."),
                io.String.Input("replace_with", default="", tooltip="Replacement string for matches."),
                io.Boolean.Input("remove_instructions", default=False, tooltip="Remove LLM meta-commentary: 'Title:', 'Description:', numbered labels like '1. Composition:', conversational openers like 'Let me describe', and analysis intros."),
                io.Boolean.Input("list_select_first", default=False, tooltip="If enabled, extract the first numbered quoted choice (1.) from LLM output and use it as the result."),
                io.Boolean.Input("list_to_string", default=False, tooltip="If enabled, convert a numbered tips list into a single-line prompt and remove short labels (e.g., 'Lighting:')."),
                io.Boolean.Input("remove_image_style", default=False, tooltip="Remove image style prefixes like 'A digital illustration of', 'anime-style', '3d render', quality tags like 'highly detailed'."),
                io.Boolean.Input("remove_subject", default=False, tooltip="Whether to remove subject description matches."),
                io.Boolean.Input("remove_background", default=False, tooltip="Whether to remove background description matches."),
                io.Boolean.Input("remove_mood", default=False, tooltip="Whether to remove mood description matches."),
                io.Boolean.Input("cleanup", default=False, tooltip="When enabled, trim whitespace and remove surrounding quotes from the final output."),
            ],
            outputs=[
                io.String.Output("string"),
            ],
        )

    @classmethod
    def execute(
        cls,
        string: str,
        regex: str = "",
        replace_with: str = "",
        remove_instructions: bool = False,
        list_select_first: bool = False,
        list_to_string: bool = False,
        remove_image_style: bool = False,
        remove_subject: bool = False,
        remove_background: bool = False,
        remove_mood: bool = False,
        cleanup: bool = False,
    ) -> io.NodeOutput:

        # Process string with regex replacement and optional description removals
        s = string or ""
        
        # Apply custom regex replacement if provided
        if regex and s:
            try:
                pattern = re.compile(regex, re.IGNORECASE)
                s = pattern.sub(replace_with, s)
            except re.error as e:
                # Log error but continue processing
                log.warning("ReplaceStringV2", f"Regex error: {e}")

        # Integrate with SmartTextProcessor
        try:
            from ...core.smart_text_processor import get_default_processor
            from ...core.regex_helper import is_tags_format
            processor = get_default_processor()

            matches_all = []
            
            # Detect input format: tags vs prose
            # Word-level removal is only safe for tag-format input
            input_is_tags = is_tags_format(s)

            # Category mappings for WORD-LEVEL detection
            # NOTE: For prose, many categories use SENTENCE patterns only
            # Word-level removal of "image", "scene", "room", etc. is too aggressive for prose
            flag_to_cat = {
                'remove_subject': 'subjects',
            }
            
            # For TAG format, also use word-level patterns for more categories
            if input_is_tags:
                flag_to_cat['remove_image_style'] = 'image_styles'  # Tags: "photo", "3d render" as standalone
                flag_to_cat['remove_background'] = 'backgrounds'
                flag_to_cat['remove_mood'] = 'moods'  # Matches moods.json category

            to_remove = []
            
            # Build flags dict
            flags = {
                'remove_instructions': remove_instructions,
                'list_select_first': list_select_first,
                'list_to_string': list_to_string,
                'remove_image_style': remove_image_style,
                'remove_subject': remove_subject,
                'remove_background': remove_background,
                'remove_mood': remove_mood,
                'cleanup': cleanup,
            }
            
            # Log which removal options are enabled
            enabled_flags = [flag for flag, value in flags.items() if value]
            if enabled_flags:
                log.debug("ReplaceStringV2", f"Removal options enabled: {', '.join(enabled_flags)}, input_is_tags={input_is_tags}")
            
            # CRITICAL: For instructions, sentence patterns MUST run BEFORE prefix removal
            # Sentence patterns like ^Title:[^\n]*\n+ need to match "Title: Content\n\n" as a whole
            # If we run prefix removal first, it strips "Title:" leaving orphaned content
            
            # Step 1: Handle instruction SENTENCE patterns first (removes entire labeled lines)
            if remove_instructions or list_select_first or list_to_string:
                log.debug("ReplaceStringV2", f"Step 1: Checking instruction sentence patterns")
                log.debug("ReplaceStringV2", f"Text length: {len(s)}, first 100 chars: {s[:100]}")
                instruction_sentence_matches = processor.detect_sentences(s, categories=['instructions'])
                if instruction_sentence_matches:
                    log.debug("ReplaceStringV2", f"Instruction sentence matches: {[m['text'][:40] for m in instruction_sentence_matches]}")
                    # Remove instruction sentence matches immediately
                    s = processor.remove_matches(s, instruction_sentence_matches)
                    log.debug("ReplaceStringV2", f"After instruction sentence removal: {s[:100]}...")
            
            # Step 2: Now handle instruction PREFIX patterns (for remaining prefixes)
            # This catches patterns like "The image shows" that aren't full lines
            if remove_instructions or list_select_first or list_to_string:
                s = processor.remove_prefixes(s, categories=['instructions'])
            
            # Note: numbered labels like "1. Composition: " are handled directly by list regex
            # which skips optional labels. No need to detect/remove them separately.
            
            # Handle remove_image_style - removes style/medium prefixes like "A digital illustration of"
            if remove_image_style:
                # For PROSE: detect image_styles but only remove if at start of text (prefix behavior)
                # This handles "A digital illustration, anime style shoot from behind about..."
                # without removing "illustration" mid-sentence
                # Loop to handle chained prefixes like "The image depicts a cartoon-style illustration of"
                # where removing "The image depicts" reveals "a cartoon-style illustration of" as a new prefix
                if not input_is_tags:
                    max_prefix_passes = 3  # Safety limit to prevent infinite loops
                    for pass_num in range(max_prefix_passes):
                        image_style_matches = processor.detect(s, categories=['image_styles'])
                        if image_style_matches:
                            log.debug("ReplaceStringV2", f"image_style pass {pass_num + 1}: {len(image_style_matches)} matches: {[(m['text'], m['span']) for m in image_style_matches]}")
                        # Filter to only matches starting at or very near position 0 (allowing for leading whitespace)
                        prefix_matches = [m for m in image_style_matches if m['span'][0] <= 2]
                        if not prefix_matches:
                            break
                        log.debug("ReplaceStringV2", f"Found {len(prefix_matches)} image_style prefix matches: {[m['text'] for m in prefix_matches]}")
                        # Remove the longest prefix match (highest priority)
                        prefix_matches.sort(key=lambda m: m['span'][1] - m['span'][0], reverse=True)
                        best_match = prefix_matches[0]
                        # Remove the prefix
                        s = s[best_match['span'][1]:].lstrip(' ,')
                        # Capitalize first letter
                        if s and s[0].islower():
                            s = s[0].upper() + s[1:]
                        log.debug("ReplaceStringV2", f"Removed image_style prefix: '{best_match['text']}', result starts: {s[:50]}...")
                    # After prefix removal, also remove remaining multi-word image style matches
                    # Single words like "scene", "image", "photo" are too generic for prose removal
                    # But compound phrases like "an anime-style", "digital illustration" are safe
                    remaining_matches = processor.detect(s, categories=['image_styles'])
                    compound_matches = [m for m in remaining_matches if len(m['text'].split()) > 1]
                    if compound_matches:
                        to_remove.extend(compound_matches)
                        log.debug("ReplaceStringV2", f"image_style compound matches for removal: {[m['text'] for m in compound_matches]}")
            
            # Now detect patterns on the modified text (after prefix removal)
            for flag, cat in flag_to_cat.items():
                if flags.get(flag):
                    ms = processor.detect(s, categories=[cat])
                    matches_all.extend(ms)
                    to_remove.extend(ms)
            
            # Process sentence patterns for prose-aware removal (PROSE only)
            # These handle complete sentences for background/mood descriptions
            # Note: instruction sentence patterns are handled earlier in Step 1
            sentence_cats = []
            if not input_is_tags:
                # Only use sentence patterns for prose format
                if remove_background:
                    sentence_cats.append('backgrounds')   # "In the background...", "Behind her..."
                if remove_mood:
                    sentence_cats.append('moods')         # "The overall atmosphere is...", "The mood is..."
            
            # Note: instruction sentence patterns (Title:, Description:, composition meta-commentary)
            # are handled earlier in Step 1 to ensure proper ordering with prefix removal
            
            if sentence_cats:
                log.debug("ReplaceStringV2", f"Calling detect_sentences with categories: {sentence_cats}")
                log.debug("ReplaceStringV2", f"Text length: {len(s)}, first 100 chars: {s[:100]}")
                sentence_matches = processor.detect_sentences(s, categories=sentence_cats)
                log.debug("ReplaceStringV2", f"detect_sentences returned {len(sentence_matches)} matches")
                if sentence_matches:
                    matches_all.extend(sentence_matches)
                    to_remove.extend(sentence_matches)
                    log.debug("ReplaceStringV2", f"Sentence patterns matched: {[m['text'][:50] + '...' if len(m['text']) > 50 else m['text'] for m in sentence_matches]}")
            
            # When remove_subject is enabled, also remove NSFW terms (for complete landscape extraction)
            if remove_subject:
                nsfw_matches = processor.detect(s, categories=['nsfw'])
                matches_all.extend(nsfw_matches)
                to_remove.extend(nsfw_matches)
                if nsfw_matches:
                    log.debug("ReplaceStringV2", f"Also removing NSFW terms with subjects: {[m['text'] for m in nsfw_matches]}")

            if to_remove:
                # Build list of categories to preserve (those with flags set to False)
                preserve_categories = []
                for flag, cat in flag_to_cat.items():
                    if not flags.get(flag):
                        preserve_categories.append(cat)
                
                # Log before and after removal
                before_len = len(s)
                before_text = s[:100] + "..." if len(s) > 100 else s
                log.debug("ReplaceStringV2", f"Text before removal (len={before_len}): {before_text}")
                
                s = processor.remove_matches(s, to_remove, preserve_categories=preserve_categories if preserve_categories else None)
                
                after_len = len(s)
                after_text = s[:100] + "..." if len(s) > 100 else s
                log.debug("ReplaceStringV2", f"Text after removal (len={after_len}): {after_text}")
                
                if before_len == after_len:
                    log.warning("ReplaceStringV2", "Text length unchanged after removal - no actual removal occurred!")

            # Handle LLM lists AFTER all removals are applied
            # Priority: if list_select_first is True, it takes precedence over list_to_string
            # Regex captures content after number AND optional label like "1. Multi Word Label: content"
            # Label pattern: capital word + optional more words (inc. lowercase) + colon + space
            if list_select_first:
                # capture numbered/bulleted list items, skip optional "Label: " or "Multi Word Label: " prefix
                # Try inline format first (semicolon-separated): '1. a; 2. b; 3. c'
                items = re.findall(r"\d+[\.)]\s*(?:[A-Z][A-Za-z]*(?:\s+[A-Za-z]+)*:\s+)?([^;\n]+)", s)
                # If no inline items or only one, try multiline format
                if len(items) <= 1:
                    items = re.findall(r"^\s*(?:\d+[\.)]|\d+\s*-|[-\*]+)\s*(?:[A-Z][A-Za-z]*(?:\s+[A-Za-z]+)*:\s+)?(.+)$", s, flags=re.M)
                if items:
                    s = items[0].strip()

            elif list_to_string:
                # Try inline format first (semicolon-separated): '1. a; 2. b; 3. c'
                items = re.findall(r"\d+[\.)]\s*(?:[A-Z][A-Za-z]*(?:\s+[A-Za-z]+)*:\s+)?([^;\n]+)", s)
                # If no inline items or only one, try multiline format
                if len(items) <= 1:
                    items = re.findall(r"^\s*(?:\d+[\.)]|\d+\s*-|[-\*]+)\s*(?:[A-Z][A-Za-z]*(?:\s+[A-Za-z]+)*:\s+)?(.+)$", s, flags=re.M)
                if items:
                    s = ", ".join(i.strip() for i in items)

            # cleanup: remove surrounding quotes and trim
            if cleanup:
                s = s.strip()
                if len(s) >= 2 and ((s[0] == s[-1]) and s[0] in ('"', "'")):
                    s = s[1:-1].strip()

        except Exception as e:
            log.warning('ReplaceStringV2', f'Processor integration failed: {e}')

        # Log final return value
        final_text = s[:100] + "..." if len(s) > 100 else s
        log.debug("ReplaceStringV2", f"Returning final text (len={len(s)}): {final_text}")
        
        return io.NodeOutput(s)
