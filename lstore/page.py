import struct

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


class PageRange:

    def __init__(self, num_columns):
        self.num_columns = num_columns
        self.base_pages = [
            [Page() for _ in range(num_columns)]
            for _ in range(BASE_PAGES_PER_RANGE)
        ]
        self.tail_pages = []
        self.base_page_idx = 0
        self.base_slot = 0
        self.tail_slot = 0

    def has_base_capacity(self):
        return self.base_page_idx < BASE_PAGES_PER_RANGE

    def _column_pages(self, is_tail, page_idx):
        if is_tail:
            return self.tail_pages[page_idx]
        return self.base_pages[page_idx]

    def _write_row(self, column_pages, slot, values):
        assert len(values) == self.num_columns
        for column, value in enumerate(values):
            page = column_pages[column]
            if page.num_records == slot:
                page.write(value)
            else:
                page.update(slot, value)

    def append_base(self, values):
        if not self.has_base_capacity():
            return None
        page_idx = self.base_page_idx
        slot = self.base_slot
        self._write_row(self.base_pages[page_idx], slot, values)
        self.base_slot += 1
        if self.base_slot == RECORDS_PER_PAGE:
            self.base_slot = 0
            self.base_page_idx += 1
        return (page_idx, slot)

    def append_tail(self, values):
       assert len(values) == self.num_columns
       if not self.tail_pages or self.tail_slot == RECORDS_PER_PAGE:
           self.tail_pages.append([Page() for _ in range(self.num_columns)])
           self.tail_slot = 0
       page_idx = len(self.tail_pages) - 1
       slot = self.tail_slot
       self._write_row(self.tail_pages[page_idx], slot, values)
       self.tail_slot += 1
       return (page_idx, slot)

    def read(self, is_tail, page_idx, slot):
        column_pages = self._column_pages(is_tail, page_idx)
        return [page.read(slot) for page in column_pages]

    def write_cell(self, is_tail, page_idx, slot, column, value):
        self._column_pages(is_tail, page_idx)[column].update(slot, value)
