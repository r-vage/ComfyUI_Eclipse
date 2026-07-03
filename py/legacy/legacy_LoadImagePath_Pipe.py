import os
import torch #type: ignore
import numpy as np
import hashlib
import requests  # type: ignore[import-untyped]
import re

from io import BytesIO
from PIL import Image, ImageOps #type: ignore

from comfy_api.latest import io #type: ignore

from ...core import CATEGORY
from ...core.common import is_safe_url
from ...core.image_metadata import extract_image_metadata

#credits to https://github.com/Jordach/comfy-plasma for the initial code, which was modified for this project


class RvImage_LoadImagePath_Pipe(io.ComfyNode):
	@classmethod
	def define_schema(cls):
		return io.Schema(
			node_id="Load Image from Path (Metadata Pipe) [Eclipse]",
			display_name="⚠ Load Image from Path (Metadata Pipe)",
			description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
			category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
			is_deprecated=True,
			inputs=[
				io.String.Input("image", default=""),
			],
			outputs=[
				io.Image.Output("image"),
				io.Mask.Output("mask"),
				io.Custom("PIPE").Output("pipe"),
			],
		)

	@classmethod
	def execute(cls, image):
		# Removes any quotes from Explorer
		image_path = str(image)
		image_path = image_path.replace('"', "")
		if image_path.startswith("http"):
			image_path = re.sub(r'quality=\d+', 'quality=100', image_path)
		i = None
		if image_path.startswith("http"):
			# Security: validate URL to prevent SSRF
			if not is_safe_url(image_path):
				raise ValueError("URL blocked: cannot access private or local network addresses")
			response = requests.get(image_path)
			i = Image.open(BytesIO(response.content)).convert("RGB")
		else:
			i = Image.open(image_path)
		# Extract metadata using shared utility
		pipe = extract_image_metadata(i)
		
		# Removes EXIF rotation and other nonsense
		i = ImageOps.exif_transpose(i)
		image_rgb = i.convert("RGB")
		image_np = np.array(image_rgb).astype(np.float32) / 255.0
		image_tensor = torch.from_numpy(image_np)[None,]
		if 'A' in i.getbands():
			mask_np = np.array(i.getchannel('A')).astype(np.float32) / 255.0
			mask = 1. - torch.from_numpy(mask_np)
		else:
			mask = torch.zeros((64,64), dtype=torch.float32, device="cpu")
		pipe["image"] = image_tensor
		
		return io.NodeOutput(image_tensor, mask, pipe)

	@classmethod
	def fingerprint_inputs(cls, **kwargs):
		image = kwargs.get("image")
		if image is None or image == "":
			return ""
		image_path = str(image)
		image_path = image_path.replace('"', "")
		m = hashlib.sha256()
		if not image_path.startswith("http"):
			try:
				with open(image_path, 'rb') as f:
					m.update(f.read())
				return m.digest().hex()
			except Exception:
				return ""
		else:
			m.update(image.encode("utf-8"))
			return m.digest().hex()

	@classmethod
	def validate_inputs(cls, **kwargs):
		image = kwargs.get("image")
		if image is None or image == "":
			return True
		image_path = str(image)
		image_path = image_path.replace('"', "")
		if image_path.startswith("http"):
			return True
		if not os.path.isfile(image_path):
			return "No file found: {}".format(image_path)
		return True
