"""Small explicit LRU budget for on-demand FITS display-only rasters.

This is not a scientific data cache. Arrays remain authoritative in the
read-only FITS dataset; only uint8 RGB/RGBA view buffers are cached.
"""
from __future__ import annotations

from collections import OrderedDict
import numpy as np


class BoundedRasterCache:
    def __init__(self, max_bytes: int = 48 * 1024 * 1024):
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.max_bytes = max_bytes
        self._items: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self.bytes_used = 0

    def get(self, key):
        if key not in self._items:
            return None
        self._items.move_to_end(key)
        return self._items[key]

    def put(self, key, value: np.ndarray):
        if not isinstance(value, np.ndarray) or value.dtype != np.uint8:
            raise ValueError("Only uint8 display arrays may be cached")
        if key in self._items:
            self.bytes_used -= self._items.pop(key).nbytes
        if value.nbytes > self.max_bytes:
            return False
        while self._items and self.bytes_used + value.nbytes > self.max_bytes:
            _, removed = self._items.popitem(last=False)
            self.bytes_used -= removed.nbytes
        value.setflags(write=False)
        self._items[key] = value
        self.bytes_used += value.nbytes
        return True

    def clear(self):
        self._items.clear()
        self.bytes_used = 0

    @property
    def count(self) -> int:
        return len(self._items)
