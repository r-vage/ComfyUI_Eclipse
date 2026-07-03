# RvConversion_DetectionToBboxes - Convert Florence-2 detection data to masks and bboxes
#
# Converts bounding boxes, quad boxes, and polygons from Florence-2 detection
# tasks (caption_to_phrase_grounding, region_proposal, etc.) to masks with
# optional inversion, grow/shrink, and blur operations. Also outputs standardized
# bboxes in SAM2 Ultra compatible format.

import torch  # type: ignore
import numpy as np  # type: ignore
import cv2  # type: ignore
from typing import Any, Optional
from PIL import Image, ImageDraw, ImageFilter #type: ignore
from ..core import CATEGORY
from ..core.logger import log
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "Convert"


def _create_bbox_mask(bbox: list, width: int, height: int) -> Image.Image:
    # Create mask from bounding box [x1, y1, x2, y2].
    # Create black background
    mask = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(mask)

    # bbox format: [x1, y1, x2, y2] - already in absolute pixel coordinates
    # Convert floats to integers and ensure x1 <= x2, y1 <= y2
    x1, y1, x2, y2 = int(round(bbox[0])), int(round(bbox[1])), int(round(bbox[2])), int(round(bbox[3]))
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)

    # Draw filled white rectangle
    draw.rectangle([x1, y1, x2, y2], fill=255)

    return mask


def _create_quad_mask(quad_box: list, width: int, height: int) -> Image.Image:
    # Create mask from quad box [x1, y1, x2, y2, x3, y3, x4, y4].
    # Create black background
    mask = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(mask)

    # quad_box format: 8 coordinates for 4 corners - already in absolute pixels
    # Just convert floats to integers
    points = [(int(round(quad_box[i])), int(round(quad_box[i+1]))) for i in range(0, 8, 2)]

    # Draw filled white polygon
    draw.polygon(points, fill=255)

    return mask


def _create_polygon_mask(polygon: list, width: int, height: int) -> Image.Image:
    # Create mask from polygon [[x1, y1], [x2, y2], ...].
    # Create black background
    mask = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(mask)

    # polygon format: list of [x, y] points - already in absolute pixels
    # Just convert floats to integers
    points = [(int(round(point[0])), int(round(point[1]))) for point in polygon]

    # Draw filled white polygon
    draw.polygon(points, fill=255)

    return mask


def _grow_shrink_mask(mask: Image.Image, amount: int) -> Image.Image:
    # Grow (positive) or shrink (negative) mask regions.
    #
    # Uses morphological operations:
    # - Positive amount: Dilation (grow white regions)
    # - Negative amount: Erosion (shrink white regions)

    if amount == 0:
        return mask

    # Convert to numpy for morphological operations
    mask_np = np.array(mask)

    if amount > 0:
        # Grow mask (dilation)
        # Apply MaxFilter multiple times for larger grow amounts
        for _ in range(abs(amount)):
            mask = mask.filter(ImageFilter.MaxFilter(size=3))
    else:
        # Shrink mask (erosion)
        # Apply MinFilter multiple times for larger shrink amounts
        for _ in range(abs(amount)):
            mask = mask.filter(ImageFilter.MinFilter(size=3))

    return mask


def _standardize_bbox(bbox: list, width: int, height: int) -> list:
    # Standardize bbox to [x1, y1, x2, y2] format with absolute integer coordinates.
    # Ensures x1 < x2 and y1 < y2.

    # Coordinates are already in absolute pixels, just convert floats to integers
    x1 = int(round(bbox[0]))
    y1 = int(round(bbox[1]))
    x2 = int(round(bbox[2]))
    y2 = int(round(bbox[3]))

    # Standardize: ensure x1 < x2 and y1 < y2
    x1_std = min(x1, x2)
    y1_std = min(y1, y2)
    x2_std = max(x1, x2)
    y2_std = max(y1, y2)

    return [x1_std, y1_std, x2_std, y2_std]


def _quad_to_bbox(quad_box: list, width: int, height: int) -> list:
    # Convert quad box [x1, y1, x2, y2, x3, y3, x4, y4] to standard bbox [x1, y1, x2, y2].
    # Returns the bounding box that encompasses all 4 corners.

    # Coordinates are already in absolute pixels, just convert to integers
    points = [(int(round(quad_box[i])), int(round(quad_box[i+1]))) for i in range(0, 8, 2)]

    # Find min/max coordinates
    x_coords = [p[0] for p in points]
    y_coords = [p[1] for p in points]

    x1 = min(x_coords)
    y1 = min(y_coords)
    x2 = max(x_coords)
    y2 = max(y_coords)

    return [x1, y1, x2, y2]


def _polygon_to_bbox(polygon: list, width: int, height: int) -> list:
    # Convert polygon [[x1, y1], [x2, y2], ...] to standard bbox [x1, y1, x2, y2].
    # Returns the bounding box that encompasses all polygon points.

    # Coordinates are already in absolute pixels, just convert to integers
    points = [(int(round(point[0])), int(round(point[1]))) for point in polygon]

    # Find min/max coordinates
    x_coords = [p[0] for p in points]
    y_coords = [p[1] for p in points]

    x1 = min(x_coords)
    y1 = min(y_coords)
    x2 = max(x_coords)
    y2 = max(y_coords)

    return [x1, y1, x2, y2]


def _detect_from_image(image: torch.Tensor, detect_color: str, threshold: int, min_area: int) -> tuple:
    # Use CV2 to detect contours/rectangles from image and extract bboxes.
    # Similar to Object Detector Mask from LayerStyle.
    #
    # Args:
    #     image: Image tensor (B, H, W, C)
    #     detect_color: Which channel to use for detection (brightness/red/green/blue)
    #     threshold: Threshold value for binary conversion (0-255)
    #     min_area: Minimum contour area in pixels to filter small detections
    #
    # Returns:
    #     Tuple of (bboxes, quad_boxes, polygons) - only bboxes will be populated

    bboxes = []

    # Process first image in batch
    if image.dim() == 4:
        img = image[0]
    else:
        img = image

    # Convert tensor to numpy (H, W, C) with values 0-255
    img_np = (img.cpu().numpy() * 255).astype(np.uint8)

    # Select channel based on detect_color
    if detect_color == "brightness":
        # Convert to grayscale
        if img_np.shape[2] == 3:
            channel = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
        else:
            channel = img_np[:, :, 0]
    elif detect_color == "red":
        # Use red channel
        channel = img_np[:, :, 0] if img_np.shape[2] >= 3 else img_np[:, :, 0]
    elif detect_color == "green":
        # Use green channel
        channel = img_np[:, :, 1] if img_np.shape[2] >= 3 else img_np[:, :, 0]
    elif detect_color == "blue":
        # Use blue channel
        channel = img_np[:, :, 2] if img_np.shape[2] >= 3 else img_np[:, :, 0]
    else:
        # Default to grayscale
        channel = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

    # Threshold to binary
    _, binary = cv2.threshold(channel, threshold, 255, cv2.THRESH_BINARY)

    # Find contours
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Extract bounding boxes from contours, filtering by minimum area
    for contour in contours:
        # Calculate contour area
        area = cv2.contourArea(contour)

        # Skip if area is too small
        if area < min_area:
            continue

        x, y, w, h = cv2.boundingRect(contour)
        bboxes.append([float(x), float(y), float(x + w), float(y + h)])

    # Return as tuple (bboxes, quad_boxes, polygons)
    return bboxes, [], []


class RvConversion_DetectionToBboxes(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Detection to Bboxes [Eclipse]",
            display_name="Detection to Bboxes",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            inputs=[
                io.Image.Input("image", tooltip="Reference image to get dimensions for mask or source for CV2 detection"),
                io.Boolean.Input("get_mask_from_image", default=False, tooltip="Use CV2 to detect rectangles directly from image instead of JSON data"),
                io.Combo.Input("detect_color", options=["brightness", "red", "green", "blue"], default="red", tooltip="What to detect: brightness (grayscale) or specific color channel"),
                io.Int.Input("threshold", default=250, min=0, max=255, step=1, tooltip="Threshold for CV2 detection (higher = less sensitive, only bright regions)"),
                io.Int.Input("min_area", default=500, min=0, max=50000, step=50, tooltip="Minimum contour area in pixels (filters out small detections)"),
                io.Boolean.Input("invert", default=False, tooltip="Invert mask (swap black/white)"),
                io.Int.Input("grow", default=0, min=-512, max=512, step=1, tooltip="Grow (positive) or shrink (negative) mask by N pixels"),
                io.Float.Input("blur", default=0.0, min=0.0, max=64.0, step=0.1, tooltip="Gaussian blur radius to soften mask edges"),
                io.Boolean.Input("combine_masks", default=True, tooltip="Combine all detections into single mask (True) or return separate masks (False)"),
                io.String.Input("indices", default="0,", tooltip="When combine_masks=False, specify which detections to return (e.g., '0,1,4'). Leave empty for all."),
                io.Custom("JSON").Input("data_opt", optional=True, tooltip="Detection data from Smart Language Model Loader (bboxes/quad_boxes/polygons)"),
            ],
            outputs=[
                io.Mask.Output("mask"),
                io.Custom("BBOXES").Output("bboxes"),
            ],
        )

    @classmethod
    def execute(cls, image: torch.Tensor, get_mask_from_image: bool, detect_color: str, threshold: int, min_area: int, invert: bool, grow: int, blur: float, combine_masks: bool, indices: str = "", data_opt: Optional[dict] = None):
        # Get image dimensions
        if image.dim() == 4:
            batch_size, height, width, _ = image.shape
        else:
            batch_size = 1
            height, width, _ = image.shape

        # Determine detection source
        # Prepare output bboxes in SAM2 Ultra format
        output_bboxes: list[list] = []

        if get_mask_from_image:
            # Use CV2 to detect rectangles from image
            bboxes, quad_boxes, polygons = _detect_from_image(image, detect_color, threshold, min_area)
        else:
            # Extract detection data from JSON
            if data_opt is None:
                # No data provided and not using image detection
                empty_mask = torch.zeros((1, height, width), dtype=torch.float32)
                if invert:
                    empty_mask = 1.0 - empty_mask
                output_bboxes = [[] for _ in range(batch_size)]
                return io.NodeOutput(empty_mask, output_bboxes)

            bboxes = data_opt.get("bboxes", [])
            quad_boxes = data_opt.get("quad_boxes", [])
            polygons = data_opt.get("polygons", [])

            # Denormalize if coords are in [0, coord_range) normalized range
            coord_range = data_opt.get("coord_range", 0)
            if coord_range > 0:
                sx = width / coord_range
                sy = height / coord_range
                bboxes = [[b[0]*sx, b[1]*sy, b[2]*sx, b[3]*sy] for b in bboxes if len(b) >= 4]

        # Count total detections
        total_detections = len(bboxes) + len(quad_boxes) + len(polygons)

        if total_detections == 0:
            # No detections - return empty mask and empty bboxes
            empty_mask = torch.zeros((1, height, width), dtype=torch.float32)
            if invert:
                empty_mask = 1.0 - empty_mask
            # Return empty bboxes list for each image in batch
            for _ in range(batch_size):
                output_bboxes.append([])
            return io.NodeOutput(empty_mask, output_bboxes)

        # Create individual masks for each detection and collect standardized bboxes
        masks = []
        standardized_bboxes = []

        # Process bounding boxes
        for bbox in bboxes:
            mask = _create_bbox_mask(bbox, width, height)
            masks.append(mask)
            # Add standardized bbox coordinates
            std_bbox = _standardize_bbox(bbox, width, height)
            standardized_bboxes.append(std_bbox)

        # Process quad boxes (OCR rectangles) - convert to bboxes
        for quad_box in quad_boxes:
            mask = _create_quad_mask(quad_box, width, height)
            masks.append(mask)
            # Convert quad box to standard bbox
            std_bbox = _quad_to_bbox(quad_box, width, height)
            standardized_bboxes.append(std_bbox)

        # Process polygons - convert to bounding boxes
        for polygon in polygons:
            mask = _create_polygon_mask(polygon, width, height)
            masks.append(mask)
            # Convert polygon to standard bbox
            std_bbox = _polygon_to_bbox(polygon, width, height)
            standardized_bboxes.append(std_bbox)

        # Parse indices if provided and not combining
        selected_indices = None
        include_following = False  # Flag to include all indices after the last specified one
        if not combine_masks and indices.strip():
            try:
                # Check if ends with comma (means "and all following")
                indices_clean = indices.strip()
                if indices_clean.endswith(','):
                    include_following = True
                    indices_clean = indices_clean.rstrip(',')

                # Parse comma-separated indices
                if indices_clean:
                    selected_indices = [int(idx.strip()) for idx in indices_clean.split(',') if idx.strip()]
                else:
                    # Just a trailing comma with no indices means all
                    selected_indices = list(range(len(masks)))
            except ValueError:
                log.warning(_LOG_PREFIX, f"Invalid indices format '{indices}', using all detections")
                selected_indices = None

        # Filter by indices if specified
        if selected_indices is not None:
            filtered_masks = []
            filtered_bboxes = []

            # If include_following is True, add all indices from the specified ones onwards
            if include_following and selected_indices:
                # Get all specified indices
                specified = set(selected_indices)
                # Add all indices starting from each specified index to end
                all_indices = set()
                for idx in selected_indices:
                    for i in range(idx, len(masks)):
                        all_indices.add(i)
                selected_indices = sorted(list(all_indices))

            for idx in selected_indices:
                if 0 <= idx < len(masks):
                    filtered_masks.append(masks[idx])
                    filtered_bboxes.append(standardized_bboxes[idx])
                else:
                    log.warning(_LOG_PREFIX, f"Index {idx} out of range (0-{len(masks)-1}), skipping")

            if filtered_masks:
                masks = filtered_masks
                standardized_bboxes = filtered_bboxes
            else:
                # No valid indices, return empty
                empty_mask = torch.zeros((1, height, width), dtype=torch.float32)
                if invert:
                    empty_mask = 1.0 - empty_mask
                for _ in range(batch_size):
                    output_bboxes.append([])
                return io.NodeOutput(empty_mask, output_bboxes)

        # Combine masks if requested
        if combine_masks:
            # Merge all masks into one (logical OR)
            combined = Image.new('L', (width, height), 0)
            for mask in masks:
                combined = Image.fromarray(np.maximum(np.array(combined), np.array(mask)))
            masks = [combined]

        # Apply post-processing operations
        processed_masks = []
        for mask in masks:
            # Apply grow/shrink
            if grow != 0:
                mask = _grow_shrink_mask(mask, grow)

            # Apply blur
            if blur > 0:
                mask = mask.filter(ImageFilter.GaussianBlur(radius=blur))

            # Apply invert
            if invert:
                mask = Image.fromarray(255 - np.array(mask))

            processed_masks.append(mask)

        # Convert to tensor format (B, H, W) with values in [0, 1]
        mask_tensors = []
        for mask in processed_masks:
            mask_np = np.array(mask).astype(np.float32) / 255.0
            mask_tensor = torch.from_numpy(mask_np)
            mask_tensors.append(mask_tensor)

        # Stack into batch
        final_mask = torch.stack(mask_tensors, dim=0)

        # Format bboxes for SAM2 Ultra: List[List[List[int]]]
        # For each image in batch, wrap the standardized_bboxes list
        for _ in range(batch_size):
            output_bboxes.append(standardized_bboxes)

        return io.NodeOutput(final_mask, output_bboxes)
