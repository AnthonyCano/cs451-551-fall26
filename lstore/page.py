import struct
from contextlib import nullcontext

from lstore.config import (
    BASE_PAGES_PER_RANGE,
    PAGE_SIZE,
    RECORD_SIZE,
    RECORDS_PER_PAGE,
)

assert struct.calcsize("<q") == RECORD_SIZE, "RECORD_SIZE must match the struct format"

class Page:

    def __init__(self):
        self.num_records = 0
        self.data = bytearray(PAGE_SIZE)

    def has_capacity(self):
        return self.num_records < RECORDS_PER_PAGE

    def write(self, value):
        if not self.has_capacity():
            return False
        offset = self.num_records * RECORD_SIZE
        struct.pack_into("<q", self.data, offset, value)
        self.num_records += 1
        return True

    def read(self, slot):
        offset = slot * RECORD_SIZE
        return struct.unpack_from("<q", self.data, offset)[0]

    def update(self, slot, value):
        offset = slot * RECORD_SIZE
        struct.pack_into("<q", self.data, offset, value)

    # On disk: the raw 4096 page bytes, then num_records as an 8-byte trailer
    def to_bytes(self):
        return bytes(self.data) + struct.pack("<q", self.num_records)

    @classmethod
    def from_bytes(cls, raw):
        if len(raw) != PAGE_SIZE + RECORD_SIZE:
            raise ValueError(f"page file must be {PAGE_SIZE + RECORD_SIZE} bytes, got {len(raw)}")
        page = cls()
        page.data = bytearray(raw[:PAGE_SIZE])
        page.num_records = struct.unpack_from("<q", raw, PAGE_SIZE)[0]
        return page


class PageRange:

    """
    A page range holds 16 base pages plus a growing list of tail pages, one
    physical Page per column. With no bufferpool the Pages live in plain lists
    (Assignment 1 behaviour). With a bufferpool, nothing is held here: every
    access asks the pool for the page by key, so the pool can evict it.
    """
    def __init__(self, num_columns, pool=None, table_name=None, range_idx=None):
        self.num_columns = num_columns
        self.pool = pool
        self.table_name = table_name
        self.range_idx = range_idx
        if pool is None:
            self.base_pages = [
                [Page() for _ in range(num_columns)]
                for _ in range(BASE_PAGES_PER_RANGE)
            ]
        self.tail_pages = []  # only used without a pool
        self.num_tail_pages = 0
        self.base_page_idx = 0
        self.base_slot = 0
        self.tail_slot = 0
        # merge bookkeeping, owned by Table
        self.merging = False
        self.merged_tails = 0  # tail records already folded into the base pages

    def has_base_capacity(self):
        return self.base_page_idx < BASE_PAGES_PER_RANGE

    # Total base / tail records written so far
    def base_count(self):
        return self.base_page_idx * RECORDS_PER_PAGE + self.base_slot

    def tail_count(self):
        if self.num_tail_pages == 0:
            return 0
        return (self.num_tail_pages - 1) * RECORDS_PER_PAGE + self.tail_slot

    # The pool lock is re-entrant, so holding it across get_page -> write ->
    # mark_dirty means a page can't be evicted between the write and the dirty flag
    def lock(self):
        return self.pool.lock if self.pool is not None else nullcontext()

    def page(self, is_tail, page_idx, column):
        if self.pool is None:
            pages = self.tail_pages if is_tail else self.base_pages
            return pages[page_idx][column]
        return self.pool.get_page((self.table_name, self.range_idx, is_tail, page_idx, column))

    def mark_dirty(self, is_tail, page_idx, column):
        if self.pool is not None:
            self.pool.mark_dirty((self.table_name, self.range_idx, is_tail, page_idx, column))

    def _write_row(self, is_tail, page_idx, slot, values):
        assert len(values) == self.num_columns
        with self.lock():
            for column, value in enumerate(values):
                page = self.page(is_tail, page_idx, column)
                if page.num_records == slot:
                    page.write(value)
                else:
                    page.update(slot, value)
                self.mark_dirty(is_tail, page_idx, column)

    def append_base(self, values):
        if not self.has_base_capacity():
            return None
        page_idx = self.base_page_idx
        slot = self.base_slot
        self._write_row(False, page_idx, slot, values)
        self.base_slot += 1
        if self.base_slot == RECORDS_PER_PAGE:
            self.base_slot = 0
            self.base_page_idx += 1
        return (page_idx, slot)

    def append_tail(self, values):
        assert len(values) == self.num_columns
        if self.num_tail_pages == 0 or self.tail_slot == RECORDS_PER_PAGE:
            if self.pool is None:
                self.tail_pages.append([Page() for _ in range(self.num_columns)])
            self.num_tail_pages += 1
            self.tail_slot = 0
        page_idx = self.num_tail_pages - 1
        slot = self.tail_slot
        self._write_row(True, page_idx, slot, values)
        self.tail_slot += 1
        return (page_idx, slot)

    def read(self, is_tail, page_idx, slot):
        with self.lock():
            return [self.page(is_tail, page_idx, c).read(slot) for c in range(self.num_columns)]

    def read_cell(self, is_tail, page_idx, slot, column):
        with self.lock():
            return self.page(is_tail, page_idx, column).read(slot)

    def write_cell(self, is_tail, page_idx, slot, column, value):
        with self.lock():
            self.page(is_tail, page_idx, column).update(slot, value)
            self.mark_dirty(is_tail, page_idx, column)
