import os
import json
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import cv2
import numpy as np
from PIL import Image

from .enums import VideoStatus
from .renderer import pixels_to_sh1106_base64


@dataclass
class FileEntry:
    name: str
    rel_path: str
    is_dir: bool
    status: Optional[VideoStatus] = None
    progress: int = 0


def floyd_steinberg_dither(img: np.ndarray) -> np.ndarray:
    img = img.astype(np.float32)
    h, w = img.shape
    for y in range(h - 1):
        for x in range(1, w - 1):
            old = img[y, x]
            new = 255.0 if old >= 128 else 0.0
            err = old - new
            img[y, x] = new
            img[y, x + 1] += err * 7 / 16
            img[y + 1, x - 1] += err * 3 / 16
            img[y + 1, x] += err * 5 / 16
            img[y + 1, x + 1] += err * 1 / 16
    return np.clip(img, 0, 255).astype(np.uint8)


def enhance_for_binary(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrast = clahe.apply(gray)
    blur1 = cv2.GaussianBlur(contrast, (0, 0), 1.0)
    blur2 = cv2.GaussianBlur(contrast, (0, 0), 2.0)
    dog = cv2.subtract(blur1, blur2)
    return cv2.addWeighted(contrast, 1.0, dog, 1.5, 0)


def process_frame(frame: np.ndarray, width: int, height: int) -> str:
    """Process a single BGR video frame to SH1106 base64."""
    frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    enhanced = enhance_for_binary(gray)
    binary = floyd_steinberg_dither(enhanced)
    binary = np.where(binary >= 128, 255, 0).astype(np.uint8)
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
    binary = floyd_steinberg_dither(enhanced)
    binary = np.where(binary >= 128, 255, 0).astype(np.uint8)
    return Image.fromarray(binary, mode='L').convert('1')


class VideoProcessor:
    def __init__(
        self,
        videos_dir: str,
        client,
        width: int = 128,
        height: int = 64,
        target_fps: float = 10.0,
        scan_interval: float = 10.0,
    ):
        self.videos_dir = videos_dir
        self.client = client
        self.width = width
        self.height = height
        self.target_fps = target_fps
        self.scan_interval = scan_interval

        self._state: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        os.makedirs(videos_dir, exist_ok=True)

    def scan_and_sync(self) -> None:
        """Scan the videos directory and synchronize internal state with reality."""
        stored = self._load_remote_state()
        current_files = self._find_all_mp4()

        new_state: Dict[str, dict] = {}
        for rel in current_files:
            frames_path = self._frames_path(rel)
            if rel in stored:
                entry = dict(stored[rel])
                old_status = entry.get('status', VideoStatus.PENDING.value)
                if old_status == VideoStatus.READY.value:
                    if os.path.isfile(frames_path):
                        new_state[rel] = entry
                    else:
                        new_state[rel] = {'status': VideoStatus.PENDING.value}
                elif old_status == VideoStatus.PROCESSING.value:
                    new_state[rel] = {'status': VideoStatus.PENDING.value}
                else:
                    new_state[rel] = entry
            else:
                if os.path.isfile(frames_path):
                    new_state[rel] = {'status': VideoStatus.READY.value}
                else:
                    new_state[rel] = {'status': VideoStatus.PENDING.value}

        with self._lock:
            self._state = new_state

        self._save_remote_state()

    def get_status(self, rel_path: str) -> VideoStatus:
        with self._lock:
            entry = self._state.get(rel_path)
        if entry is None:
            return VideoStatus.PENDING
        try:
            return VideoStatus(entry['status'])
        except (KeyError, ValueError):
            return VideoStatus.PENDING

    def get_progress(self, rel_path: str) -> int:
        with self._lock:
            entry = self._state.get(rel_path)
        if entry is None:
            return 0
        return entry.get('progress', 0)

    def get_frames_path(self, rel_path: str) -> str:
        return self._frames_path(rel_path)

    def list_directory(self, rel_path: str = '') -> List[FileEntry]:
        """List directories and mp4 files in a given relative path under videos_dir."""
        abs_path = os.path.join(self.videos_dir, rel_path) if rel_path else self.videos_dir
        if not os.path.isdir(abs_path):
            return []

        dirs: List[FileEntry] = []
        files: List[FileEntry] = []

        try:
            entries = sorted(os.listdir(abs_path))
        except OSError:
            return []

        for name in entries:
            if name.startswith('.'):
                continue
            full = os.path.join(abs_path, name)
            item_rel = os.path.join(rel_path, name) if rel_path else name

            if os.path.isdir(full):
                dirs.append(FileEntry(name=name, rel_path=item_rel, is_dir=True))
            elif name.lower().endswith('.mp4'):
                status = self.get_status(item_rel)
                progress = self.get_progress(item_rel)
                files.append(
                    FileEntry(
                        name=name,
                        rel_path=item_rel,
                        is_dir=False,
                        status=status,
                        progress=progress,
                    )
                )
        return dirs + files

    def get_ready_videos_in(self, rel_path: str) -> List[str]:
        """Get all Ready video relative paths in a directory (non-recursive)."""
        abs_path = os.path.join(self.videos_dir, rel_path) if rel_path else self.videos_dir
        result = []
        try:
            for name in sorted(os.listdir(abs_path)):
                if name.lower().endswith('.mp4'):
                    video_rel = os.path.join(rel_path, name) if rel_path else name
                    if self.get_status(video_rel) == VideoStatus.READY:
                        result.append(video_rel)
        except OSError:
            pass
        return result

    def get_all_ready_videos(self) -> List[str]:
        """Get all Ready video paths across the entire library."""
        with self._lock:
            return sorted(
                rel for rel, info in self._state.items()
                if info.get('status') == VideoStatus.READY.value
            )

    def start_background(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _process_loop(self) -> None:
        while self._running:
            pending = self._get_next_pending()
            if pending:
                self._process_video(pending)
                self._save_remote_state()
            else:
                for _ in range(int(self.scan_interval * 10)):
                    if not self._running:
                        return
                    time.sleep(0.1)
                self.scan_and_sync()

    def _get_next_pending(self) -> Optional[str]:
        with self._lock:
            for rel, info in self._state.items():
                if info.get('status') == VideoStatus.PENDING.value:
                    return rel
        return None

    def _process_video(self, rel_path: str) -> None:
        abs_path = os.path.join(self.videos_dir, rel_path)
        frames_path = self._frames_path(rel_path)

        with self._lock:
            self._state[rel_path] = {'status': VideoStatus.PROCESSING.value, 'progress': 0}

        try:
            parent_dir = os.path.dirname(frames_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            cap = cv2.VideoCapture(abs_path)
            if not cap.isOpened():
                raise RuntimeError(f'Cannot open video: {abs_path}')

            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
            src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            frame_interval = src_fps / self.target_fps

            frame_index = 0
            next_frame = 0.0
            processed = 0

            with open(frames_path, 'w', encoding='utf-8') as out:
                while self._running:
                    ret, frame = cap.read()
                    if not ret:
                        break

                    if frame_index >= next_frame:
                        b64 = process_frame(frame, self.width, self.height)
                        out.write(b64 + '\n')
                        next_frame += frame_interval
                        processed += 1

                    frame_index += 1
                    if frame_index % 50 == 0:
                        with self._lock:
                            self._state[rel_path]['progress'] = min(
                                99, int(frame_index / total * 100)
                            )

            cap.release()

            if not self._running:
                with self._lock:
                    self._state[rel_path] = {'status': VideoStatus.PENDING.value}
                try:
                    os.remove(frames_path)
                except OSError:
                    pass
                return

            with self._lock:
                self._state[rel_path] = {
                    'status': VideoStatus.READY.value,
                    'progress': 100,
                    'frame_count': processed,
                }

            self.client.logger.info(f'Video processed: {rel_path} ({processed} frames)')

        except Exception as e:
            with self._lock:
                self._state[rel_path] = {
                    'status': VideoStatus.ERROR.value,
                    'error': str(e),
                }
            self.client.logger.error(f'Video processing error ({rel_path}): {e}')

    def _frames_path(self, rel_path: str) -> str:
        base = os.path.splitext(rel_path)[0]
        return os.path.join(self.videos_dir, base + '.txt')

    def _find_all_mp4(self) -> List[str]:
        result = []
        for root, _dirs, files in os.walk(self.videos_dir):
            for f in sorted(files):
                if f.lower().endswith('.mp4'):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, self.videos_dir)
                    result.append(rel)
        return sorted(result)

    def _load_remote_state(self) -> Dict[str, dict]:
        try:
            raw = self.client.rest_client.get_state_storage()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            self.client.logger.warning(f'Failed to load remote state: {e}')
        return {}

    def _save_remote_state(self) -> None:
        try:
            with self._lock:
                snapshot = dict(self._state)
            self.client.rest_client.set_state_storage(snapshot)
        except Exception as e:
            self.client.logger.warning(f'Failed to save remote state: {e}')
