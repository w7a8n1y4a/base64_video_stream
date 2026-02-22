import os
import time
import random
import threading
from abc import ABC, abstractmethod
from typing import Optional, List

from PIL import Image

from .enums import EncoderAction, VideoStatus
from .renderer import Renderer
from .streamer import Streamer
from .video_processor import VideoProcessor, process_image_to_mono


class Playback:
    """Manages sequential frame playback from one or more video frame files."""

    def __init__(
        self,
        video_processor: VideoProcessor,
        video_paths: List[str],
        shuffle: bool = False,
    ):
        self._vp = video_processor
        self._playlist = list(video_paths)
        self._shuffle = shuffle
        self._current_idx = 0
        self._frame_idx = 0
        self._frames: List[str] = []

        if self._shuffle:
            random.shuffle(self._playlist)

        self._load_current()

    def get_next_frame(self) -> Optional[str]:
        if not self._playlist:
            return None

        if self._frame_idx >= len(self._frames):
            self._current_idx += 1
            if self._current_idx >= len(self._playlist):
                if self._shuffle:
                    random.shuffle(self._playlist)
                self._current_idx = 0
            self._load_current()
            if not self._frames:
                return None

        frame = self._frames[self._frame_idx]
        self._frame_idx += 1
        return frame

    def _load_current(self) -> None:
        if self._current_idx >= len(self._playlist):
            self._frames = []
            return
        rel = self._playlist[self._current_idx]
        path = self._vp.get_frames_path(rel)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                self._frames = [line.strip() for line in f if line.strip()]
        except OSError:
            self._frames = []
        self._frame_idx = 0


class Screen(ABC):
    def __init__(self, navigator: 'Navigator'):
        self.nav = navigator

    @abstractmethod
    def render(self) -> Image.Image:
        ...

    @abstractmethod
    def on_action(self, action: EncoderAction) -> None:
        ...


class SplashScreen(Screen):
    def __init__(self, navigator: 'Navigator'):
        super().__init__(navigator)
        self._icon: Optional[Image.Image] = None
        self._icon_loaded = False

    def _ensure_icon(self) -> None:
        if self._icon_loaded:
            return
        self._icon_loaded = True
        icon_path = self.nav.icon_path
        if icon_path and os.path.isfile(icon_path):
            self._icon = process_image_to_mono(icon_path, 64, 64)

    def render(self) -> Image.Image:
        self._ensure_icon()
        r = self.nav.renderer
        canvas = r.create_canvas()

        if self._icon:
            r.paste_image(canvas, self._icon, 0, 0)
            r.draw_line(canvas, 64, 0, 64, 63)

        right_x = 68
        r.draw_text(canvas, right_x, 4, 'Video', font=r._font)
        r.draw_text(canvas, right_x, 16, 'Stream', font=r._font)
        r.draw_text(canvas, right_x, 32, f'v{self.nav.version}', font=r._font_small)
        r.draw_text(canvas, right_x, 50, 'AGPLv3', font=r._font_small)

        return canvas

    def on_action(self, action: EncoderAction) -> None:
        self.nav.switch_screen(MainMenuScreen(self.nav))


class MainMenuScreen(Screen):
    ITEMS = ['Библиотека', 'Рандом']

    def __init__(self, navigator: 'Navigator'):
        super().__init__(navigator)
        self.selected = 0

    def render(self) -> Image.Image:
        r = self.nav.renderer
        canvas = r.create_canvas()
        items = [(name, None, None, 0) for name in self.ITEMS]
        r.draw_menu(canvas, items, self.selected, 0, title='Меню')
        return canvas

    def on_action(self, action: EncoderAction) -> None:
        if action == EncoderAction.LEFT:
            self.selected = min(self.selected + 1, len(self.ITEMS) - 1)
        elif action == EncoderAction.RIGHT:
            self.selected = max(self.selected - 1, 0)
        elif action == EncoderAction.ONE:
            if self.selected == 0:
                self.nav.switch_screen(LibraryScreen(self.nav))
            elif self.selected == 1:
                self._start_random()
        elif action == EncoderAction.LONG:
            self.nav.switch_screen(SplashScreen(self.nav))

    def _start_random(self) -> None:
        ready = self.nav.video_processor.get_all_ready_videos()
        if ready:
            self.nav.start_playback(ready, shuffle=True)
        else:
            self.nav.show_message('Нет готовых\nвидео')


class LibraryScreen(Screen):
    def __init__(self, navigator: 'Navigator', rel_path: str = ''):
        super().__init__(navigator)
        self.rel_path = rel_path
        self.selected = 0
        self.scroll_offset = 0
        self.entries = self.nav.video_processor.list_directory(rel_path)

    def refresh(self) -> None:
        self.entries = self.nav.video_processor.list_directory(self.rel_path)
        self.selected = min(self.selected, max(0, len(self.entries) - 1))
        self._adjust_scroll()

    def render(self) -> Image.Image:
        r = self.nav.renderer
        canvas = r.create_canvas()

        title = os.path.basename(self.rel_path) if self.rel_path else 'Библиотека'
        items = []
        for e in self.entries:
            if e.is_dir:
                items.append((e.name, 'folder', None, 0))
            else:
                items.append((e.name, None, e.status, e.progress))

        r.draw_menu(canvas, items, self.selected, self.scroll_offset, title=title)
        return canvas

    def on_action(self, action: EncoderAction) -> None:
        if not self.entries:
            if action == EncoderAction.LONG:
                self._go_back()
            return

        if action == EncoderAction.LEFT:
            if self.selected < len(self.entries) - 1:
                self.selected += 1
                self._adjust_scroll()
        elif action == EncoderAction.RIGHT:
            if self.selected > 0:
                self.selected -= 1
                self._adjust_scroll()
        elif action == EncoderAction.ONE:
            entry = self.entries[self.selected]
            if entry.is_dir:
                self.nav.switch_screen(LibraryScreen(self.nav, entry.rel_path))
            elif entry.status == VideoStatus.READY:
                self.nav.start_playback([entry.rel_path])
            else:
                self.nav.show_message('Воспроизведение\nневозможно')
        elif action == EncoderAction.DOUBLE:
            entry = self.entries[self.selected]
            if entry.is_dir:
                ready = self.nav.video_processor.get_ready_videos_in(entry.rel_path)
                if ready:
                    self.nav.start_playback(ready)
                else:
                    self.nav.show_message('Нет готовых\nвидео')
        elif action == EncoderAction.LONG:
            self._go_back()

    def _go_back(self) -> None:
        if self.rel_path:
            parent = os.path.dirname(self.rel_path)
            self.nav.switch_screen(LibraryScreen(self.nav, parent))
        else:
            self.nav.switch_screen(MainMenuScreen(self.nav))

    def _adjust_scroll(self) -> None:
        per_page = self.nav.renderer.items_per_page
        if self.selected < self.scroll_offset:
            self.scroll_offset = self.selected
        elif self.selected >= self.scroll_offset + per_page:
            self.scroll_offset = self.selected - per_page + 1


class Navigator:
    def __init__(
        self,
        video_processor: VideoProcessor,
        renderer: Renderer,
        streamer: Streamer,
        version: str,
        icon_path: str = 'icon.png',
    ):
        self.video_processor = video_processor
        self.renderer = renderer
        self.streamer = streamer
        self.version = version
        self.icon_path = icon_path

        self._lock = threading.Lock()
        self._screen: Screen = SplashScreen(self)
        self._playback: Optional[Playback] = None
        self._return_screen: Optional[Screen] = None
        self._temp_message: Optional[str] = None
        self._temp_until: float = 0

    def handle_action(self, action: EncoderAction) -> None:
        with self._lock:
            if self._temp_message and time.time() < self._temp_until:
                return

            self._temp_message = None

            if self._playback:
                if action == EncoderAction.LONG:
                    self._playback = None
                    if self._return_screen:
                        self._screen = self._return_screen
                        if isinstance(self._screen, LibraryScreen):
                            self._screen.refresh()
                        self._return_screen = None
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
                frame = self._playback.get_next_frame()
                if frame:
                    return frame
                self._playback = None
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
