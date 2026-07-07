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
from PIL import Image, ImageDraw, ImageFilter  # type: ignore
from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import unwrap_value, flatten_images
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "Convert"


def _create_bbox_mask(bbox: list, width: int, height: int) -> Image.Image:
    # Create mask from bounding box [x1, y1, x2, y2].
    # Create black background
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)

    # bbox format: [x1, y1, x2, y2] - already in absolute pixel coordinates
    # Convert floats to integers and ensure x1 <= x2, y1 <= y2
    x1, y1, x2, y2 = (
        int(round(bbox[0])),
        int(round(bbox[1])),
        int(round(bbox[2])),
        int(round(bbox[3])),
    )
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)

    # Draw filled white rectangle
    draw.rectangle([x1, y1, x2, y2], fill=255)

    return mask


def _create_quad_mask(quad_box: list, width: int, height: int) -> Image.Image:
    # Create mask from quad box [x1, y1, x2, y2, x3, y3, x4, y4].
    # Create black background
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)

    # quad_box format: 8 coordinates for 4 corners - already in absolute pixels
    # Just convert floats to integers
    points = [
        (int(round(quad_box[i])), int(round(quad_box[i + 1]))) for i in range(0, 8, 2)
    ]

    # Draw filled white polygon
    draw.polygon(points, fill=255)

    return mask


def _create_polygon_mask(polygon: list, width: int, height: int) -> Image.Image:
    # Create mask from polygon [[x1, y1], [x2, y2], ...].
    # Create black background
    mask = Image.new("L", (width, height), 0)
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
    points = [
        (int(round(quad_box[i])), int(round(quad_box[i + 1]))) for i in range(0, 8, 2)
    ]

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


def _detect_from_image(
    image: torch.Tensor, detect_color: str, threshold: int, min_area: int
) -> tuple:
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
            is_input_list=True,
            inputs=[
                io.Image.Input(
                    "image",
                    tooltip="Reference image to get dimensions for mask or source for CV2 detection",
                ),
                io.Boolean.Input(
                    "get_mask_from_image",
                    default=False,
                    tooltip="Use CV2 to detect rectangles directly from image instead of JSON data",
                ),
                io.Combo.Input(
                    "detect_color",
                    options=["brightness", "red", "green", "blue"],
                    default="red",
                    tooltip="What to detect: brightness (grayscale) or specific color channel",
                ),
                io.Int.Input(
                    "threshold",
                    default=250,
                    min=0,
                    max=255,
                    step=1,
                    tooltip="Threshold for CV2 detection (higher = less sensitive, only bright regions)",
                ),
                io.Int.Input(
                    "min_area",
                    default=500,
                    min=0,
                    max=50000,
                    step=50,
                    tooltip="Minimum contour area in pixels (filters out small detections)",
                ),
                io.Boolean.Input(
                    "invert", default=False, tooltip="Invert mask (swap black/white)"
                ),
                io.Int.Input(
                    "grow",
                    default=0,
                    min=-512,
                    max=512,
                    step=1,
                    tooltip="Grow (positive) or shrink (negative) mask by N pixels",
                ),
                io.Float.Input(
                    "blur",
                    default=0.0,
                    min=0.0,
                    max=64.0,
                    step=0.1,
                    tooltip="Gaussian blur radius to soften mask edges",
                ),
                io.Boolean.Input(
                    "combine_masks",
                    default=True,
                    tooltip="Combine all detections into single mask (True) or return separate masks (False)",
                ),
                io.String.Input(
                    "indices",
                    default="0,",
                    tooltip="When combine_masks=False, specify which detections to return (e.g., '0,1,4'). Leave empty for all.",
                ),
                io.Custom("JSON").Input(
                    "data_opt",
                    optional=True,
                    tooltip="Detection data from Smart Language Model Loader (bboxes/quad_boxes/polygons)",
                ),
            ],
            outputs=[
                io.Mask.Output("mask", is_output_list=True),
                io.Custom("BBOXES").Output("bboxes", is_output_list=True),
            ],
        )

    @classmethod
    def execute(
        cls,
        image,
        get_mask_from_image,
        detect_color,
        threshold,
        min_area,
        invert,
        grow,
        blur,
        combine_masks,
        indices="",
        data_opt=None,
    ):
        get_mask_from_image = unwrap_value(get_mask_from_image, False)
        detect_color = unwrap_value(detect_color, "red")
        threshold = unwrap_value(threshold, 250)
        min_area = unwrap_value(min_area, 500)
        invert = unwrap_value(invert, False)
        grow = unwrap_value(grow, 0)
        blur = unwrap_value(blur, 0.0)
        combine_masks = unwrap_value(combine_masks, True)
        indices = unwrap_value(indices, "")

        # Flatten/normalize input images to list of tensors
        flat_images = flatten_images(image)

        # Flatten/normalize input data_opt to list of dicts
        flat_datas = []

        def _process_data(item):
            if isinstance(item, (list, tuple)):
                for sub in item:
                    _process_data(sub)
            elif isinstance(item, dict):
                flat_datas.append(item)
            elif item is not None:
                flat_datas.append(item)

        _process_data(data_opt)

        num_runs = (
            len(flat_images)
            if get_mask_from_image
            else max(len(flat_images), len(flat_datas))
        )
        if num_runs == 0:
            empty_mask = torch.zeros((1, 64, 64), dtype=torch.float32)
            if invert:
                empty_mask = 1.0 - empty_mask
            return io.NodeOutput([empty_mask], [[]])

        images_aligned = (
            [flat_images[i if i < len(flat_images) else -1] for i in range(num_runs)]
            if flat_images
            else [None] * num_runs
        )

        datas_aligned = (
            [flat_datas[i if i < len(flat_datas) else -1] for i in range(num_runs)]
            if flat_datas
            else [None] * num_runs
        )

        out_masks = []
        out_bboxes = []

        for run_idx in range(num_runs):
            img_tensor = images_aligned[run_idx]
            if img_tensor is None:
                continue

            if img_tensor.dim() == 4:
                height, width = img_tensor.shape[1], img_tensor.shape[2]
            else:
                height, width = img_tensor.shape[0], img_tensor.shape[1]

            empty_mask = torch.zeros((1, height, width), dtype=torch.float32)
            if invert:
                empty_mask = 1.0 - empty_mask

            data_dict = datas_aligned[run_idx]

            bboxes = []
            quad_boxes = []
            polygons = []

            if get_mask_from_image:
                bboxes, quad_boxes, polygons = _detect_from_image(
                    img_tensor, detect_color, threshold, min_area
                )
            else:
                if data_dict is None:
                    out_masks.append(empty_mask)
                    out_bboxes.append([])
                    continue

                bboxes = data_dict.get("bboxes", [])
                quad_boxes = data_dict.get("quad_boxes", [])
                polygons = data_dict.get("polygons", [])

                coord_range = data_dict.get("coord_range", 0)
                if coord_range > 0:
                    sx = width / coord_range
                    sy = height / coord_range
                    bboxes = [
                        [b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy]
                        for b in bboxes
                        if len(b) >= 4
                    ]

            total_detections = len(bboxes) + len(quad_boxes) + len(polygons)
            if total_detections == 0:
                out_masks.append(empty_mask)
                out_bboxes.append([])
                continue

            run_masks = []
            standardized_bboxes = []

            for bbox in bboxes:
                mask = _create_bbox_mask(bbox, width, height)
                run_masks.append(mask)
                std_bbox = _standardize_bbox(bbox, width, height)
                standardized_bboxes.append(std_bbox)

            for quad_box in quad_boxes:
                mask = _create_quad_mask(quad_box, width, height)
                run_masks.append(mask)
                std_bbox = _quad_to_bbox(quad_box, width, height)
                standardized_bboxes.append(std_bbox)

            for polygon in polygons:
                mask = _create_polygon_mask(polygon, width, height)
                run_masks.append(mask)
                std_bbox = _polygon_to_bbox(polygon, width, height)
                standardized_bboxes.append(std_bbox)

            selected_indices = None
            include_following = False
            if not combine_masks and indices.strip():
                try:
                    indices_clean = indices.strip()
                    if indices_clean.endswith(","):
                        include_following = True
                        indices_clean = indices_clean.rstrip(",")

                    if indices_clean:
                        selected_indices = [
                            int(idx.strip())
                            for idx in indices_clean.split(",")
                            if idx.strip()
                        ]
                    else:
                        selected_indices = list(range(len(run_masks)))
                except ValueError:
                    log.warning(
                        _LOG_PREFIX,
                        f"Invalid indices format '{indices}', using all detections",
                    )
                    selected_indices = None

            if selected_indices is not None:
                filtered_masks = []
                filtered_bboxes = []

                if include_following and selected_indices:
                    all_indices = set()
                    for idx in selected_indices:
                        for i in range(idx, len(run_masks)):
                            all_indices.add(i)
                    selected_indices = sorted(list(all_indices))

                for idx in selected_indices:
                    if 0 <= idx < len(run_masks):
                        filtered_masks.append(run_masks[idx])
                        filtered_bboxes.append(standardized_bboxes[idx])
                    else:
                        log.warning(
                            _LOG_PREFIX,
                            f"Index {idx} out of range (0-{len(run_masks)-1}), skipping",
                        )

                if filtered_masks:
                    run_masks = filtered_masks
                    standardized_bboxes = filtered_bboxes
                else:
                    out_masks.append(empty_mask)
                    out_bboxes.append([])
                    continue

            if combine_masks:
                combined = Image.new("L", (width, height), 0)
                for mask in run_masks:
                    combined = Image.fromarray(
                        np.maximum(np.array(combined), np.array(mask))
                    )
                run_masks = [combined]

            processed_masks = []
            for mask in run_masks:
                if grow != 0:
                    mask = _grow_shrink_mask(mask, grow)
                if blur > 0:
                    mask = mask.filter(ImageFilter.GaussianBlur(radius=blur))
                if invert:
                    mask = Image.fromarray(255 - np.array(mask))
                processed_masks.append(mask)

            mask_tensors = []
            for mask in processed_masks:
                mask_np = np.array(mask).astype(np.float32) / 255.0
                mask_tensor = torch.from_numpy(mask_np)
                mask_tensors.append(mask_tensor)

            final_mask = torch.stack(mask_tensors, dim=0)
            out_masks.append(final_mask)
            out_bboxes.append([standardized_bboxes])

        if not out_masks:
            # If no masks were generated, return empty tensor
            empty_mask = torch.zeros((1, height, width), dtype=torch.float32)
            if invert:
                empty_mask = 1.0 - empty_mask
            return io.NodeOutput([empty_mask], [[]])

        log.msg(
            _LOG_PREFIX,
            f"Returning out_masks list of len {len(out_masks)}, out_bboxes list of len {len(out_bboxes)}",
        )
        return io.NodeOutput(out_masks, out_bboxes)
