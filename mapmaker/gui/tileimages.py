"""Finished map tiles as QImages, made on background threads and kept in memory.

The map is drawn from these tiles directly: panning and zooming only repaint
cached images at a new position and scale, which costs a few milliseconds.
Only tiles that are not cached yet are made (base-map tiles decoded from disk,
terrain tiles computed, point tiles drawn), newest request first, and requests
for tiles that have scrolled out of view are dropped."""
from __future__ import annotations

import itertools
import logging
import queue
import threading
from collections import OrderedDict

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

log = logging.getLogger("mapmaker")


class _Empty:
    """Marker for a tile that exists but has nothing to draw."""

    def __bool__(self):
        return False


EMPTY = _Empty()


def to_qimage(a) -> QImage | None:
    if a is None:
        return None
    if isinstance(a, QImage):
        return a
    a = np.ascontiguousarray(a)
    h, w = a.shape[:2]
    img = QImage(a.data, w, h, 4 * w, QImage.Format.Format_RGBA8888)
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)   # copies; fastest to draw


class TileImages(QObject):
    ready = Signal()

    def __init__(self, workers: int = 3, budget_mb: int = 200):
        super().__init__()
        self._lru: OrderedDict = OrderedDict()
        self._bytes = 0
        self._budget = budget_mb * 1024 * 1024
        self._lock = threading.Lock()
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._pending: set = set()
        self._wanted: set = set()
        self._seq = itertools.count()
        self._stop = False
        for i in range(workers):
            threading.Thread(target=self._work, name=f"maptiles-{i}", daemon=True).start()

    # ------------------------------------------------------------ cache
    def get(self, key):
        with self._lock:
            v = self._lru.get(key)
            if v is not None:
                self._lru.move_to_end(key)
            return v

    def put(self, key, img):
        size = img.sizeInBytes() if isinstance(img, QImage) else 64
        with self._lock:
            old = self._lru.pop(key, None)
            if isinstance(old, QImage):
                self._bytes -= old.sizeInBytes()
            self._lru[key] = img
            self._bytes += size
            while self._bytes > self._budget and len(self._lru) > 1:
                _k, v = self._lru.popitem(last=False)
                self._bytes -= v.sizeInBytes() if isinstance(v, QImage) else 64

    def drop(self, pred):
        """Forget tiles whose key matches pred (e.g. a layer that was switched off)."""
        with self._lock:
            for k in [k for k in self._lru if pred(k)]:
                v = self._lru.pop(k)
                self._bytes -= v.sizeInBytes() if isinstance(v, QImage) else 64

    # ------------------------------------------------------------ work
    def request(self, key, fn, priority: int = 0):
        """Make tile `key` with fn() -> RGBA array / QImage / EMPTY / None (not available yet)."""
        with self._lock:
            self._wanted.add(key)
            if key in self._pending or key in self._lru:
                return
            self._pending.add(key)
        self._q.put((priority, -next(self._seq), key, fn))

    def set_wanted(self, keys: set):
        with self._lock:
            self._wanted = set(keys)

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def _work(self):
        while not self._stop:
            _p, _s, key, fn = self._q.get()
            with self._lock:
                skip = key not in self._wanted or key in self._lru
            if skip:
                with self._lock:
                    self._pending.discard(key)
                continue
            try:
                out = fn()
                img = EMPTY if out is EMPTY else to_qimage(out)
            except Exception:
                log.exception("Map tile %s failed", key)
                img = None
            if img is not None:
                self.put(key, img)
            with self._lock:
                self._pending.discard(key)
            if img is not None:
                self.ready.emit()

    def shutdown(self):
        self._stop = True
