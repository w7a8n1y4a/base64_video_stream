import time
import threading
from typing import Optional, List

from .enums import EncoderAction
from .playback import Playback
from .renderer import Renderer
from .screens import Screen, SplashScreen, LibraryScreen
from .video_processor import VideoProcessor


class Navigator:
    def __init__(
        self,
        video_processor: VideoProcessor,
        renderer: Renderer,
        version: str,
        seek_seconds: float = 5.0,
    ):
        self.video_processor = video_processor
        self.renderer = renderer
        self.version = version
        self._seek_seconds = seek_seconds
        self._seek_frames = max(1, int(seek_seconds * video_processor.target_fps))

        self._lock = threading.Lock()
        self._screen: Screen = SplashScreen(self)
        self._playback: Optional[Playback] = None
        self._return_screen: Optional[Screen] = None
        self._temp_message: Optional[str] = None
        self._temp_until: float = 0
        self._seek_overlay: Optional[str] = None
        self._seek_overlay_until: float = 0
        self._paused: bool = False

    def handle_action(self, action: EncoderAction) -> None:
        with self._lock:
            if self._temp_message and time.time() < self._temp_until:
                return

            self._temp_message = None

            if self._playback:
                if action == EncoderAction.LONG:
                    self._playback = None
                    self._paused = False
                    if self._return_screen:
                        self._screen = self._return_screen
                        if isinstance(self._screen, LibraryScreen):
                            self._screen.refresh()
                        self._return_screen = None
                elif action == EncoderAction.ONE:
                    self._paused = not self._paused
                elif action in (EncoderAction.LEFT, EncoderAction.RIGHT):
                    forward = action == EncoderAction.RIGHT
                    delta = self._seek_frames if forward else -self._seek_frames
                    if self._playback.seek(delta):
                        sec = int(self._seek_seconds)
                        self._seek_overlay = f'+{sec}' if forward else f'-{sec}'
                        self._seek_overlay_until = time.time() + 0.5
                    else:
                        self.show_message('Нет данных')
                return

            self._screen.on_action(action)

    def get_next_frame(self) -> Optional[str]:
        with self._lock:
            if self._temp_message:
                if time.time() > self._temp_until:
                    self._temp_message = None
                else:
                    return self._render_message(self._temp_message)

            if self._playback:
                if self._paused:
                    frame = self._playback.get_current_frame()
                else:
                    frame = self._playback.get_next_frame()
                if frame:
                    if self._seek_overlay and time.time() < self._seek_overlay_until:
                        return self.renderer.overlay_on_frame(frame, self._seek_overlay)
                    self._seek_overlay = None
                    if self._paused:
                        return self.renderer.overlay_on_frame(frame, '||')
                    return frame
                self._playback = None
                self._paused = False
                if self._return_screen:
                    self._screen = self._return_screen
                    self._return_screen = None

            return self._render_screen()

    def switch_screen(self, screen: Screen) -> None:
        self._screen = screen

    def start_playback(self, video_paths: List[str], shuffle: bool = False) -> None:
        self._return_screen = self._screen
        self._playback = Playback(self.video_processor, video_paths, shuffle=shuffle)

    def show_message(self, text: str, duration: float = 1.0) -> None:
        self._temp_message = text
        self._temp_until = time.time() + duration

    def _render_screen(self) -> str:
        canvas = self._screen.render()
        return self.renderer.canvas_to_base64(canvas)

    def _render_message(self, text: str) -> str:
        canvas = self.renderer.create_canvas()
        self.renderer.draw_centered_message(canvas, text)
        return self.renderer.canvas_to_base64(canvas)
