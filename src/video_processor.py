import os
import json
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import cv2

from .enums import VideoStatus
from .image_utils import process_frame


@dataclass
class FileEntry:
    name: str
    rel_path: str
    is_dir: bool
    status: Optional[VideoStatus] = None
    progress: int = 0


class VideoProcessor:
    def __init__(
        self,
        videos_dir: str,
        frames_dir: str,
        client,
        width: int = 128,
        height: int = 64,
        target_fps: float = 10.0,
        scan_interval: float = 10.0,
    ):
        self.videos_dir = videos_dir
        self.frames_dir = frames_dir
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
        os.makedirs(frames_dir, exist_ok=True)

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
                        self._ensure_frame_metadata(entry, frames_path)
                        new_state[rel] = entry
                    else:
                        new_state[rel] = {'status': VideoStatus.PENDING.value}
                elif old_status == VideoStatus.PROCESSING.value:
                    new_state[rel] = {'status': VideoStatus.PENDING.value}
                else:
                    new_state[rel] = entry
            else:
                if os.path.isfile(frames_path):
                    new_state[rel] = self._build_ready_entry(frames_path)
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
            status = VideoStatus(entry['status'])
        except (KeyError, ValueError):
            return VideoStatus.PENDING
        if status == VideoStatus.READY:
            stored_fps = entry.get('fps')
            if stored_fps is not None and stored_fps != self.target_fps:
                return VideoStatus.WARNING
        return status

    def get_progress(self, rel_path: str) -> int:
        with self._lock:
            entry = self._state.get(rel_path)
        if entry is None:
            return 0
        return entry.get('progress', 0)

    def get_video_info(self, rel_path: str) -> dict:
        """Return detailed info about a video including derived WARNING status."""
        with self._lock:
            entry = dict(self._state.get(rel_path, {}))

        status_str = entry.get('status', VideoStatus.PENDING.value)
        try:
            status = VideoStatus(status_str)
        except ValueError:
            status = VideoStatus.PENDING

        if status == VideoStatus.READY:
            stored_fps = entry.get('fps')
            if stored_fps is not None and stored_fps != self.target_fps:
                status = VideoStatus.WARNING

        info = dict(entry)
        info['status'] = status

        if status == VideoStatus.PENDING:
            info['queue_position'] = self._get_queue_position(rel_path)

        return info

    def force_reprocess(self, rel_path: str) -> None:
        """Delete the cached txt and mark the video as PENDING."""
        frames_path = self._frames_path(rel_path)
        try:
            os.remove(frames_path)
        except OSError:
            pass
        with self._lock:
            self._state[rel_path] = {'status': VideoStatus.PENDING.value}
        self._save_remote_state()

    def clear_all_txt(self) -> None:
        """Delete all cached txt files and reset all statuses to PENDING."""
        for root, _dirs, files in os.walk(self.frames_dir):
            for f in files:
                if f.lower().endswith('.txt'):
                    try:
                        os.remove(os.path.join(root, f))
                    except OSError:
                        pass
        with self._lock:
            for rel in self._state:
                self._state[rel] = {'status': VideoStatus.PENDING.value}
        self._save_remote_state()

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

    def _get_queue_position(self, rel_path: str) -> int:
        with self._lock:
            position = 0
            for rel, entry in self._state.items():
                if rel == rel_path:
                    return position
                status = entry.get('status')
                if status in (VideoStatus.PENDING.value, VideoStatus.PROCESSING.value):
                    position += 1
        return 0

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
                    if frame_index >= next_frame:
                        ret, frame = cap.read()
                        if not ret:
                            break
                        b64 = process_frame(frame, self.width, self.height)
                        out.write(b64 + '\n')
                        next_frame += frame_interval
                        processed += 1
                    else:
                        if not cap.grab():
                            break

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
                    'fps': self.target_fps,
                }

            self.client.logger.info(f'Video processed: {rel_path} ({processed} frames)')

        except Exception as e:
            with self._lock:
                self._state[rel_path] = {
                    'status': VideoStatus.ERROR.value,
                    'error': str(e),
                }
            self.client.logger.error(f'Video processing error ({rel_path}): {e}')

    def _ensure_frame_metadata(self, entry: dict, frames_path: str) -> None:
        """Fill in missing fps/frame_count from the frames file."""
        if 'frame_count' not in entry:
            entry['frame_count'] = self._count_frames_in_file(frames_path)
        if 'fps' not in entry:
            entry['fps'] = self.target_fps
        entry.setdefault('progress', 100)

    def _build_ready_entry(self, frames_path: str) -> dict:
        return {
            'status': VideoStatus.READY.value,
            'progress': 100,
            'frame_count': self._count_frames_in_file(frames_path),
            'fps': self.target_fps,
        }

    def _count_frames_in_file(self, frames_path: str) -> int:
        try:
            with open(frames_path, 'r', encoding='utf-8') as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def _frames_path(self, rel_path: str) -> str:
        base = os.path.splitext(rel_path)[0]
        return os.path.join(self.frames_dir, base + '.txt')

    def _find_all_mp4(self) -> List[str]:
        result = []
        for root, _dirs, files in os.walk(self.videos_dir):
            for f in sorted(files):
                if f.lower().endswith('.mp4'):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, self.videos_dir)
                    result.append(rel)
        return sorted(result)

    def _load_remote_state(self, retries: int = 3) -> Dict[str, dict]:
        for attempt in range(retries):
            try:
                raw = self.client.rest_client.get_state_storage()
                if raw:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        return data
                return {}
            except Exception as e:
                if attempt < retries - 1:
                    delay = 2 ** attempt
                    self.client.logger.warning(
                        f'Failed to load remote state (attempt {attempt + 1}/{retries}): {e}, '
                        f'retrying in {delay}s'
                    )
                    time.sleep(delay)
                else:
                    self.client.logger.warning(
                        f'Failed to load remote state after {retries} attempts: {e}'
                    )
        return {}

    def _save_remote_state(self, retries: int = 3) -> None:
        with self._lock:
            snapshot = dict(self._state)
        payload = json.dumps(snapshot)
        for attempt in range(retries):
            try:
                self.client.rest_client.set_state_storage(payload)
                return
            except Exception as e:
                if attempt < retries - 1:
                    delay = 2 ** attempt
                    self.client.logger.warning(
                        f'Failed to save remote state (attempt {attempt + 1}/{retries}): {e}, '
                        f'retrying in {delay}s'
                    )
                    time.sleep(delay)
                else:
                    self.client.logger.warning(
                        f'Failed to save remote state after {retries} attempts: {e}'
                    )
