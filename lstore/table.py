import threading
from time import time

from lstore.index import Index
from lstore.page import Page, PageRange
from lstore.config import (
    NUM_METADATA_COLUMNS,
    BASE_RID_COLUMN,
    MERGE_THRESHOLD,
    RECORDS_PER_PAGE,
    DELETED_RID,
)

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
        # Set by Database when the table is opened. None = plain in-memory pages
        self.bufferpool = None
        # TPS (edition watermark) per base page: (wing, page) -> highest tail RID
        # already folded into that base page. Missing means 0 (never merged)
        self.tps = {}
        # Finished merges wait here until the foreground thread installs them
        self.merge_lock = threading.Lock()
        self.merge_results = []
        self.merge_threads = []
        self.index = Index(self)

    # Stamp the next call number and hand it out
    def _new_rid(self):
        rid = self.next_rid
        self.next_rid += 1
        return rid

    # Every shelf has 5 admin columns (sticky note, call number, date, checkboxes,
    # original book's call number) in front of the real columns
    def _physical_columns(self):
        return NUM_METADATA_COLUMNS + self.num_columns

    def _new_wing(self):
        wing_idx = len(self.page_ranges)
        self.page_ranges.append(PageRange(
            self._physical_columns(), self.bufferpool, self.name, wing_idx))
        return wing_idx

    # Shelve a new book in the newest wing. If that wing is full of books,
    # open a fresh wing (with its own back room) and shelve it there.
    # Returns (wing_idx, page_idx, slot)
    def _shelve_base(self, row):
        if self.page_ranges:
            wing_idx = len(self.page_ranges) - 1
            spot = self.page_ranges[wing_idx].append_base(row)
            if spot is not None:
                return (wing_idx,) + spot
        wing_idx = self._new_wing()
        return (wing_idx,) + self.page_ranges[wing_idx].append_base(row)

    # New book arrives. Its sticky note points at itself (no slips yet), its
    # checkboxes are empty, and its BaseRID is itself. Returns the call number.
    def insert_base(self, columns):
        self._apply_merges()
        rid = self._new_rid()
        row = [rid, rid, int(time()), 0, rid] + list(columns)
        wing_idx, page_idx, slot = self._shelve_base(row)
        self.page_directory[rid] = (wing_idx, False, page_idx, slot)
        return rid

    # File an errata slip in the back room of the SAME wing as its book.
    # `indirection` is whatever the slip points back to: the book itself for the
    # first slip, otherwise the previous slip. That tells us which wing to use.
    # `base_rid` is stamped on the slip so merge can find its book directly.
    # Note: we do not touch the book's sticky note here. Whoever files the slip
    # (query.update) moves it with write_meta once the slip exists.
    def append_tail(self, columns, indirection, schema, base_rid):
        self._apply_merges()
        rid = self._new_rid()
        wing_idx = self.page_directory[indirection][0]
        row = [indirection, rid, int(time()), schema, base_rid] + list(columns)
        page_idx, slot = self.page_ranges[wing_idx].append_tail(row)
        self.page_directory[rid] = (wing_idx, True, page_idx, slot)
        self._maybe_merge(wing_idx)
        return rid

    # Put the "in use" card on every column page of a record so the bufferpool
    # can't evict them. Does nothing when there is no bufferpool.
    def pin(self, rid):
        if self.bufferpool is None or rid not in self.page_directory:
            return
        for key in self._page_keys(rid):
            self.bufferpool.get_page(key)  # make sure it is resident before pinning
            self.bufferpool.pin(key)

    def unpin(self, rid):
        if self.bufferpool is None or rid not in self.page_directory:
            return
        for key in self._page_keys(rid):
            self.bufferpool.unpin(key)

    # Bufferpool keys of every column page that holds this record
    def _page_keys(self, rid):
        wing_idx, is_tail, page_idx, _ = self.page_directory[rid]
        return [(self.name, wing_idx, is_tail, page_idx, column)
                for column in range(self._physical_columns())]

    # Walk to the right wing/shelf via the floor map and copy out the whole row
    # (admin columns included). Unknown call number -> None.
    # Note: this is the raw row. For a merged book the TPS shortcut lives in get_version
    def read_record(self, rid):
        self._apply_merges()
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
        if relative_version == 0 and current <= self._tps_of(base_rid):
            return book  # the reprinted shelf already includes the newest slip

        steps_back = -relative_version
        row = self.read_record(current)
        while steps_back > 0:
            previous = row[INDIRECTION_COLUMN]
            if not self.page_directory[previous][1]:
                # ran out of slips. A before-copy (schema 0) holds the original row
                # of a merged book; otherwise the book itself is still the original
                return row if row[SCHEMA_ENCODING_COLUMN] == 0 else book
            current = previous
            row = self.read_record(current)
            steps_back -= 1
        return row

    def _tps_of(self, base_rid):
        wing_idx, _, page_idx, _ = self.page_directory[base_rid]
        return self.tps.get((wing_idx, page_idx), 0)

    # ---- merge (reprinting the books) ----

    # Start a background merge once a wing has piled up enough slips since the last one
    def _maybe_merge(self, wing_idx):
        wing = self.page_ranges[wing_idx]
        if wing.merging or wing.tail_count() - wing.merged_tails < MERGE_THRESHOLD:
            return
        wing.merging = True
        # snapshot taken here on the foreground thread so it is consistent:
        # everything up to these counts is fully written
        snapshot = (wing.tail_count(), wing.base_count(), self.next_rid - 1)
        thread = threading.Thread(target=self.__merge, args=(wing_idx, snapshot), daemon=True)
        self.merge_threads.append(thread)
        thread.start()

    """
    Background thread. Copies the wing's base pages (user columns only; the admin
    columns stay live because queries keep moving them), then scans the slips
    newest to oldest. Slips are cumulative, so the first slip seen for a book is
    all we need. The copies are queued; the foreground thread swaps them in.
    """
    def __merge(self, wing_idx, snapshot):
        tail_count, base_count, max_rid = snapshot
        wing = self.page_ranges[wing_idx]
        user_columns = range(NUM_METADATA_COLUMNS, self._physical_columns())

        # slots of each base page that were fully written when the snapshot was taken
        base_pages = {}
        for page_idx in range((base_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE):
            used = min(RECORDS_PER_PAGE, base_count - page_idx * RECORDS_PER_PAGE)
            copies = {}
            for column in user_columns:
                live = wing.page(False, page_idx, column)
                copies[column] = Page.from_bytes(live.to_bytes())
            base_pages[page_idx] = (used, copies)

        seen = set()
        originals = {}  # base_rid -> row values as the book looked before any merge
        needs_copy = []  # (oldest slip rid, base_rid): books with no before-copy yet
        for position in range(tail_count - 1, -1, -1):
            page_idx, slot = divmod(position, RECORDS_PER_PAGE)
            if wing.read_cell(True, page_idx, slot, RID_COLUMN) == DELETED_RID:
                continue  # slip of a withdrawn book
            base_rid = wing.read_cell(True, page_idx, slot, BASE_RID_COLUMN)
            if base_rid == 0:
                continue  # a before-copy, never the latest version
            _, _, base_page, base_slot = self.page_directory[base_rid]
            if wing.read_cell(True, page_idx, slot, INDIRECTION_COLUMN) == base_rid:
                # oldest slip of this book, and nothing sits between it and the book
                needs_copy.append((wing.read_cell(True, page_idx, slot, RID_COLUMN), base_rid))
            if base_rid in seen:
                continue  # an older slip, a newer one already won
            seen.add(base_rid)
            if wing.read_cell(False, base_page, base_slot, RID_COLUMN) == DELETED_RID:
                continue  # deleted books stay exactly where they are
            originals[base_rid] = [base_pages[base_page][1][c].read(base_slot) for c in user_columns]
            for column in user_columns:
                value = wing.read_cell(True, page_idx, slot, column)
                base_pages[base_page][1][column].update(base_slot, value)

        with self.merge_lock:
            self.merge_results.append((wing_idx, base_pages, max_rid, tail_count, needs_copy, originals))

    # Foreground thread only: install finished merges.
    # shortcut: instead of repointing the page directory at brand new pages we copy
    # the merged bytes into the live page object, which has the same effect (the
    # directory entries never change) and keeps the bufferpool keys stable.
    def _apply_merges(self):
        if not self.merge_results:
            return
        with self.merge_lock:
            results, self.merge_results = self.merge_results, []
        for wing_idx, base_pages, max_rid, tail_count, needs_copy, originals in results:
            wing = self.page_ranges[wing_idx]
            with wing.lock():
                for page_idx, (used, copies) in base_pages.items():
                    for column, merged in copies.items():
                        live = wing.page(False, page_idx, column)
                        # only the slots that existed at snapshot time; newer inserts stay
                        live.data[:used * 8] = merged.data[:used * 8]
                        wing.mark_dirty(False, page_idx, column)
                    self.tps[(wing_idx, page_idx)] = max_rid
            # The merge overwrote the originals, so keep a before-copy (schema 0,
            # BaseRID 0 so a later merge ignores it) as the oldest slip of each book.
            copies = 0
            for oldest_rid, base_rid in needs_copy:
                if base_rid in originals:
                    before = self.append_tail(originals[base_rid], base_rid, 0, 0)
                    self.write_meta(oldest_rid, INDIRECTION_COLUMN, before)
                    copies += 1
            wing.merged_tails = tail_count + copies
            wing.merging = False

    # Wait for running merges and install them (used by Database.close)
    def finish_merges(self):
        for thread in self.merge_threads:
            thread.join()
        self.merge_threads = []
        self._apply_merges()
