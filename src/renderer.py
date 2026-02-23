import os
from typing import Optional, List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .enums import VideoStatus
from .image_utils import pixels_to_sh1106_base64


_FONT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Roboto-Regular.ttf')

ICONS_8X8 = {
    'check': [
        0b00000000,
        0b00000010,
        0b00000100,
        0b00001000,
        0b10010000,
        0b01100000,
        0b00100000,
        0b00000000,
    ],
    'error': [
        0b00111100,
        0b01000010,
        0b10000101,
        0b10001001,
        0b10010001,
        0b10100001,
        0b01000010,
        0b00111100,
    ],
    'pending': [
        0b00111100,
        0b01001010,
        0b10010001,
        0b10000001,
        0b10000001,
        0b10001001,
        0b01010010,
        0b00111100,
    ],
    'folder': [
        0b00000000,
        0b01110000,
        0b01001000,
        0b01111110,
        0b01000010,
        0b01000010,
        0b01111110,
        0b00000000,
    ],
    'video': [
        0b00000000,
        0b01111110,
        0b01100010,
        0b01111010,
        0b01111010,
        0b01100010,
        0b01111110,
        0b00000000,
    ],
}

STATUS_ICON_MAP = {
    VideoStatus.READY: 'check',
    VideoStatus.ERROR: 'error',
    VideoStatus.PENDING: 'pending',
}

HEADER_HEIGHT = 11
SCROLLBAR_WIDTH = 4
ICON_SIZE = 8
ICON_MARGIN = 2


class Renderer:
    def __init__(self, width: int = 128, height: int = 64):
        self.width = width
        self.height = height
        self._font: ImageFont.ImageFont = self._load_font(9)
        self._font_small: ImageFont.ImageFont = self._load_font(8)
        self._line_height = self._calc_line_height(self._font)
        self._line_height_small = self._calc_line_height(self._font_small)
        self._content_y = HEADER_HEIGHT + 1
        self._content_height = self.height - self._content_y
        self._items_per_page = max(1, self._content_height // self._line_height_small)
        self._item_text_width = self.width - SCROLLBAR_WIDTH - ICON_SIZE - ICON_MARGIN - 4

    @staticmethod
    def _load_font(size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype(_FONT_PATH, size)
        except Exception:
            return ImageFont.load_default()

    @staticmethod
    def _calc_line_height(font: ImageFont.ImageFont) -> int:
        try:
            bbox = font.getbbox('Ayg')
            return bbox[3] - bbox[1] + 2
        except Exception:
            return 10

    def create_canvas(self) -> Image.Image:
        return Image.new('1', (self.width, self.height), 0)

    def canvas_to_base64(self, img: Image.Image) -> str:
        arr = np.array(img.convert('L'), dtype=np.uint8)
        return pixels_to_sh1106_base64(arr, self.width, self.height)

    def draw_text(
        self,
        img: Image.Image,
        x: int,
        y: int,
        text: str,
        font: Optional[ImageFont.ImageFont] = None,
        color: int = 1,
    ) -> None:
        draw = ImageDraw.Draw(img)
        draw.text((x, y), text, fill=color, font=font or self._font_small)

    def draw_text_wrapped(
        self,
        img: Image.Image,
        x: int,
        y: int,
        text: str,
        max_width: int,
        font: Optional[ImageFont.ImageFont] = None,
        color: int = 1,
    ) -> int:
        """Draw word-wrapped text. Returns the y position after the last line."""
        f = font or self._font_small
        lh = self._calc_line_height(f)
        draw = ImageDraw.Draw(img)
        current_y = y

        for paragraph in text.split('\n'):
            if not paragraph:
                current_y += lh
                continue
            words = paragraph.split()
            line = ''
            for word in words:
                test = f'{line} {word}'.strip()
                tw = self._text_width(test, f)
                if tw <= max_width:
                    line = test
                else:
                    if line:
                        draw.text((x, current_y), line, fill=color, font=f)
                        current_y += lh
                    if self._text_width(word, f) > max_width:
                        for ch in word:
                            test = line + ch
                            if self._text_width(test, f) > max_width and line:
                                draw.text((x, current_y), line, fill=color, font=f)
                                current_y += lh
                                line = ch
                            else:
                                line = test
                    else:
                        line = word
            if line:
                draw.text((x, current_y), line, fill=color, font=f)
                current_y += lh
        return current_y

    def draw_header(self, img: Image.Image, title: str) -> None:
        draw = ImageDraw.Draw(img)
        tw = self._text_width(title, self._font)
        tx = max(0, (self.width - tw) // 2)
        draw.text((tx, 0), title, fill=1, font=self._font)
        draw.line([(0, HEADER_HEIGHT - 1), (self.width - 1, HEADER_HEIGHT - 1)], fill=1)

    def draw_menu(
        self,
        img: Image.Image,
        items: List[Tuple[str, Optional[str], Optional[VideoStatus], int]],
        selected: int,
        scroll_offset: int,
        title: Optional[str] = None,
    ) -> None:
        """Draw a scrollable menu.

        Each item is (name, icon_key_or_none, video_status_or_none, progress).
        """
        if title:
            self.draw_header(img, title)

        draw = ImageDraw.Draw(img)
        visible = items[scroll_offset: scroll_offset + self._items_per_page]
        y = self._content_y

        for i, (name, icon_key, status, progress) in enumerate(visible):
            actual_idx = scroll_offset + i
            is_selected = actual_idx == selected

            if is_selected:
                draw.rectangle(
                    [0, y, self.width - SCROLLBAR_WIDTH - 1, y + self._line_height_small - 1],
                    fill=1,
                )

            icon_x = 1
            if icon_key:
                self._draw_icon_8x8(img, icon_x, y, icon_key, invert=is_selected)
            elif status is not None:
                if status == VideoStatus.PROCESSING:
                    self._draw_progress_icon(img, icon_x, y, progress, invert=is_selected)
                else:
                    icon_name = STATUS_ICON_MAP.get(status)
                    if icon_name:
                        self._draw_icon_8x8(img, icon_x, y, icon_name, invert=is_selected)

            text_x = ICON_SIZE + ICON_MARGIN + 1
            truncated = self._truncate_text(name, self._item_text_width, self._font_small)
            draw.text(
                (text_x, y),
                truncated,
                fill=0 if is_selected else 1,
                font=self._font_small,
            )
            y += self._line_height_small

        if len(items) > self._items_per_page:
            self._draw_scrollbar(img, len(items), self._items_per_page, scroll_offset)

    def _draw_scrollbar(
        self,
        img: Image.Image,
        total: int,
        visible: int,
        offset: int,
    ) -> None:
        draw = ImageDraw.Draw(img)
        x = self.width - SCROLLBAR_WIDTH
        y_start = self._content_y
        track_h = self._content_height

        draw.line([(x + 1, y_start), (x + 1, y_start + track_h - 1)], fill=1)

        if total <= 0:
            return
        thumb_h = max(3, int(track_h * visible / total))
        thumb_y = y_start + int((track_h - thumb_h) * offset / max(1, total - visible))
        draw.rectangle([x, thumb_y, x + SCROLLBAR_WIDTH - 1, thumb_y + thumb_h - 1], fill=1)

    def _draw_icon_8x8(
        self,
        img: Image.Image,
        x: int,
        y: int,
        icon_key: str,
        invert: bool = False,
    ) -> None:
        icon_data = ICONS_8X8.get(icon_key)
        if not icon_data:
            return
        color = 0 if invert else 1
        for row_idx, row_byte in enumerate(icon_data):
            for col_idx in range(8):
                if row_byte & (0x80 >> col_idx):
                    img.putpixel((x + col_idx, y + row_idx), color)

    def _draw_progress_icon(
        self,
        img: Image.Image,
        x: int,
        y: int,
        progress: int,
        invert: bool = False,
    ) -> None:
        draw = ImageDraw.Draw(img)
        outline = 0 if invert else 1
        fill_color = 0 if invert else 1
        bg = 1 if invert else 0

        draw.rectangle([x, y, x + 7, y + 7], outline=outline, fill=bg)
        fill_h = max(0, min(6, int(6 * progress / 100)))
        if fill_h > 0:
            draw.rectangle([x + 1, y + 7 - fill_h, x + 6, y + 6], fill=fill_color)

    def paste_image(self, canvas: Image.Image, img: Image.Image, x: int, y: int) -> None:
        canvas.paste(img.convert('1'), (x, y))

    def draw_centered_message(self, img: Image.Image, text: str) -> None:
        draw = ImageDraw.Draw(img)
        lines = text.split('\n')
        total_h = len(lines) * self._line_height
        start_y = max(0, (self.height - total_h) // 2)
        for i, line in enumerate(lines):
            tw = self._text_width(line, self._font)
            tx = max(0, (self.width - tw) // 2)
            draw.text((tx, start_y + i * self._line_height), line, fill=1, font=self._font)

    def draw_rect(
        self,
        img: Image.Image,
        x: int,
        y: int,
        w: int,
        h: int,
        fill: Optional[int] = None,
        outline: Optional[int] = 1,
    ) -> None:
        draw = ImageDraw.Draw(img)
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=fill, outline=outline)

    def draw_line(self, img: Image.Image, x1: int, y1: int, x2: int, y2: int) -> None:
        draw = ImageDraw.Draw(img)
        draw.line([(x1, y1), (x2, y2)], fill=1)

    def _text_width(self, text: str, font: ImageFont.ImageFont) -> int:
        try:
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0]
        except Exception:
            return len(text) * 6

    def _truncate_text(self, text: str, max_width: int, font: ImageFont.ImageFont) -> str:
        if self._text_width(text, font) <= max_width:
            return text
        for i in range(len(text), 0, -1):
            t = text[:i] + '..'
            if self._text_width(t, font) <= max_width:
                return t
        return '..'

    @property
    def items_per_page(self) -> int:
        return self._items_per_page
