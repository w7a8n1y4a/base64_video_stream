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

        right_x = 72
        r.draw_text(canvas, right_x, 4, 'Pepeunit', font=r._font)
        r.draw_text(canvas, right_x, 16, 'Stream', font=r._font)
        r.draw_text(canvas, right_x, 30, f'v{self.nav.version}', font=r._font_small)
        fps = int(self.nav.video_processor.target_fps)
        r.draw_text(canvas, right_x, 40, f'FPS: {fps}', font=r._font_small)
        r.draw_text(canvas, right_x, 52, 'AGPLv3', font=r._font_small)

        return canvas

    def on_action(self, action: EncoderAction) -> None:
        self.nav.switch_screen(MainMenuScreen(self.nav))


class MainMenuScreen(Screen):
    ITEMS = ['Библиотека', 'Рандом', 'Действия']

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
            elif self.selected == 2:
                self.nav.switch_screen(ActionsScreen(self.nav))
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
            elif not entry.is_dir:
                self.nav.switch_screen(VideoDetailScreen(self.nav, entry))
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


class VideoDetailScreen(Screen):
    REFRESH_INTERVAL = 1.0

    def __init__(self, navigator: Navigator, entry):
        super().__init__(navigator)
        self.entry = entry
        self._last_refresh = 0.0
        self._refresh_info()

    def _refresh_info(self) -> None:
        self.info = self.nav.video_processor.get_video_info(self.entry.rel_path)
        self._last_refresh = time.monotonic()

    def render(self) -> Image.Image:
        if time.monotonic() - self._last_refresh >= self.REFRESH_INTERVAL:
            self._refresh_info()

        r = self.nav.renderer
        canvas = r.create_canvas()

        name = os.path.splitext(self.entry.name)[0]
        name = r._truncate_text(name, r.width - 4, r._font)
        r.draw_header(canvas, name)

        y = r._content_y + 1
        status = self.info.get('status', VideoStatus.PENDING)
        target_fps = int(self.nav.video_processor.target_fps)

        if status == VideoStatus.ERROR:
            r.draw_text(canvas, 2, y, 'Статус: Ошибка', font=r._font_small)
            y += r._line_height_small
            error = self.info.get('error', 'Неизвестно')
            r.draw_text_wrapped(canvas, 2, y, error, r.width - 4, font=r._font_small)

        elif status == VideoStatus.PROCESSING:
            progress = self.info.get('progress', 0)
            r.draw_text(canvas, 2, y, f'Обработка: {progress}%', font=r._font_small)
            y += r._line_height_small
            r.draw_text(canvas, 2, y, f'Целевой FPS: {target_fps}', font=r._font_small)

        elif status == VideoStatus.PENDING:
            queue = self.info.get('queue_position', 0)
            r.draw_text(canvas, 2, y, 'Статус: Ожидание', font=r._font_small)
            y += r._line_height_small
            r.draw_text(canvas, 2, y, f'В очереди перед: {queue}', font=r._font_small)
            y += r._line_height_small
            r.draw_text(canvas, 2, y, f'Целевой FPS: {target_fps}', font=r._font_small)

        elif status == VideoStatus.READY:
            fps_val = self.info.get('fps')
            fps_str = str(int(fps_val)) if fps_val is not None else '?'
            frames = self.info.get('frame_count', '?')
            r.draw_text(canvas, 2, y, 'Статус: Готово', font=r._font_small)
            y += r._line_height_small
            r.draw_text(canvas, 2, y, f'FPS: {fps_str}', font=r._font_small)
            y += r._line_height_small
            r.draw_text(canvas, 2, y, f'Кадров: {frames}', font=r._font_small)

        elif status == VideoStatus.WARNING:
            file_fps = self.info.get('fps')
            file_fps_str = str(int(file_fps)) if file_fps is not None else '?'
            frames = self.info.get('frame_count', '?')
            r.draw_text(canvas, 2, y, 'FPS не совпадает!', font=r._font_small)
            y += r._line_height_small
            r.draw_text(canvas, 2, y, f'Сейчас: {target_fps}  Файл: {file_fps_str}', font=r._font_small)
            y += r._line_height_small
            r.draw_text(canvas, 2, y, f'Кадров: {frames}', font=r._font_small)
            y += r._line_height_small
            btn_text = 'Переделать'
            tw = r._text_width(btn_text, r._font_small)
            r.draw_rect(canvas, 1, y, tw + 4, r._line_height_small, fill=1)
            r.draw_text(canvas, 3, y, btn_text, font=r._font_small, color=0)

        return canvas

    def on_action(self, action: EncoderAction) -> None:
        if action == EncoderAction.LONG:
            parent = os.path.dirname(self.entry.rel_path)
            self.nav.switch_screen(LibraryScreen(self.nav, parent))
        elif action == EncoderAction.ONE:
            if self.info.get('status') == VideoStatus.WARNING:
                self.nav.video_processor.force_reprocess(self.entry.rel_path)
                self.nav.show_message('Переделка\nзапущена')
                parent = os.path.dirname(self.entry.rel_path)
                self.nav.switch_screen(LibraryScreen(self.nav, parent))


class ActionsScreen(Screen):
    ITEMS = ['Очистка txt', 'Информация']

    def __init__(self, navigator: Navigator):
        super().__init__(navigator)
        self.selected = 0

    def render(self) -> Image.Image:
        r = self.nav.renderer
        canvas = r.create_canvas()
        items = [(name, None, None, 0) for name in self.ITEMS]
        r.draw_menu(canvas, items, self.selected, 0, title='Действия')
        return canvas

    def on_action(self, action: EncoderAction) -> None:
        if action == EncoderAction.LEFT:
            self.selected = (self.selected + 1) % len(self.ITEMS)
        elif action == EncoderAction.RIGHT:
            self.selected = (self.selected - 1) % len(self.ITEMS)
        elif action == EncoderAction.ONE:
            if self.selected == 0:
                self.nav.video_processor.clear_all_txt()
                self.nav.show_message('Все txt\nудалены')
            elif self.selected == 1:
                self.nav.switch_screen(SplashScreen(self.nav))
        elif action == EncoderAction.LONG:
            self.nav.switch_screen(MainMenuScreen(self.nav))
