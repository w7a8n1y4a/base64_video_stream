from typing import Optional

from PIL import Image

from .renderer import Renderer


class Streamer:
    def __init__(self, client, topic: str = 'full_frame_stream/pepeunit'):
        self.client = client
        self.topic = topic
        self._renderer: Optional[Renderer] = None

    def set_renderer(self, renderer: Renderer) -> None:
        self._renderer = renderer

    def send_frame(self, frame_base64: str) -> None:
        self.client.publish_to_topics(self.topic, frame_base64)

    def send_canvas(self, canvas: Image.Image) -> None:
        if self._renderer is None:
            raise RuntimeError('Renderer not set on Streamer')
        self.send_frame(self._renderer.canvas_to_base64(canvas))
