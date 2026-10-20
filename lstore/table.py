from lstore.index import Index
from lstore.page import PageRange
from lstore.config import NUM_METADATA_COLUMNS
from time import time

INDIRECTION_COLUMN = 0
RID_COLUMN = 1
TIMESTAMP_COLUMN = 2
SCHEMA_ENCODING_COLUMN = 3


class Record:

    def __init__(self, rid, key, columns):
        self.rid = rid
        self.key = key
        self.columns = columns

class Table:

    """
    :param name: string         #Table name
    :param num_columns: int     #Number of Columns: all columns are integer
    :param key: int             #Index of table key in columns
    """
    def __init__(self, name, num_columns, key):
        self.name = name
        self.key = key
        self.num_columns = num_columns
        # Floor map: call number -> (wing, is_slip, page, slot)
        self.page_directory = {}
        # Every wing (page range) of the library, in the order we opened them
        self.page_ranges = []
        # Next unused call number. Books and slips share one stamp, 0 is WITHDRAWN
        self.next_rid = 1
        self.index = Index(self)

    # Stamp the next call number and hand it out
    def _new_rid(self):
        rid = self.next_rid
        self.next_rid += 1
        return rid

    # Every shelf has 4 admin columns (sticky note, call number, date, checkboxes)
    # in front of the real columns
    def _physical_columns(self):
        return NUM_METADATA_COLUMNS + self.num_columns

    # Shelve a new book in the newest wing. If that wing is full of books,
    # open a fresh wing (with its own back room) and shelve it there.
    # Returns (wing_idx, page_idx, slot)
    def _shelve_base(self, row):
        if self.page_ranges:
            wing_idx = len(self.page_ranges) - 1
            spot = self.page_ranges[wing_idx].append_base(row)
            if spot is not None:
                return (wing_idx,) + spot
        self.page_ranges.append(PageRange(self._physical_columns()))
        wing_idx = len(self.page_ranges) - 1
        return (wing_idx,) + self.page_ranges[wing_idx].append_base(row)

    # New book arrives. Its sticky note points at itself (no slips yet) and its
    # checkboxes are all empty. Returns the call number we gave it.
    def insert_base(self, columns):
        rid = self._new_rid()
        row = [rid, rid, int(time()), 0] + list(columns)
        wing_idx, page_idx, slot = self._shelve_base(row)
        self.page_directory[rid] = (wing_idx, False, page_idx, slot)
        return rid

    # File an errata slip in the back room of the SAME wing as its book.
    # `indirection` is whatever the slip points back to: the book itself for the
    # first slip, otherwise the previous slip. That tells us which wing to use.
    # Note: we do not touch the book's sticky note here. Whoever files the slip
    # (query.update) moves it with write_meta once the slip exists.
    def append_tail(self, columns, indirection, schema):
        rid = self._new_rid()
        wing_idx = self.page_directory[indirection][0]
        row = [indirection, rid, int(time()), schema] + list(columns)
        page_idx, slot = self.page_ranges[wing_idx].append_tail(row)
        self.page_directory[rid] = (wing_idx, True, page_idx, slot)
        return rid

    # Walk to the right wing/shelf via the floor map and copy out the whole row
    # (admin columns included). Unknown call number -> None.
    def read_record(self, rid):
        if rid not in self.page_directory:
            return None
        wing_idx, is_tail, page_idx, slot = self.page_directory[rid]
        return self.page_ranges[wing_idx].read(is_tail, page_idx, slot)

    # Scribble on one admin cell of a book or slip (move a sticky note,
    # tick a checkbox, stamp WITHDRAWN). Returns False if we can't find the call number.
    def write_meta(self, rid, column, value):
        if rid not in self.page_directory:
            return False
        wing_idx, is_tail, page_idx, slot = self.page_directory[rid]
        self.page_ranges[wing_idx].write_cell(is_tail, page_idx, slot, column, value)
        return True

    # Fetch a book as it looked `relative_version` corrections ago:
    #   0 = latest slip, -1 = one slip back, -2 = two back, ...
    # Follow the sticky note to the newest slip, then step back along the
    # "previous slip was #Y" notes. Because slips are cumulative, whichever slip
    # we land on already holds the full row, so no stitching is needed.
    # Asking for further back than the history goes gives you the original book.
    def get_version(self, base_rid, relative_version):
        book = self.read_record(base_rid)
        if book is None:
            return None
        current = book[INDIRECTION_COLUMN]
        if current == base_rid:
            return book  # never corrected, only one version exists

        steps_back = -relative_version
        row = self.read_record(current)
        while steps_back > 0:
            previous = row[INDIRECTION_COLUMN]
            if not self.page_directory[previous][1]:
                return book  # previous note points at the book: we ran out of slips
            current = previous
            row = self.read_record(current)
            steps_back -= 1
        return row

    def __merge(self):
        print("merge is happening")
        pass
 
