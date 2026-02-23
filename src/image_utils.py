import base64
from typing import Optional

import cv2
import numpy as np
from PIL import Image


def pixels_to_sh1106_base64(pixels: np.ndarray, width: int, height: int) -> str:
    """Convert a 2D pixel array to SH1106 page-column buffer format, then base64 encode."""
    binary = pixels.astype(bool).reshape(height // 8, 8, width)
    weights = (1 << np.arange(8, dtype=np.uint8)).reshape(1, 8, 1)
    buf = (binary * weights).sum(axis=1).astype(np.uint8)
    return base64.b64encode(buf.tobytes()).decode('ascii')


def enhance_for_binary(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrast = clahe.apply(gray)
    blur1 = cv2.GaussianBlur(contrast, (0, 0), 1.0)
    blur2 = cv2.GaussianBlur(contrast, (0, 0), 2.0)
    dog = cv2.subtract(blur1, blur2)
    return cv2.addWeighted(contrast, 1.0, dog, 1.5, 0)


def _dither_to_binary(enhanced: np.ndarray) -> np.ndarray:
    """Floyd-Steinberg dithering via PIL's C implementation."""
    arr = np.array(Image.fromarray(enhanced, mode='L').convert('1'), dtype=np.uint8)
    arr *= 255
    return arr


def process_frame(frame: np.ndarray, width: int, height: int) -> str:
    """Process a single BGR video frame to SH1106 base64."""
    frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    enhanced = enhance_for_binary(gray)
    binary = _dither_to_binary(enhanced)
    binary[0, :] = 0
    binary[-1, :] = 0
    binary[:, 0] = 0
    binary[:, -1] = 0
    return pixels_to_sh1106_base64(binary, width, height)


def process_image_to_mono(image_path: str, target_w: int, target_h: int) -> Optional[Image.Image]:
    """Load an image file, process with dithering, return monochrome PIL Image."""
    frame = cv2.imread(image_path)
    if frame is None:
        return None
    frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    enhanced = enhance_for_binary(gray)
    return Image.fromarray(enhanced, mode='L').convert('1')
