from __future__ import annotations

import base64
import io
import os
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from PIL import Image

from .enums import EncoderAction, VideoStatus

if TYPE_CHECKING:
    from .navigator import Navigator


class Screen(ABC):
    def __init__(self, navigator: Navigator):
        self.nav = navigator

    @abstractmethod
    def render(self) -> Image.Image:
        ...

    @abstractmethod
    def on_action(self, action: EncoderAction) -> None:
        ...


class SplashScreen(Screen):
    _ICON_B64 = (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABAAQAAAACCEkxzAAAB1UlEQVR4nG2ST0iT'
        'cRzGP7+vv9z7rrm9gtIfELdGIl4KkjL74w7JIqpDZENK2SEoiahDh9CSQQV2jjp5'
        'yAo0yhBysaD0zepiUYNIigXu0EIr8I223GF/OjTBQ8/pw/M88FweWCUFIIA3XQGs'
        'FXAqHV90xQlVoGRXIm8a0FjWOWW0v9Oc5/3m/BRAVTlXKefiMdCA6+UHEDB9u/6N'
        'dYQkD8Dej6cvBgyBF7mNr+bzoMsl8zAIRZ7uXAQBp8m3PgaEswMPlm1QrtyO8G8b'
        'KRuphYVb/iFF481UMvhnSNERjDfXjhkwmOyc8lxCURwdafu0OIP0B2fPeHqRUn9r'
        'oq3JQehLhqfTDkLP7oJ2H0MQ+/WbxIyGQbPuTuKRpXh+6lfnsvkNpmNH6mQUIRW'
        'Ye2IohMy2y5HoONCY2X91ZCvChapZz/VulNTcXbq/fVMv+vZPt/9oDbLW2dPeN96'
        'T1vmGdW8L9Qdt5XuczU0Oz1frfCJ5w919wlGu6uGHyKECOhM5uzRpWYquwudS4Pg'
        'XoevHQEPqa0xjXzFPZuMIE8Ut3qigcE+sebbBEwW5Vh8/AKCa7+3zx0BJzKw1QCF'
        'jc7aUbCDU4rZA4Hsk3IoB0KJX3SKEWPxXmr9iZpVFmU8T1AAAAABJRU5ErkJggg=='
    )
    _icon_cache: Image.Image | None = None

    @classmethod
    def _get_icon(cls) -> Image.Image:
        if cls._icon_cache is None:
            cls._icon_cache = Image.open(io.BytesIO(base64.b64decode(cls._ICON_B64)))
        return cls._icon_cache

    def render(self) -> Image.Image:
        r = self.nav.renderer
        canvas = r.create_canvas()

        r.paste_image(canvas, self._get_icon(), 0, 0)

        right_x = 74
        r.draw_text(canvas, right_x, 4, 'Pepeunit', font=r._font)
        r.draw_text(canvas, right_x, 16, 'Stream', font=r._font)
        r.draw_text(canvas, right_x, 32, f'v{self.nav.version}', font=r._font_small)
        r.draw_text(canvas, right_x, 50, 'AGPLv3', font=r._font_small)

        return canvas

    def on_action(self, action: EncoderAction) -> None:
        self.nav.switch_screen(MainMenuScreen(self.nav))


class MainMenuScreen(Screen):
    ITEMS = ['Библиотека', 'Рандом']

    def __init__(self, navigator: Navigator):
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
            self.selected = (self.selected + 1) % len(self.ITEMS)
        elif action == EncoderAction.RIGHT:
            self.selected = (self.selected - 1) % len(self.ITEMS)
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
    REFRESH_INTERVAL = 2.0

    def __init__(self, navigator: Navigator, rel_path: str = ''):
        super().__init__(navigator)
        self.rel_path = rel_path
        self.selected = 0
        self.scroll_offset = 0
        self.entries = self.nav.video_processor.list_directory(rel_path)
        self._last_refresh = time.monotonic()

    def refresh(self) -> None:
        self.entries = self.nav.video_processor.list_directory(self.rel_path)
        self.selected = min(self.selected, max(0, len(self.entries) - 1))
        self._adjust_scroll()
        self._last_refresh = time.monotonic()

    def render(self) -> Image.Image:
        if time.monotonic() - self._last_refresh >= self.REFRESH_INTERVAL:
            self.refresh()
        r = self.nav.renderer
        canvas = r.create_canvas()

        title = os.path.basename(self.rel_path) if self.rel_path else 'Библиотека'
        items = []
        for e in self.entries:
            if e.is_dir:
                items.append((e.name, 'folder', None, 0))
            elif e.status == VideoStatus.PROCESSING:
                items.append((f'{e.name} {e.progress}%', None, e.status, e.progress))
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
            self.selected = (self.selected + 1) % len(self.entries)
            self._adjust_scroll()
        elif action == EncoderAction.RIGHT:
            self.selected = (self.selected - 1) % len(self.entries)
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
