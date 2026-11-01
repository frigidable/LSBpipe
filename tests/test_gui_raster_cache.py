"""Bounded display cache: eviction, immutable RGB and memory accounting."""
import numpy as np
import pytest
from lsbpipe.gui.raster_cache import BoundedRasterCache


def test_lru_bytes_and_recent_entry_survive_eviction():
    cache = BoundedRasterCache(max_bytes=32)
    a = np.ones((2, 2, 3), dtype=np.uint8)  # 12 bytes
    b = np.full((2, 2, 3), 2, dtype=np.uint8)
    c = np.full((2, 2, 3), 3, dtype=np.uint8)
    assert cache.put(("r",1), a)
    assert cache.put(("r",2), b)
    assert cache.bytes_used == 24
    assert cache.get(("r",1)) is a
    assert cache.put(("r",3), c)
    assert cache.get(("r",2)) is None
    assert cache.get(("r",1)) is a
    assert cache.get(("r",3)) is c
    assert cache.bytes_used == 24
    assert cache.count == 2
    assert not a.flags.writeable
    with pytest.raises(ValueError):
        a[0,0,0] = 40


def test_large_display_not_cached_and_old_entries_unmodified():
    cache = BoundedRasterCache(max_bytes=8)
    one = np.arange(6, dtype="u1")
    assert cache.put(("small",), one)
    huge = np.zeros((3, 3, 4), dtype="u1")
    assert not cache.put(("huge",), huge)
    assert cache.count == 1
    assert cache.get(("small",)) is one
    cache.clear()
    assert cache.count == 0
    assert cache.bytes_used == 0


def test_replace_existing_key_and_reject_invalid_types():
    cache = BoundedRasterCache(max_bytes=20)
    assert cache.put("a", np.zeros((5,), dtype="u1"))
    assert cache.put("a", np.zeros((12,), dtype="u1"))
    assert cache.count == 1
    assert cache.bytes_used == 12
    assert not cache.put("a", np.zeros((22,), dtype="u1"))
    assert cache.bytes_used == 0
    with pytest.raises(ValueError, match="uint8"):
        cache.put("b", np.zeros((2,), dtype="f4"))
    with pytest.raises(ValueError, match="positive"):
        BoundedRasterCache(max_bytes=0)
