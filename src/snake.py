from __future__ import annotations

import random
import time
from collections import deque
from typing import Optional, Set, Tuple

import numpy as np
from PIL import Image


class SnakeOverlay:
    """Animated snake that crawls over a static frame, eating white pixels.

    The snake is 2px wide (BLOCK), starts at SNAKE_LEN_INIT blocks long
    and grows by GROW_PER_EAT for each eaten white cell (capped at
    SNAKE_LEN_MAX).  It moves semi-randomly, biased toward uneaten white
    cells but with enough noise to avoid straight-line paths.  Once every
    white pixel has been consumed the body drains away and the cycle restarts.
    """

    BLOCK = 2
    SNAKE_LEN_INIT = 8
    SNAKE_LEN_MAX = 40
    GROW_PER_EAT = 1
    DRAIN_EXTRA = 4
    TARGET_TTL = 30
    START_DELAY = 5.0

    _DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    def __init__(self, width: int, height: int, duration: float = 30.0):
        self.width = width
        self.height = height
        self.cols = width // self.BLOCK
        self.rows = height // self.BLOCK

        self._body: deque[Tuple[int, int]] = deque()
        self._body_set: Set[Tuple[int, int]] = set()
        self._visited: Set[Tuple[int, int]] = set()
        self._dir: Tuple[int, int] = (1, 0)

        self._eaten_mask = np.zeros((height, width), dtype=bool)
        self._white_cells: Set[Tuple[int, int]] = set()
        self._uneaten: Set[Tuple[int, int]] = set()
        self._base_ready = False
        self._drain_left = -1
        self._max_len = self.SNAKE_LEN_INIT

        self._target: Optional[Tuple[int, int]] = None
        self._target_ttl = 0

        self._sps = (self.cols * self.rows) / duration
        self._last_t = 0.0
        self._step_accum = 0.0
        self._born = time.monotonic()

        self._place_snake()

    def _place_snake(self) -> None:
        self._body.clear()
        self._body_set.clear()
        self._visited.clear()
        self._eaten_mask[:] = False
        self._dir = (1, 0)
        self._max_len = self.SNAKE_LEN_INIT
        self._target = None
        self._target_ttl = 0

        sx, sy = self.cols // 2, self.rows // 2
        for i in range(self.SNAKE_LEN_INIT):
            p = (sx - self.SNAKE_LEN_INIT + 1 + i, sy)
            self._body.append(p)
            self._body_set.add(p)
            self._visited.add(p)

        self._uneaten = self._white_cells - self._visited
        self._drain_left = -1
        self._last_t = time.monotonic()
        self._step_accum = 0.0

    def _scan_base(self, img: Image.Image) -> None:
        arr = np.array(img.convert('L'), dtype=np.uint8)
        self._white_cells.clear()
        for gy in range(self.rows):
            for gx in range(self.cols):
                y0, x0 = gy * self.BLOCK, gx * self.BLOCK
                if arr[y0:y0 + self.BLOCK, x0:x0 + self.BLOCK].any():
                    self._white_cells.add((gx, gy))
        self._uneaten = self._white_cells - self._visited
        self._base_ready = True

    def _refresh_target(self) -> None:
        if not self._uneaten:
            self._target = None
            return
        hx, hy = self._body[-1]
        by_dist = sorted(
            self._uneaten,
            key=lambda c: abs(c[0] - hx) + abs(c[1] - hy),
        )
        pool_end = max(1, len(by_dist) // 3)
        self._target = random.choice(by_dist[:pool_end])
        self._target_ttl = self.TARGET_TTL

    def _pop_tail(self) -> None:
        if not self._body:
            return
        tail = self._body.popleft()
        self._body_set.discard(tail)
        ty, tx = tail[1] * self.BLOCK, tail[0] * self.BLOCK
        self._eaten_mask[ty:ty + self.BLOCK, tx:tx + self.BLOCK] = True

    def _choose(
        self,
        valid: list,
        hx: int,
        hy: int,
        dx: int,
        dy: int,
    ) -> Tuple[int, int]:
        fresh = [d for d in valid if (hx + d[0], hy + d[1]) not in self._visited]

        if self._target_ttl <= 0 or self._target is None or self._target in self._visited:
            self._refresh_target()
        self._target_ttl -= 1

        pool = fresh if fresh else valid

        if self._target and len(pool) > 1:
            tx, ty = self._target
            scored = sorted(
                pool,
                key=lambda d: abs(hx + d[0] - tx) + abs(hy + d[1] - ty),
            )
            if random.random() < 0.65:
                return scored[0]
            return random.choice(scored[1:])

        if not fresh and (dx, dy) in pool and random.random() < 0.5:
            return (dx, dy)

        return random.choice(pool)

    def _step(self) -> None:
        if self._drain_left >= 0:
            self._drain_left -= 1
            self._pop_tail()
            if self._drain_left < 0 or not self._body:
                self._place_snake()
            return

        hx, hy = self._body[-1]
        dx, dy = self._dir

        candidates = [(dx, dy), (-dy, dx), (dy, -dx)]

        valid = []
        for d in candidates:
            nx, ny = hx + d[0], hy + d[1]
            if 0 <= nx < self.cols and 0 <= ny < self.rows and (nx, ny) not in self._body_set:
                valid.append(d)

        if not valid:
            for d in self._DIRS:
                nx, ny = hx + d[0], hy + d[1]
                if 0 <= nx < self.cols and 0 <= ny < self.rows and (nx, ny) not in self._body_set:
                    valid.append(d)

        if not valid:
            self._pop_tail()
            return

        chosen = self._choose(valid, hx, hy, dx, dy)

        self._dir = chosen
        nh = (hx + chosen[0], hy + chosen[1])

        self._body.append(nh)
        self._body_set.add(nh)

        ate = False
        if nh not in self._visited:
            self._visited.add(nh)
            if nh in self._uneaten:
                self._uneaten.discard(nh)
                ate = True

        if ate:
            self._max_len = min(self._max_len + self.GROW_PER_EAT, self.SNAKE_LEN_MAX)

        if len(self._body) > self._max_len:
            self._pop_tail()

        if not self._uneaten and self._white_cells:
            self._drain_left = len(self._body) + self.DRAIN_EXTRA

    def apply(self, base: Image.Image) -> Image.Image:
        if time.monotonic() - self._born < self.START_DELAY:
            return base

        if not self._base_ready:
            self._scan_base(base)

        now = time.monotonic()
        self._step_accum += (now - self._last_t) * self._sps
        self._last_t = now
        steps = int(self._step_accum)
        if steps > 0:
            self._step_accum -= steps
            for _ in range(min(steps, 40)):
                self._step()

        arr = np.array(base.convert('L'), dtype=np.uint8)
        arr[self._eaten_mask] = 0

        for gx, gy in self._body:
            y0, x0 = gy * self.BLOCK, gx * self.BLOCK
            arr[y0:y0 + self.BLOCK, x0:x0 + self.BLOCK] = 255

        return Image.fromarray(arr, mode='L').convert('1')
