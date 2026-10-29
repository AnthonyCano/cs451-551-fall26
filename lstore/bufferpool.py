import os
import threading
from collections import OrderedDict

from lstore.config import BUFFERPOOL_SIZE
from lstore.page import Page


class BufferPool:
    """
    The stacks: a fixed number of pages kept in memory, the rest on disk.

    A key is (table, range_idx, is_tail, page_idx, column). Usage:
        page = pool.get_page(key); pool.pin(key)
        ... read / write page ...
        pool.mark_dirty(key)  # only if you wrote
        pool.unpin(key)
    """

    def __init__(self, path, capacity=BUFFERPOOL_SIZE):
        self.path = path
        self.capacity = capacity
        self.frames = OrderedDict()  # key -> Page, least recently used first
        self.pins = {}
        self.dirty = set()
        # merge runs on a background thread and shares the pool
        self.lock = threading.RLock()
        os.makedirs(path, exist_ok=True)

    def _path(self, key):
        table, range_idx, is_tail, page_idx, column = key
        kind = "t" if is_tail else "b"
        return os.path.join(self.path, table, f"r{range_idx}_{kind}{page_idx}_c{column}.page")

    def _write(self, key):
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(self.frames[key].to_bytes())
        os.replace(tmp, path)  # no torn page if we crash mid-write
        self.dirty.discard(key)

    def _evict(self):
        for key in self.frames:
            if self.pins.get(key, 0) == 0:
                if key in self.dirty:
                    self._write(key)
                del self.frames[key]
                self.pins.pop(key, None)
                return
        raise RuntimeError("bufferpool full and every page is pinned (missing unpin?)")

    def get_page(self, key):
        with self.lock:
            if key in self.frames:
                self.frames.move_to_end(key)
                return self.frames[key]
            if len(self.frames) >= self.capacity:
                self._evict()
            path = self._path(key)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    page = Page.from_bytes(f.read())
            else:
                page = Page()  # new page, reaches disk on first flush
            self.frames[key] = page
            return page

    def pin(self, key):
        with self.lock:
            self.pins[key] = self.pins.get(key, 0) + 1

    def unpin(self, key):
        with self.lock:
            if self.pins.get(key, 0) <= 0:
                raise ValueError(f"unpin without pin: {key}")
            self.pins[key] -= 1

    def mark_dirty(self, key):
        with self.lock:
            self.dirty.add(key)

    def flush_all(self):
        with self.lock:
            for key in list(self.dirty):
                self._write(key)
