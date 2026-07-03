import os
import numpy as np #type: ignore
import torch #type: ignore
import sys
from PIL import Image, ImageOps #type: ignore

my_dir = os.path.dirname(os.path.abspath(__file__))
custom_nodes_dir = os.path.abspath(os.path.join(my_dir, '.'))
comfy_dir = os.path.abspath(os.path.join(my_dir, '..'))
sys.path.append(comfy_dir)

from ..core import CATEGORY, SLIDER_DISPLAY
from comfy_api.latest import io #type: ignore

MAX_RESOLUTION = 32768

def tensor2pil(image: torch.Tensor) -> Image.Image:
    return Image.fromarray(np.clip(255. * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8))


def pil2tensor(image: Image.Image) -> torch.Tensor:
    return torch.from_numpy(np.array(image).astype(np.float32) / 255.0).unsqueeze(0)

def common_upscale(samples, Scale_width, Scale_height, upscale_method, crop):
    if crop == "center":
        old_Scale_width = samples.shape[3]
        old_Scale_height = samples.shape[2]
        old_aspect = old_Scale_width / old_Scale_height
        new_aspect = Scale_width / Scale_height
        x = 0
        y = 0
        if old_aspect > new_aspect:
            x = round((old_Scale_width - old_Scale_width * (new_aspect / old_aspect)) / 2)
        elif old_aspect < new_aspect:
            y = round((old_Scale_height - old_Scale_height * (old_aspect / new_aspect)) / 2)
        s = samples[:,:,y:old_Scale_height-y,x:old_Scale_width-x]
    else:
        s = samples

    return torch.nn.functional.interpolate(s, size=(Scale_height, Scale_width), mode=upscale_method)

_ZOOM_MODES = ["None", "Fit", "zoom", "Scale_according_to_input_width_and_height"]
_SCALING_METHODS = ["nearest-exact", "bilinear", "area"]
_POSITIONS = ["Centered", "Top", "Bottom", "Left", "Right", "Top Left", "Top Right", "Bottom Left", "Bottom Right"]


class RvImage_AddWatermarkImage(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Add Watermark Image [Eclipse]",
            display_name="Add Watermark Image",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            inputs=[
                io.Image.Input("original_image"),
                io.Image.Input("Watermark_image"),
                io.Combo.Input("Zoom_mode", options=_ZOOM_MODES),
                io.Combo.Input("Scaling_method", options=_SCALING_METHODS),
                io.Float.Input("Scaling_factor", default=1, min=0.01, max=16.0, step=0.1),
                io.Int.Input("Scale_width", default=512, min=0, max=MAX_RESOLUTION, step=64),
                io.Int.Input("Scale_height", default=512, min=0, max=MAX_RESOLUTION, step=64),
                io.Combo.Input("initial_position", options=_POSITIONS),
                io.Int.Input("X_direction", default=0, min=-48000, max=48000, step=10),
                io.Int.Input("Y_direction", default=0, min=-48000, max=48000, step=10),
                io.Int.Input("rotate", default=0, min=-180, max=180, step=5),
                io.Float.Input("transparency", default=0, min=0, max=100, step=5, display_mode=SLIDER_DISPLAY),
                io.Mask.Input("mask", optional=True),
            ],
            outputs=[
                io.Image.Output("image"),
            ],
        )

    @classmethod
    def execute(cls, original_image, Watermark_image, Zoom_mode, Scaling_method, Scaling_factor,
                Scale_width, Scale_height, X_direction, Y_direction, rotate, transparency, initial_position, mask=None) -> io.NodeOutput:

        size = Scale_width, Scale_height
        location = X_direction, Y_direction
        mask = mask

        if Zoom_mode != "None":
            Watermark_image_size = Watermark_image.size()
            Watermark_image_size = (Watermark_image_size[2], Watermark_image_size[1])
            if Zoom_mode == "Fit":
                h_ratio = original_image.size()[1] / Watermark_image_size[1]
                w_ratio = original_image.size()[2] / Watermark_image_size[0]
                ratio = min(h_ratio, w_ratio)
                Watermark_image_size = tuple(round(dimension * ratio) for dimension in Watermark_image_size)
            elif Zoom_mode == "zoom":
                Watermark_image_size = tuple(int(dimension * Scaling_factor) for dimension in Watermark_image_size)
            elif Zoom_mode == "Scale_according_to_input_width_and_height":
                Watermark_image_size = (size[0], size[1])

            samples = Watermark_image.movedim(-1, 1)
            Watermark_image = common_upscale(samples, Watermark_image_size[0], Watermark_image_size[1], Scaling_method, False)
            Watermark_image = Watermark_image.movedim(1, -1)
            
        Watermark_image = tensor2pil(Watermark_image)

        Watermark_image = Watermark_image.convert('RGBA')
        Watermark_image.putalpha(Image.new("L", Watermark_image.size, 255))

        if mask is not None:
            mask = tensor2pil(mask)
            mask = mask.resize(Watermark_image.size)
            Watermark_image.putalpha(ImageOps.invert(mask))

        Watermark_image = Watermark_image.rotate(rotate, expand=True)

        r, g, b, a = Watermark_image.split()
        a = a.point(lambda x: max(0, int(x * (1 - transparency / 100))))
        Watermark_image.putalpha(a)
        
        # Calculate position based on alignment
        original_image_Scale_width, original_image_Scale_height = original_image.size()[2], original_image.size()[1]
        Watermark_image_Scale_width, Watermark_image_Scale_height = Watermark_image.size
        
        # Initialize position variables
        X_direction_int = None
        Y_direction_int = None
        
        if initial_position == "Centered":
            X_direction_int = int(X_direction + (original_image_Scale_width - Watermark_image_Scale_width) / 2)
            Y_direction_int = int(Y_direction + (original_image_Scale_height - Watermark_image_Scale_height) / 2)
        elif initial_position == "Top":
            X_direction_int = int(X_direction + (original_image_Scale_width - Watermark_image_Scale_width) / 2)
            Y_direction_int = Y_direction  
        elif initial_position == "Bottom":
            X_direction_int = int(X_direction + (original_image_Scale_width - Watermark_image_Scale_width) / 2)
            Y_direction_int = int(Y_direction + original_image_Scale_height - Watermark_image_Scale_height)
        elif initial_position == "Left":
            Y_direction_int = int(Y_direction + (original_image_Scale_height - Watermark_image_Scale_height) / 2)
            X_direction_int = X_direction  
        elif initial_position == "Right":
            X_direction_int = int(X_direction + original_image_Scale_width - Watermark_image_Scale_width)
            Y_direction_int = int(Y_direction + (original_image_Scale_height - Watermark_image_Scale_height) / 2)
        elif initial_position == "Top Left":
            pass  
        elif initial_position == "Top Right":
            X_direction_int = int(original_image_Scale_width - Watermark_image_Scale_width + X_direction)  
            Y_direction_int = Y_direction  
        elif initial_position == "Bottom Left":
            X_direction_int = X_direction  
            Y_direction_int = int(original_image_Scale_height - Watermark_image_Scale_height + Y_direction) 
        elif initial_position == "Bottom Right":
            X_direction_int = int(X_direction + original_image_Scale_width - Watermark_image_Scale_width)
            Y_direction_int = int(Y_direction + original_image_Scale_height - Watermark_image_Scale_height)
        
        if X_direction_int is not None and Y_direction_int is not None:
            location = X_direction_int, Y_direction_int
        else:
            location = X_direction, Y_direction

        original_image_list = torch.unbind(original_image, dim=0)

        processed_original_image_list = []
        for tensor in original_image_list:
            image = tensor2pil(tensor)

            if mask is None:
                image.paste(Watermark_image, location)
            else:
                image.paste(Watermark_image, location, Watermark_image)

            processed_tensor = pil2tensor(image)
            processed_original_image_list.append(processed_tensor)

        original_image = torch.stack([tensor.squeeze() for tensor in processed_original_image_list])

        return io.NodeOutput(original_image)