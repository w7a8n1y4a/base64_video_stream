import random
from typing import Optional, List

from .video_processor import VideoProcessor


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

    def get_current_frame(self) -> Optional[str]:
        """Return the frame at the current position without advancing."""
        if not self._frames:
            return None
        idx = min(self._frame_idx, len(self._frames) - 1)
        return self._frames[idx]

    def seek(self, delta_frames: int) -> bool:
        """Seek forward (positive) or backward (negative) by delta_frames.

        Returns True if seek was performed, False if no frames are available.
        """
        if not self._frames:
            return False

        new_idx = self._frame_idx + delta_frames
        if new_idx < 0:
            self._frame_idx = 0
        elif new_idx >= len(self._frames):
            self._current_idx += 1
            if self._current_idx >= len(self._playlist):
                if self._shuffle:
                    random.shuffle(self._playlist)
                self._current_idx = 0
            self._load_current()
        else:
            self._frame_idx = new_idx
        return True

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
