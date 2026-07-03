# Detection & Bounding Box Utilities
#
# Shared detection utilities for all VLM backends (Qwen, Florence-2, etc.)
# Handles bounding box drawing, coordinate parsing, NMS filtering, and
# detection JSON parsing from model outputs.

import re
import json
import math
import numpy as np  # type: ignore
import torch  # type: ignore
from typing import Any, Tuple, List, Optional
from PIL import Image, ImageColor, ImageDraw, ImageFont  # type: ignore

from .logger import log


_LOG_PREFIX = "Detection"


# ==============================================================================
# TENSOR TO PIL CONVERSION
# ==============================================================================

def tensor_to_pil(tensor):
    # Convert a ComfyUI image tensor to PIL Image.
    # Handles [B,H,W,C] (takes first frame) and [H,W,C].
    # Returns None if tensor is None.
    if tensor is None:
        return None
    if tensor.dim() == 4:
        tensor = tensor[0]
    array = (tensor.cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
    return Image.fromarray(array)


# ==============================================================================
# IMAGE RESIZE FOR VLM BACKENDS
# ==============================================================================

# Max pixel limits per backend type (matches each backend's internal handling)
VLM_MAX_PIXELS_TRANSFORMERS = 1_003_520   # ~1MP  (1280 * 28 * 28, HuggingFace Qwen default — fallback only)
VLM_MAX_PIXELS_DOCKER = 2_097_152         # ~2MP  (2 << 20, Ollama/vLLM default)
VLM_MAX_PIXELS_GENERIC = 2_097_152        # ~2MP  generic fallback for non-Qwen Transformers
VLM_PATCH_FACTOR = 28                     # patch_size(14) * spatial_merge_size(2) for Qwen2.5-VL


# Per-model-type pre-resize cap for the Transformers backend.
# These are upstream sanity guards — the processor always re-resizes to its
# own model-specific limit. Keys must be ModelType values (string), set high
# enough that we don't strip detail the processor would have used, low enough
# to keep CPU memory + decode time reasonable.
#
# Processor internals (from transformers source):
#   Pixtral/Mistral3: longest_edge=1024..1540, patch=16  → ~1.0–2.4 MP
#   LLaVA:            fixed image_size=336 (center-cropped) → 0.11 MP
#   mLLaMA:           up to 4 tiles of 560² each            → ~1.3 MP
#   Florence-2:       fixed 768²                            → 0.59 MP
#   Qwen2/2.5-VL:     dynamic patching, default max_pixels  → up to ~12 MP
VLM_MAX_PIXELS_BY_MODEL_TYPE = {
    "qwenvl":   12_582_912,   # ~12MP — Qwen2/2.5-VL dynamic patcher native ceiling
    "mistral3":  2_500_000,   # ~2.5MP — Pixtral processor (image_size=1540)
    "llava":     1_000_000,   # ~1MP — center-cropped to 336² anyway
    "mllama":    2_097_152,   # ~2MP — tiled up to 4×560²
    "florence2":   786_432,   # ~0.78MP — fixed 768²
}


def get_max_pixels_for_model_type(model_type: Any) -> int:
    # Resolve the pre-resize cap for a given ModelType (or fallback).
    # Accepts a ModelType enum or its string value.
    if model_type is None:
        return VLM_MAX_PIXELS_GENERIC
    val = getattr(model_type, "value", model_type)
    if isinstance(val, str):
        return VLM_MAX_PIXELS_BY_MODEL_TYPE.get(val, VLM_MAX_PIXELS_GENERIC)
    return VLM_MAX_PIXELS_GENERIC


def smart_resize_for_vlm(pil_image: Image.Image, max_pixels: int = VLM_MAX_PIXELS_DOCKER, factor: int = VLM_PATCH_FACTOR) -> Tuple[Image.Image, Tuple[int, int]]:
    # Resize an image to fit within max_pixels while preserving aspect ratio.
    # Dimensions are aligned to multiples of `factor` (28 for Qwen VL models).
    # This mirrors the "smart_resize" algorithm used by Qwen's processor
    # and Ollama's Go implementation.
    #
    # Args:
    #     pil_image: Input PIL Image
    #     max_pixels: Maximum total pixel count (width * height)
    #     factor: Alignment factor (dimensions rounded to nearest multiple)
    #
    # Returns:
    #     (resized_image, original_size) where original_size is (width, height)
    #     If no resize needed, returns (original_image, original_size).
    orig_w, orig_h = pil_image.size
    orig_size = (orig_w, orig_h)
    orig_pixels = orig_w * orig_h
    
    # If image is already under the limit, skip resizing entirely
    # The backend (Ollama, vLLM, etc.) handles alignment internally
    if orig_pixels <= max_pixels:
        log.debug(_LOG_PREFIX, f"  VLM: {orig_w}x{orig_h} ({orig_pixels/1e6:.2f}MP) - no resize needed (under {max_pixels/1e6:.2f}MP limit)")
        return pil_image, orig_size
    
    # Image exceeds limit - calculate new dimensions with factor alignment
    # Round to nearest factor
    w_bar = max(factor, round(orig_w / factor) * factor)
    h_bar = max(factor, round(orig_h / factor) * factor)
    
    # Scale down to fit within max_pixels
    beta = math.sqrt(orig_pixels / max_pixels)
    w_bar = max(factor, math.floor(orig_w / beta / factor) * factor)
    h_bar = max(factor, math.floor(orig_h / beta / factor) * factor)
    
    # Only resize if dimensions actually changed
    if w_bar == orig_w and h_bar == orig_h:
        return pil_image, orig_size
    
    if hasattr(Image, "Resampling"):
        resample_filter = Image.Resampling.LANCZOS
    else:
        resample_filter = getattr(Image, "LANCZOS")
    resized = pil_image.resize((w_bar, h_bar), resample_filter)
    log.debug(_LOG_PREFIX, f"  VLM resize: {orig_w}x{orig_h} ({orig_pixels/1e6:.2f}MP) → {w_bar}x{h_bar} ({w_bar*h_bar/1e6:.2f}MP)")
    return resized, orig_size


def scale_bboxes_to_original(data: dict, resized_size: Tuple[int, int], original_size: Tuple[int, int]) -> dict:
    # Scale bounding box coordinates from resized image space back to original image space.
    # Only applies when coords are pixel-based (no coord_range), i.e., text backends
    # like Ollama/llama.cpp that return pixel coordinates.
    #
    # Args:
    #     data: Detection dict with 'bboxes' key
    #     resized_size: (width, height) of the resized image sent to the backend
    #     original_size: (width, height) of the original full-resolution image
    #
    # Returns:
    #     Modified data dict with scaled coordinates
    if not data or 'bboxes' not in data:
        return data
    
    res_w, res_h = resized_size
    orig_w, orig_h = original_size
    
    # No scaling needed if sizes match
    if res_w == orig_w and res_h == orig_h:
        return data
    
    # Only scale pixel-based coords (no coord_range means pixel coords)
    # If coord_range is present, coords are normalized and draw_bboxes handles scaling
    if data.get('coord_range', 0) > 0:
        return data
    
    scale_x = orig_w / res_w
    scale_y = orig_h / res_h
    
    scaled_bboxes = []
    for bbox in data['bboxes']:
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            scaled_bboxes.append([
                bbox[0] * scale_x,
                bbox[1] * scale_y,
                bbox[2] * scale_x,
                bbox[3] * scale_y,
            ])
        else:
            scaled_bboxes.append(bbox)
    
    data['bboxes'] = scaled_bboxes
    log.debug(_LOG_PREFIX, f"  Scaled {len(scaled_bboxes)} bboxes: {res_w}x{res_h} → {orig_w}x{orig_h} (scale {scale_x:.3f}, {scale_y:.3f})")
    return data


# ==============================================================================
# NMS FILTERING
# ==============================================================================

def nms_filter(bboxes: List[List[float]], labels: List[str], iou_threshold: float = 0.5, containment_threshold: float = 0.7) -> Tuple[List[List[float]], List[str], List[int]]:
    # Apply Non-Maximum Suppression to remove overlapping bounding boxes.
    # Uses area-based sorting (smaller boxes preferred) and containment-based suppression.
    #
    # Args:
    #     bboxes: List of bounding boxes in [x1, y1, x2, y2] format
    #     labels: List of labels corresponding to each bbox
    #     iou_threshold: IoU threshold for suppression (default: 0.5)
    #     containment_threshold: If smaller box is contained this much in larger box, suppress larger (default: 0.7)
    #
    # Returns:
    #     Tuple of (filtered_bboxes, filtered_labels, keep_indices)
    #     keep_indices: Original indices of kept detections (for syncing with instance_masks)
    if not bboxes or len(bboxes) == 0:
        return [], [], []
    
    # Convert to numpy for easier computation
    boxes = np.array(bboxes, dtype=float)
    # Defensive: handle flat single-box [x1, y1, x2, y2] → reshape to [[...]]
    if boxes.ndim == 1 and boxes.shape[0] == 4:
        boxes = boxes.reshape(1, 4)
        if isinstance(bboxes, list) and len(bboxes) == 4 and all(isinstance(c, (int, float)) for c in bboxes):
            bboxes = [list(bboxes)]  # type: ignore
    if boxes.ndim != 2 or boxes.shape[1] != 4:
        return [], [], []
    
    # Calculate areas
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    
    # Sort by area ascending (smaller boxes first = preferred, more specific detections)
    order = np.argsort(areas)
    
    keep = []
    suppressed = set()
    
    while len(order) > 0:
        i = order[0]
        
        if i in suppressed:
            order = order[1:]
            continue
            
        keep.append(i)
        
        if len(order) == 1:
            break
        
        # Calculate IoU and containment with remaining boxes
        remaining = order[1:]
        xx1 = np.maximum(x1[i], x1[remaining])
        yy1 = np.maximum(y1[i], y1[remaining])
        xx2 = np.minimum(x2[i], x2[remaining])
        yy2 = np.minimum(y2[i], y2[remaining])
        
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        intersection = w * h
        
        # Standard IoU calculation
        iou = intersection / (areas[i] + areas[remaining] - intersection + 1e-6)
        
        # Containment: what fraction of the current (smaller) box is inside each remaining (larger) box?
        # If a large box mostly contains this small box, suppress the large box
        containment = intersection / (areas[i] + 1e-6)
        
        # Suppress boxes that either:
        # 1. Have high IoU with current box (standard NMS)
        # 2. Mostly contain the current smaller box (containment-based suppression)
        suppress_mask = (iou > iou_threshold) | (containment > containment_threshold)
        
        for idx, should_suppress in enumerate(suppress_mask):
            if should_suppress:
                suppressed.add(remaining[idx])
        
        order = order[1:]
    
    # Filter bboxes and labels using keep indices
    keep_indices = list(keep)
    filtered_bboxes = [bboxes[i] for i in keep_indices]
    filtered_labels = [labels[i] for i in keep_indices] if labels else []
    
    return filtered_bboxes, filtered_labels, keep_indices


# ==============================================================================
# FLORENCE-2 LOCATION TOKEN PARSING
# ==============================================================================

def parse_florence_location_tokens(text: str, width: int, height: int) -> dict:
    # Parse Florence-2 location tokens manually when processor doesn't parse properly.
    #
    # Supports multiple formats:
    # - Bboxes: "label<loc_x1><loc_y1><loc_x2><loc_y2>" (4 tokens)
    # - Polygons: "label<loc_x1><loc_y1><loc_x2><loc_y2>...<loc_xn><loc_yn>" (4+ tokens, multiples of 2)
    # - Quad boxes: "label<loc_x1><loc_y1><loc_x2><loc_y2><loc_x3><loc_y3><loc_x4><loc_y4>" (8 tokens for OCR)
    #
    # Location tokens are normalized to 0-999 range.
    #
    # Args:
    #     text: Raw model output with location tokens
    #     width: Image width in pixels
    #     height: Image height in pixels
    #
    # Returns:
    #     Dict with 'bboxes'/'quad_boxes'/'polygons' and 'labels'
    # Pattern to match label followed by location tokens
    # Captures label and all subsequent <loc_###> tokens
    pattern = r'([^<]+?)((?:<loc_\d+>)+)'
    matches = re.findall(pattern, text)
    
    if not matches:
        return {}
    
    bboxes = []
    labels = []
    polygons = []
    quad_boxes = []
    
    for match in matches:
        label = match[0].strip()
        loc_tokens = match[1]
        
        # Extract all location values
        locs = [int(x) for x in re.findall(r'<loc_(\d+)>', loc_tokens)]
        
        if len(locs) < 4:
            continue  # Invalid
        
        # Denormalize from 0-999 to pixel coordinates
        coords = []
        for i, loc in enumerate(locs):
            if i % 2 == 0:  # x coordinate
                coords.append((loc / 999.0) * width)
            else:  # y coordinate
                coords.append((loc / 999.0) * height)
        
        # Classify by coordinate count
        if len(coords) == 4:
            # Regular bbox: [x1, y1, x2, y2]
            bboxes.append(coords)
            labels.append(label)
        elif len(coords) == 8:
            # Quad box (OCR): [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            quad = [[coords[i], coords[i+1]] for i in range(0, 8, 2)]
            quad_boxes.append(quad)
            labels.append(label)
        elif len(coords) > 4 and len(coords) % 2 == 0:
            # Polygon: [[x1,y1], [x2,y2], ...]
            poly = [[coords[i], coords[i+1]] for i in range(0, len(coords), 2)]
            polygons.append(poly)
            labels.append(label)
    
    # Return appropriate format based on what was found
    result = {}
    if bboxes:
        result['bboxes'] = bboxes
        result['labels'] = labels
    if quad_boxes:
        result['quad_boxes'] = quad_boxes
        if 'labels' not in result:
            result['labels'] = labels
    if polygons:
        result['polygons'] = polygons
        if 'labels' not in result:
            result['labels'] = labels
    
    return result


# ==============================================================================
# QWEN DETECTION JSON PARSING
# ==============================================================================

def parse_qwen_detection_json(text: str, image_size: Optional[Tuple[int, int]] = None, fallback_label: str = "") -> Tuple[dict, str]:
    # Parse Qwen detection JSON output and convert to standard format.
    #
    # Handles multiple Qwen formats:
    # 1. Object format (current): {"bboxes": [[x1,y1,x2,y2], ...], "labels": ["obj1", ...]}
    # 2. Array format (split): [{"bboxes": [...]}, {"labels": [...]}]
    # 3. Array format (no labels): [{"bboxes": [...]}, {"bboxes": [...]}]
    # 4. Array format (legacy): [{"bbox_2d": [x1,y1,x2,y2], "label": "obj1"}, ...]
    #
    # Coordinate system detection:
    # - If any coordinate > 1000: assumes pixel coordinates (no coord_range set)
    # - If all coordinates <= 1000: assumes [0, 1000) normalized range (coord_range=1000)
    # - If image_size provided and coordinates exceed image dimensions: clamps to image
    #
    # Standard format:
    # {
    #     "bboxes": [[x1, y1, x2, y2], [x1, y1, x2, y2], ...],
    #     "labels": ["object1", "object2", ...],
    #     "coord_range": 1000  # Only set if coords appear to be in [0, 1000) range
    # }
    #
    # Args:
    #     text: Raw model output text potentially containing detection JSON
    #     fallback_label: Label to use when model doesn't output labels (e.g. the search term)
    #     image_size: Optional (width, height) tuple for coordinate validation
    #
    # Returns (parsed_dict, cleaned_text) tuple.
    # - If detection found: (dict with bboxes/labels, text with JSON removed)
    # - If no detection: ({}, original text)
    
    def _normalize_bboxes(bboxes):
        # Normalize bboxes: convert string coords like "480,440,543,473" to [480,440,543,473].
        # Handles list/tuple (already good), string (comma-separated), and nested formats.
        # Also handles flat single-box format [x1, y1, x2, y2] by wrapping as [[x1, y1, x2, y2]].
        if (isinstance(bboxes, (list, tuple)) and len(bboxes) == 4
                and all(isinstance(c, (int, float)) for c in bboxes)):
            bboxes = [list(bboxes)]
        normalized = []
        for bbox in bboxes:
            if isinstance(bbox, str):
                # String format: "480,440,543,473"
                parts = [float(c.strip()) for c in bbox.split(",") if c.strip()]
                normalized.append(parts)
            elif isinstance(bbox, (list, tuple)):
                # Already a list/tuple — ensure values are floats (could contain strings)
                normalized.append([float(c) if not isinstance(c, (int, float)) else c for c in bbox])
            else:
                normalized.append(bbox)
        return normalized

    def _detect_coord_range(bboxes, image_size):
        # Determine if coordinates are in [0,1000) normalized range or pixel range.
        # Returns 1000 if normalized, 0 if pixel coordinates.
        #
        # Qwen VL outputs normalized [0,1000) coords but sometimes slightly overshoots
        # (e.g. 1035). When image_size is known, we use it to disambiguate:
        # - If image is much larger than max_coord → normalized with overshoot
        # - If max_coord is close to image dimensions → pixel coordinates
        if not bboxes:
            return 1000  # Default to normalized
        
        # Flatten all coordinate values
        all_coords = []
        for bbox in bboxes:
            if isinstance(bbox, (list, tuple)):
                all_coords.extend([float(c) for c in bbox])
        
        if not all_coords:
            return 1000
        
        max_coord = max(all_coords)
        
        # If image_size is known, use it to disambiguate
        if image_size:
            img_w, img_h = image_size
            img_max = max(img_w, img_h)
            
            # If max_coord is well below image dimensions, coords are normalized [0,~1000)
            # even if they slightly overshoot 1000 (Qwen VL known behavior)
            if max_coord < img_max * 0.7:
                log.debug(_LOG_PREFIX, f"  Coords appear normalized [0,~1000) (max={max_coord:.0f}, image={img_w}x{img_h})")
                return 1000
            
            # If max_coord is close to image dimensions, these are pixel coordinates
            if max_coord > 1000:
                log.debug(_LOG_PREFIX, f"  Coords appear to be pixels (max={max_coord:.0f}, image={img_w}x{img_h})")
                return 0
        else:
            # No image_size: use threshold with tolerance for Qwen overshoot
            if max_coord > 1100:
                log.debug(_LOG_PREFIX, f"  Coords appear to be pixels (max={max_coord:.0f} > 1100, no image_size)")
                return 0
        
        # Default: assume [0,1000) normalized (standard Qwen VL convention)
        log.debug(_LOG_PREFIX, f"  Assuming normalized [0,1000) coords (max={max_coord:.0f})")
        return 1000
    
    # Try to find JSON object or array in the text
    # First try object format: {...}
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        try:
            json_str = json_match.group(0)
            data = json.loads(json_str)
            
            # Check if it's already in standard format
            if isinstance(data, dict) and 'bboxes' in data:
                bboxes = _normalize_bboxes(data['bboxes'])
                data['bboxes'] = bboxes
                # Generate fallback labels if missing
                if 'labels' not in data or not data['labels']:
                    _fb = fallback_label.strip() if fallback_label else "object"
                    data['labels'] = [_fb] * len(bboxes)
                labels = data['labels']
                
                # Pad labels if fewer than bboxes (model may output one label for multiple boxes)
                if isinstance(labels, list) and len(labels) < len(bboxes):
                    pad_label = labels[0] if labels else (fallback_label.strip() if fallback_label else "object")
                    labels.extend([pad_label] * (len(bboxes) - len(labels)))
                    data['labels'] = labels
                
                # Validate format
                if isinstance(bboxes, list) and isinstance(labels, list) and len(bboxes) == len(labels):
                    log.debug(_LOG_PREFIX, f"Found Qwen detection JSON: {len(bboxes)} boxes")
                    # Detect coordinate system: [0,1000) normalized vs pixel coords
                    coord_range = _detect_coord_range(bboxes, image_size)
                    if coord_range > 0:
                        data['coord_range'] = coord_range
                    # Remove JSON from text and return cleaned version
                    cleaned_text = text.replace(json_str, '').strip()
                    if not cleaned_text:
                        cleaned_text = f"Detected {len(bboxes)} object(s): {', '.join(labels)}"
                    return (data, cleaned_text)
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            pass
    
    # Try array format: [...]
    json_match = re.search(r'\[[\s\S]*\]', text)
    if not json_match:
        return ({}, text)
    
    try:
        json_str = json_match.group(0)
        data = json.loads(json_str)
        
        # Check if it's Qwen detection format (list of objects)
        if not isinstance(data, list) or len(data) == 0:
            return ({}, text)
        
        # Convert to standard format
        bboxes = []
        labels = []
        
        # Try split/multi-object format: [{bboxes: [...]}, {labels: [...]}]
        # or no-label format: [{bboxes: [...]}, {bboxes: [...]}]
        # Ollama may output bboxes and labels as separate objects, or omit labels entirely
        if isinstance(data[0], dict):
            all_bboxes = []
            all_labels = []
            for item in data:
                if isinstance(item, dict):
                    if 'bboxes' in item:
                        item_bboxes = item['bboxes']
                        if isinstance(item_bboxes, list):
                            all_bboxes.extend(item_bboxes)
                    if 'labels' in item:
                        item_labels = item['labels']
                        if isinstance(item_labels, list):
                            all_labels.extend(item_labels)
            if all_bboxes:
                bboxes = all_bboxes
                labels = all_labels
        
        # Try legacy per-item format: [{bbox_2d: [...], label: "..."}, ...]
        if not bboxes and isinstance(data[0], dict) and 'bbox_2d' in data[0]:
            for item in data:
                if 'bbox_2d' in item and 'label' in item:
                    bboxes.append(item['bbox_2d'])
                    labels.append(item['label'])
        
        # Generate fallback labels if bboxes found but no labels
        if bboxes and not labels:
            _fb = fallback_label.strip() if fallback_label else "object"
            labels = [_fb] * len(bboxes)
        
        # Pad labels if fewer than bboxes
        if bboxes and labels and len(labels) < len(bboxes):
            pad_label = labels[0] if labels else (fallback_label.strip() if fallback_label else "object")
            labels.extend([pad_label] * (len(bboxes) - len(labels)))
        
        if bboxes and labels:
            bboxes = _normalize_bboxes(bboxes)
            # Detect coordinate system: [0,1000) normalized vs pixel coords
            coord_range = _detect_coord_range(bboxes, image_size)
            converted: dict[str, Any] = {
                'bboxes': bboxes,
                'labels': labels,
            }
            if coord_range > 0:
                converted['coord_range'] = coord_range
            log.debug(_LOG_PREFIX, f"Converted Qwen detection JSON: {len(bboxes)} boxes")
            # Remove JSON from text and return cleaned version
            cleaned_text = text.replace(json_str, '').strip()
            if not cleaned_text:
                cleaned_text = f"Detected {len(bboxes)} object(s): {', '.join(labels)}"
            return (converted, cleaned_text)
        
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        log.warning(_LOG_PREFIX, f"Failed to parse Qwen detection JSON: {e}")
    
    return ({}, text)


# Backward-compat alias (underscore-prefixed name used in some imports)
_parse_qwen_detection_json = parse_qwen_detection_json


# ==============================================================================
# BOUNDING BOX DRAWING
# ==============================================================================

def draw_bboxes(image: Any, data: dict) -> Any:
    # Draw bounding boxes, quad boxes, and polygons on image and return as tensor.
    #
    # Handles coordinate rescaling automatically when data contains 'coord_range'
    # (e.g. Qwen VL outputs coords in [0, 1000) normalized range).
    #
    # Args:
    #     image: Input image tensor
    #     data: Detection data dict with 'bboxes'/'quad_boxes'/'polygons' and 'labels'
    #           Optional 'coord_range' key triggers coordinate rescaling.
    #
    # Returns:
    #     Image tensor with drawn annotations
    log.debug(_LOG_PREFIX, f"draw_bboxes: data keys={list(data.keys()) if data else 'None'}")
    
    # Convert tensor to PIL
    if image is None:
        raise ValueError("Image required for drawing")
    
    if isinstance(image, torch.Tensor):
        if image.dim() == 4:
            image = image[0]  # Take first from batch
        array = (image.cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
        pil_image = Image.fromarray(array)
    else:
        pil_image = image
    
    # Handle empty data - just return original image as tensor
    if not data:
        log.debug(_LOG_PREFIX, "  No detection data, returning original image")
        img_array = np.array(pil_image).astype(np.float32) / 255.0
        return torch.from_numpy(img_array).unsqueeze(0)
    
    # Create a copy to draw on
    draw_image = pil_image.copy()
    draw = ImageDraw.Draw(draw_image)
    
    # Get detection data
    bboxes = data.get("bboxes", []) or []
    quad_boxes = data.get("quad_boxes", []) or []
    polygons = data.get("polygons", []) or []
    labels = data.get("labels", []) or []
    
    log.debug(_LOG_PREFIX, f"  bboxes={len(bboxes)}, quad_boxes={len(quad_boxes)}, polygons={len(polygons)}, labels={len(labels)}")
    
    # If all are empty, return original image
    if not bboxes and not quad_boxes and not polygons:
        log.debug(_LOG_PREFIX, "  All detection lists empty, returning original image")
        img_array = np.array(pil_image).astype(np.float32) / 255.0
        return torch.from_numpy(img_array).unsqueeze(0)
    
    # Color palette for labels - vibrant colors with good visibility
    colormap = ['red', 'lime', 'blue', 'yellow', 'cyan', 'magenta', 'orange', 'purple', 
                'green', 'pink', 'gold', 'turquoise', 'coral', 'violet', 'springgreen', 
                'deeppink', 'dodgerblue', 'tomato', 'limegreen', 'hotpink']
    
    # Get image dimensions (needed for font scaling and label clamping)
    img_w, img_h = pil_image.size
    
    # Try to load font - scale size relative to image dimensions for readability
    font_size = max(16, min(img_w, img_h) // 60)  # ~18 for 1080p, ~24 for 1440p, ~32 for 4K
    line_width = max(2, font_size // 7)  # ~3 for 1080p, ~3 for 1440p
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()
    
    # Check if coordinates need rescaling from Qwen's [0, 1000) normalized range
    coord_range = data.get('coord_range', 0)
    if coord_range > 0:
        scale_x = img_w / coord_range
        scale_y = img_h / coord_range
        log.debug(_LOG_PREFIX, f"  Rescaling coords from [0,{coord_range}) to {img_w}x{img_h} (scale {scale_x:.3f}, {scale_y:.3f})")
    else:
        scale_x = 1.0
        scale_y = 1.0
    
    # Draw regular bounding boxes
    for i, bbox in enumerate(bboxes):
        # Validate bbox has 4 elements
        if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
            log.debug(_LOG_PREFIX, f"  Skipping invalid bbox[{i}]: {bbox}")
            continue
        
        try:
            x1 = float(bbox[0]) * scale_x
            y1 = float(bbox[1]) * scale_y
            x2 = float(bbox[2]) * scale_x
            y2 = float(bbox[3]) * scale_y
            # Standardize: ensure x1 < x2 and y1 < y2 (models can return inverted coords)
            if x2 < x1:
                x1, x2 = x2, x1
            if y2 < y1:
                y1, y2 = y2, y1
            # Use fixed red color for bboxes (original behavior)
            color = 'red'
            
            # Draw rectangle
            draw.rectangle([x1, y1, x2, y2], outline=color, width=line_width)
            
            # Draw label if available (format: "index.label" like "0.eye", "1.face")
            if i < len(labels):
                indexed_label = f"{i}.{labels[i]}"  # Match florence2 format: "0.label"
                # Get text bbox for background
                text_bbox = draw.textbbox((x1, y1), indexed_label, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]
                
                # Position label above bbox, clamp to image boundaries
                text_x = x1
                text_y = y1 - text_height - 4
                # Clamp horizontal: keep label inside image
                if text_x < 0:
                    text_x = 0
                elif text_x + text_width + 4 > img_w:
                    text_x = img_w - text_width - 4
                # If label goes above image, move it below top edge of bbox
                if text_y < 0:
                    text_y = y2
                
                # Draw background rectangle (black, not red - so labels don't appear in red mask extraction)
                draw.rectangle([text_x, text_y, text_x + text_width + 4, text_y + text_height + 4], fill='black')
                # Draw text
                draw.text((text_x + 2, text_y + 2), indexed_label, fill='white', font=font)
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Error drawing bbox[{i}]: {e}")
    
    # Draw quad boxes (for OCR/text detection) with semi-transparent fill
    for i, quad_box in enumerate(quad_boxes):
        try:
            # quad_box format: [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            if not quad_box or len(quad_box) < 4:
                continue
            if isinstance(quad_box[0], (list, tuple)):
                points = [(float(pt[0]) * scale_x, float(pt[1]) * scale_y) for pt in quad_box]
            else:
                # Flat list format [x1,y1,x2,y2,...]
                points = [(float(quad_box[j]) * scale_x, float(quad_box[j+1]) * scale_y) for j in range(0, len(quad_box), 2)]
            
            color = colormap[i % len(colormap)]
            # Draw semi-transparent fill (like Florence2) + outline
            overlay = Image.new('RGBA', draw_image.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            fill_color = ImageColor.getrgb(color) + (70,)  # ~27% opacity fill
            overlay_draw.polygon(points, outline=color, fill=fill_color, width=line_width)
            draw_image = Image.alpha_composite(draw_image.convert('RGBA'), overlay).convert('RGB')
            draw = ImageDraw.Draw(draw_image)
            
            # Draw label with boundary clamping
            if points:
                label = str(i) if i >= len(labels) else str(labels[i])
                text_bbox = draw.textbbox(points[0], label, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]
                text_x = points[0][0]
                text_y = points[0][1] - text_height - 4
                if text_x < 0:
                    text_x = 0
                elif text_x + text_width + 4 > img_w:
                    text_x = img_w - text_width - 4
                if text_y < 0:
                    text_y = points[0][1] + 4
                draw.rectangle([text_x, text_y, text_x + text_width + 4, text_y + text_height + 4], fill=color)
                draw.text((text_x + 2, text_y + 2), label, fill='white', font=font)
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Error drawing quad_box[{i}]: {e}")
    
    # Draw polygons with semi-transparent fill
    for i, polygon in enumerate(polygons):
        try:
            # polygon format: [[x1,y1], [x2,y2], ...] or [x1,y1,x2,y2,...]
            if not polygon or len(polygon) < 3:
                continue
            if isinstance(polygon[0], (list, tuple)):
                points = [(float(pt[0]) * scale_x, float(pt[1]) * scale_y) for pt in polygon]
            else:
                # Flat list format
                points = [(float(polygon[j]) * scale_x, float(polygon[j+1]) * scale_y) for j in range(0, len(polygon), 2)]
            
            # Clamp polygon points to image boundaries (like Florence2)
            points = [(max(0, min(p[0], img_w - 1)), max(0, min(p[1], img_h - 1))) for p in points]
            
            color = colormap[i % len(colormap)]
            # Draw semi-transparent fill (like Florence2) + outline
            overlay = Image.new('RGBA', draw_image.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            fill_color = ImageColor.getrgb(color) + (70,)  # ~27% opacity fill
            overlay_draw.polygon(points, outline=color, fill=fill_color, width=line_width)
            draw_image = Image.alpha_composite(draw_image.convert('RGBA'), overlay).convert('RGB')
            draw = ImageDraw.Draw(draw_image)
            
            # Draw label with boundary clamping
            if points:
                label = str(i) if i >= len(labels) else str(labels[i])
                text_bbox = draw.textbbox(points[0], label, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]
                text_x = points[0][0]
                text_y = points[0][1] - text_height - 4
                if text_x < 0:
                    text_x = 0
                elif text_x + text_width + 4 > img_w:
                    text_x = img_w - text_width - 4
                if text_y < 0:
                    text_y = points[0][1] + 4
                draw.rectangle([text_x, text_y, text_x + text_width + 4, text_y + text_height + 4], fill=color)
                draw.text((text_x + 2, text_y + 2), label, fill='white', font=font)
        except Exception as e:
            log.debug(_LOG_PREFIX, f"  Error drawing polygon[{i}]: {e}")
    
    # Convert back to tensor format
    img_array = np.array(draw_image).astype(np.float32) / 255.0
    tensor_out = torch.from_numpy(img_array).unsqueeze(0)  # Add batch dimension
    
    return tensor_out


# ==============================================================================
# MASK GENERATION
# ==============================================================================

def mask_from_bbox(height: int, width: int, bbox: List[float]) -> torch.Tensor:
    # Create a binary mask from a bounding box.
    #
    # Args:
    #     height: Full image height
    #     width: Full image width
    #     bbox: [x1, y1, x2, y2] in pixel coordinates
    #
    # Returns:
    #     torch.Tensor [H, W] float32, 1.0 inside bbox, 0.0 outside
    mask = torch.zeros((height, width), dtype=torch.float32)
    x1 = max(0, int(bbox[0]))
    y1 = max(0, int(bbox[1]))
    x2 = min(width, int(bbox[2]))
    y2 = min(height, int(bbox[3]))
    mask[y1:y2, x1:x2] = 1.0
    return mask


def mask_from_polygon(height: int, width: int, polygon_points: List) -> torch.Tensor:
    # Create a binary mask from polygon points using PIL.
    #
    # Args:
    #     height: Full image height
    #     width: Full image width
    #     polygon_points: List of [x, y] pairs or flat list [x1,y1,x2,y2,...]
    #
    # Returns:
    #     torch.Tensor [H, W] float32, 1.0 inside polygon, 0.0 outside
    from PIL import ImageDraw as PilDraw

    mask_img = Image.new("L", (width, height), 0)
    draw = PilDraw.Draw(mask_img)

    # Convert to flat tuple list for PIL
    if polygon_points and isinstance(polygon_points[0], (list, tuple)):
        points = [(float(p[0]), float(p[1])) for p in polygon_points]
    else:
        # Flat list: [x1,y1,x2,y2,...]
        points = [(float(polygon_points[i]), float(polygon_points[i + 1]))
                  for i in range(0, len(polygon_points), 2)]

    if len(points) >= 3:
        draw.polygon(points, fill=255)

    arr = np.array(mask_img).astype(np.float32) / 255.0
    return torch.from_numpy(arr)


def mask_from_instance_mask(height: int, width: int, numpy_mask: np.ndarray) -> torch.Tensor:
    # Convert a numpy instance mask to a torch tensor at the target size.
    #
    # Args:
    #     height: Target height
    #     width: Target width
    #     numpy_mask: numpy array [H, W] float32 or bool
    #
    # Returns:
    #     torch.Tensor [H, W] float32
    mask = numpy_mask.astype(np.float32)
    if mask.shape[0] != height or mask.shape[1] != width:
        mask_pil = Image.fromarray((mask * 255).astype(np.uint8))
        if hasattr(Image, "Resampling"):
            resample_filter = Image.Resampling.NEAREST
        else:
            resample_filter = getattr(Image, "NEAREST")
        mask_pil = mask_pil.resize((width, height), resample_filter)
        mask = np.array(mask_pil).astype(np.float32) / 255.0
    return torch.from_numpy(mask)


def combined_mask(height: int, width: int, data: dict, instance_masks: Optional[List[np.ndarray]] = None) -> torch.Tensor:
    # Create a combined binary mask from all detections in data.
    # Uses instance masks when available, falls back to bbox masks.
    #
    # Args:
    #     height: Full image height
    #     width: Full image width
    #     data: Detection data dict with bboxes, labels, optionally polygons
    #     instance_masks: Optional list of numpy masks from YOLO segmentation
    #
    # Returns:
    #     torch.Tensor [1, H, W] float32 (batch dim for ComfyUI mask format)
    mask = torch.zeros((height, width), dtype=torch.float32)
    bboxes = data.get("bboxes", [])
    polygons = data.get("polygons", [])

    for i in range(len(bboxes)):
        if instance_masks and i < len(instance_masks) and instance_masks[i] is not None:
            # Use instance mask from YOLO segmentation
            m = mask_from_instance_mask(height, width, instance_masks[i])
            mask = torch.max(mask, m)
        elif polygons and i < len(polygons):
            # Use polygon mask from Florence segmentation
            m = mask_from_polygon(height, width, polygons[i])
            mask = torch.max(mask, m)
        elif i < len(bboxes):
            # Fall back to bbox mask
            m = mask_from_bbox(height, width, bboxes[i])
            mask = torch.max(mask, m)

    return mask.unsqueeze(0)  # [1, H, W]


# ==============================================================================
# SELECT DETECTION (index filtering)
# ==============================================================================

def select_detection(data: dict, index: int, image_height: int, image_width: int,
                     instance_masks: Optional[List[np.ndarray]] = None) -> Tuple[dict, Optional[List[np.ndarray]]]:
    # Filter detection data to a single detection by index.
    #
    # Args:
    #     data: Detection data dict with bboxes, labels, confidences, etc.
    #     index: -1 for all detections, 0+ for specific detection index
    #     image_height: Image height (for logging only)
    #     image_width: Image width (for logging only)
    #     instance_masks: Optional instance masks to filter in sync
    #
    # Returns:
    #     (filtered_data, filtered_instance_masks) — both sliced to match
    if index < 0 or not data:
        return data, instance_masks

    bboxes = data.get("bboxes", [])
    if not bboxes:
        return data, instance_masks

    # Clamp index to valid range
    if index >= len(bboxes):
        log.warning(_LOG_PREFIX, f"select_index={index} exceeds {len(bboxes)} detections, clamping to {len(bboxes) - 1}")
        index = len(bboxes) - 1

    # Build single-element data dict
    filtered = dict(data)
    filtered["bboxes"] = [bboxes[index]]

    labels = data.get("labels", [])
    if labels:
        filtered["labels"] = [labels[index]] if index < len(labels) else ["object"]

    confidences = data.get("confidences", [])
    if confidences:
        filtered["confidences"] = [confidences[index]] if index < len(confidences) else [1.0]

    polygons = data.get("polygons", [])
    if polygons:
        filtered["polygons"] = [polygons[index]] if index < len(polygons) else []

    # Filter instance masks in sync
    filtered_masks = None
    if instance_masks:
        filtered_masks = [instance_masks[index]] if index < len(instance_masks) else []

    return filtered, filtered_masks


# ==============================================================================
# SEGS GENERATION (Impact Pack compatible)
# ==============================================================================

from collections import namedtuple

SEG = namedtuple("SEG",
    ['cropped_image', 'cropped_mask', 'confidence', 'crop_region', 'bbox', 'label', 'control_net_wrapper'],
    defaults=[None])


def _normalize_region(limit: int, start: int, end: int) -> Tuple[int, int]:
    # Clamp and normalize a 1D region to [0, limit).
    if start < 0:
        end -= start
        start = 0
    if end > limit:
        start -= (end - limit)
        end = limit
    if start < 0:
        start = 0
    return start, end


def make_crop_region(image_w: int, image_h: int, bbox: Tuple[int, int, int, int], crop_factor: float) -> Tuple[int, int, int, int]:
    # Expand a bounding box by crop_factor and clamp to image boundaries.
    #
    # Args:
    #     image_w: Image width
    #     image_h: Image height
    #     bbox: (x1, y1, x2, y2) tight detection box
    #     crop_factor: Expansion multiplier (1.0 = no expansion, 3.0 = 3x the bbox area)
    #
    # Returns:
    #     (x1, y1, x2, y2) expanded crop region
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    w = (x2 - x1) * crop_factor
    h = (y2 - y1) * crop_factor

    crop_x1 = int(cx - w / 2)
    crop_y1 = int(cy - h / 2)
    crop_x2 = int(cx + w / 2)
    crop_y2 = int(cy + h / 2)

    crop_x1, crop_x2 = _normalize_region(image_w, crop_x1, crop_x2)
    crop_y1, crop_y2 = _normalize_region(image_h, crop_y1, crop_y2)

    return crop_x1, crop_y1, crop_x2, crop_y2


def dilate_mask(mask: np.ndarray, dilation: int) -> np.ndarray:
    # Dilate (positive) or erode (negative) a binary mask.
    #
    # Args:
    #     mask: numpy array [H, W] float32
    #     dilation: Positive for dilation, negative for erosion, 0 for no-op
    #
    # Returns:
    #     Modified mask numpy array
    if dilation == 0:
        return mask
    import cv2  # type: ignore
    kernel = np.ones((abs(dilation), abs(dilation)), np.uint8)
    if dilation > 0:
        return cv2.dilate(mask, kernel, iterations=1)
    else:
        return cv2.erode(mask, kernel, iterations=1)


def build_segs(data: dict, image_h: int, image_w: int,
               crop_factor: float = 3.0, dilation: int = 0,
               instance_masks: Optional[List[np.ndarray]] = None) -> tuple:
    # Build Impact Pack compatible SEGS tuple from detection data.
    # NOTE: drop_size is NOT applied here — it's applied in the execute flow
    # (step 0b) before build_segs to keep data/segs indices in sync for select_index.
    #
    # Args:
    #     data: Detection data dict with bboxes, labels, confidences
    #     image_h: Reference image height
    #     image_w: Reference image width
    #     crop_factor: Expansion factor for crop regions (default 3.0)
    #     dilation: Mask dilation/erosion amount (default 0)
    #     instance_masks: Optional list of numpy instance masks from YOLO segmentation
    #
    # Returns:
    #     SEGS tuple: ((image_h, image_w), [SEG, ...])
    segs_list = []
    bboxes = data.get("bboxes", [])
    labels = data.get("labels", [])
    confidences = data.get("confidences", [])

    for i, bbox in enumerate(bboxes):
        x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        label = labels[i] if i < len(labels) else "object"
        conf = confidences[i] if i < len(confidences) else 1.0

        crop_region = make_crop_region(image_w, image_h, (x1, y1, x2, y2), crop_factor)
        crop_x1, crop_y1, crop_x2, crop_y2 = crop_region
        crop_h = crop_y2 - crop_y1
        crop_w = crop_x2 - crop_x1

        if instance_masks and i < len(instance_masks) and instance_masks[i] is not None:
            # Use instance mask from YOLO segmentation
            full_mask = instance_masks[i].astype(np.float32)
            # Resize to image dims if needed
            if full_mask.shape[0] != image_h or full_mask.shape[1] != image_w:
                mask_pil = Image.fromarray((full_mask * 255).astype(np.uint8))
                if hasattr(Image, "Resampling"):
                    resample_filter = Image.Resampling.NEAREST
                else:
                    resample_filter = getattr(Image, "NEAREST")
                mask_pil = mask_pil.resize((image_w, image_h), resample_filter)
                full_mask = np.array(mask_pil).astype(np.float32) / 255.0
            cropped_mask = full_mask[crop_y1:crop_y2, crop_x1:crop_x2].copy()
        else:
            # Bbox-based rectangular mask
            cropped_mask = np.zeros((crop_h, crop_w), dtype=np.float32)
            mask_y1 = y1 - crop_y1
            mask_x1 = x1 - crop_x1
            mask_y2 = y2 - crop_y1
            mask_x2 = x2 - crop_x1
            # Clamp to crop region
            mask_y1 = max(0, mask_y1)
            mask_x1 = max(0, mask_x1)
            mask_y2 = min(crop_h, mask_y2)
            mask_x2 = min(crop_w, mask_x2)
            cropped_mask[mask_y1:mask_y2, mask_x1:mask_x2] = 1.0

        if dilation != 0:
            cropped_mask = dilate_mask(cropped_mask, dilation)

        seg = SEG(None, cropped_mask, conf, crop_region, (x1, y1, x2, y2), label, None)
        segs_list.append(seg)

    return ((image_h, image_w), segs_list)
