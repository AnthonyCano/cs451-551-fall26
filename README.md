# CS 451/551: Database Processing, Fall 2026, University of Oregon

Acknowledgments and Thanks to Prof. Mohammad Sadoghi (UC Davis)

| Milestone | Topic | Status |
|---|---|---|
| **Assignment 1** | Single-threaded in-memory L-Store | Completed (Full Score) |
| **Assignment 2** | Durability, Bufferpool, Background Merge & Secondary Indexing | In Progress (Due Nov 12, 2026) |

---

## Assignment 1: In-Memory L-Store (Completed — Full Score)

### Design Decisions

We use a library metaphor: original **books** are never edited; changes go on **errata slips** in a back room.

| L-Store | Library |
|---|---|
| Base record | Book on the main shelf (never rewritten) |
| Tail record | Errata slip in the back room |
| RID | Call number (permanent) |
| Indirection | Sticky note: "latest slip is #X" |
| Schema encoding | Checkboxes: which columns have been corrected |
| Page directory | Floor map: call number → location |
| Primary index | Card catalog: key → call number |
| Page range | One wing with its own back room |
| Delete | WITHDRAWN stamp |
| Merge | Reprint the book with errata folded in |

#### Pages and page ranges
- Page = 4096 bytes, 64-bit ints → **512 records per page**.
- Page range = **16 base pages** → 8,192 base records per range.
- Each range has its own tail pages, starting with one; a new one is allocated when the current one fills.

#### RIDs
- **One global counter starting at 1**, shared by base and tail records.
- **0 is reserved** as the deleted marker.
- Page directory is a dict: `rid → (range_idx, is_tail, page_idx, slot)`.

#### Tail records: cumulative
- Each tail record holds the **latest value of every column**.
- Update: read the latest version, overwrite changed columns (`None` = unchanged), append as a new tail record.
- Select: base → indirection → latest tail. One hop, no lineage walk.

#### Indirection
- Base record with no updates points to **its own RID**.
- First tail record points back to the **base RID**; each later tail points to the previous tail.

#### Schema encoding
- Base: bit set for every column ever updated.
- Tail: bits set for the columns that update changed.

#### NULL
- `NULL = -(2**63)` (min int64). Reserved placeholder; unused in practice since tail records are cumulative.

#### Delete
- Set the RID column to **0** on the base record and all its tail records.
- Remove the key from the primary index.
- Physical removal happens during merge.

#### Index
- Primary key index is a dict: `key → base RID`.
- `sum` iterates `range(start, end + 1)` and skips missing keys.

### Assignment 1 Roles

#### Claiming a role
1. Pull the latest `README.md`.
2. Replace `_unclaimed_` in the **Claimed by** column of the role you want with your name (and GitHub handle).
3. Commit and push right away (`git commit -m "Claim Role X"`) so nobody double-claims. If the push is rejected, someone claimed first: pull and pick another role.
4. To release a role, set it back to `_unclaimed_` and push.

| Role | Area | Claimed by |
|---|---|---|
| **A** | Pages (the shelves) + testing | Madeline Luu |
| **B** | Table (the floor map) | Vikram T |
| **C** | Point queries (the librarian) | Rayna P |
| **D** | Index, DB, range/version queries (the card catalog) | Anthony C |

#### Person A, Madeline Luu: Pages (the shelves)
**Files:** `page.py`, new `config.py`
- `Page`: `has_capacity()`, `write(value)`, `read(slot)`, `update(slot, value)` (8-byte ints, 512 per page).
- `PageRange`: 16 base pages plus a growing list of tail pages, with one physical `Page` per column (4 metadata columns + user columns).
- `config.py`: `PAGE_SIZE`, `RECORDS_PER_PAGE`, `BASE_PAGES_PER_RANGE`, `NULL`, `DELETED_RID`.
- **Also owns testing.** This is the smallest coding piece and finishes first. After that, A gets `__main__.py`, `m1_tester.py`, and `exam_tester_m1.py` running and reports failures to whoever owns the broken part.

#### Person B, Vikram T: Table (the floor map)
**Files:** `table.py`
- RID counter starting at 1, and `page_directory[rid] = (range_idx, is_tail, page_idx, slot)`.
- The storage API everyone else calls:
  - `insert_base(columns)` → `rid`
  - `append_tail(columns, indirection, schema)` → `rid`
  - `read_record(rid)` → all columns incl. metadata
  - `write_meta(rid, column, value)`
  - `get_version(base_rid, relative_version)` → record; follows the indirection chain (shared by C and D)

#### Person C, Rayna P: Point queries (the librarian)
**Files:** `query.py` (top half)
- `insert`, `select`, `update`, `delete`, `increment`.
- `update` is cumulative: read the latest version, apply the non-`None` values, append a tail record.
- `delete` sets RID = 0 on the book and all its slips, and removes the key from the index.
- Return `False` on failure (missing key, duplicate key) and never crash.

#### Person D, Anthony C: Index, DB, range/version queries (the card catalog)
**Files:** `index.py`, `db.py`, `query.py` (bottom half)
- `Index`: `locate`, `locate_range` on the primary-key dict; `create_index` / `drop_index` are optional.
- `Database`: `create_table`, `drop_table`, `get_table`.
- `sum`, `sum_version`, `select_version`, all built on B's `get_version`.

### Part B Notes: Table (the floor map)

Implemented in `lstore/table.py`. These notes explain how we thought about it, in the special-collections metaphor: books are never edited, corrections go on errata slips in the back room.

#### The layout
- Each **wing** is a `PageRange`. Every shelf in it has 4 admin columns (sticky note, call number, date, checkboxes) in front of the real columns, so a wing is built with `4 + num_columns` columns.
- The **floor map** is `page_directory[rid] = (wing, is_slip, page, slot)`. Any call number leads straight to its shelf.
- The librarian keeps one stamp, `next_rid`, starting at 1. Books and slips share it, and 0 is reserved for WITHDRAWN.

#### The storage API (what C and D call)
| Method | Library meaning |
|---|---|
| `insert_base(columns)` | A new book arrives. Its sticky note points at itself (no slips yet) and its checkboxes are empty. Returns the call number. |
| `append_tail(columns, indirection, schema)` | File an errata slip in the back room. Returns the slip's call number. |
| `read_record(rid)` | Walk the floor map to the shelf and copy out the whole row, admin columns included. Unknown call number returns `None`. |
| `write_meta(rid, column, value)` | Scribble on one admin cell: move a sticky note, tick a checkbox, stamp WITHDRAWN. |
| `get_version(base_rid, relative_version)` | Fetch the book as it read N corrections ago. |

#### Decisions and why
- **Slips live in their book's wing.** `append_tail` takes the `indirection` argument (the book for the first slip, the previous slip after that) and uses the floor map to find which wing it belongs to. A wing then holds a book and all its corrections, so a future merge only has to look in one back room.
- **`append_tail` does not touch the book.** B only files the slip. C moves the sticky note (and ticks the book's checkboxes) with `write_meta` once the slip exists. That keeps the storage layer simple and leaves update ordering to the query layer.
- **`get_version` reads one slip, not a chain.** Slips are cumulative, so the slip we land on already has every column. We follow the sticky note to the newest slip, then step back along the "previous slip was #Y" notes. `0` is the latest, `-1` one correction back, and so on.
- **Asking for too old a version returns the original book.** If the walk runs into the book before it runs out of steps, the book is the oldest version that exists. A never-corrected book returns itself.
- **Withdrawn books are still found.** WITHDRAWN means RID column = 0, but the floor map keeps the original call number. `read_record` and `get_version` still return the row, so C and D must check the RID column.

#### Notes for C and D
- Rows from `read_record` and `get_version` have the 4 admin columns first, so user columns start at index 4.
- Importing the column constants (`INDIRECTION_COLUMN`, `RID_COLUMN`, etc.) from `lstore.table` is safer than hard-coding indexes.

#### How it was checked
- A scratch script (not in the repo) inserted 8,200 books to cross a page boundary and a wing boundary, then filed two cumulative slips on one book. It confirmed the version walk for `0`, `-1`, `-2` and `-5`, that slips land in their book's wing, and that an unknown call number returns `None`.
- `m1_tester.py` was not run for B, because it needs C's and D's parts.

---

## Assignment 2: Durability, Bufferpool, Merge & Secondary Indexing

Assignment 2 extends our L-Store with three major capabilities:
1. **Durability & Bufferpool Extension:** Persist all table data and metadata to disk (without `pickle`) across `db.open(path)` and `db.close()` calls, and manage in-memory pages in a fixed-size bufferpool with LRU eviction, dirty-page tracking, and page pinning/unpinning.
2. **Data Reorganization (Contention-Free Merge):** Periodically consolidate tail records into copies of base pages on a background thread using a **BaseRID** metadata column and per-page **Tail-Page Sequence Numbers (TPS)**, swapping updated base pages into the page directory on the foreground thread.
3. **Secondary Indexing:** Support `create_index` and `drop_index` on any column, accelerate `select` / `select_version` queries on arbitrary search columns, and maintain all active indexes across `insert`, `update`, and `delete` operations.

### Extended Library Metaphor & Design Decisions

| L-Store (Assignment 2) | Library |
|---|---|
| Disk storage (`open` / `close`) | Underground vault (permanent archive across closing/reopening) |
| Bufferpool (`BUFFERPOOL_SIZE = 256`) | Reading room desks (limited capacity; cold shelves return to the vault) |
| Pin / Unpin | "In Use" card on a desk (cannot be cleared while a patron is reading) |
| Dirty page | Shelf with new markings that must be written back to the vault before eviction |
| BaseRID column (metadata col 4) | Original book call number printed on every errata slip |
| TPS (Tail-Page Sequence Number) | Edition watermark on a reprinted shelf ("includes all slips up to #T") |
| Secondary index | Subject/author catalog: column value → set of book call numbers |

#### 1. Durability & Bufferpool (`lstore/bufferpool.py`, `lstore/page.py`, `lstore/db.py`)
- **Binary Page Format (No `pickle`):** Each `Page` serializes to `4104` bytes via `Page.to_bytes()` and `Page.from_bytes()`: the raw `4096`-byte `bytearray` of 64-bit little-endian integers (`<q`) followed by an 8-byte trailer storing `num_records`.
- **Disk Layout:** Pages live under `<db_path>/<table>/r<range_idx>_<b|t><page_idx>_c<column>.page`. Writes use an atomic `.tmp` write + `os.replace` so crashes cannot leave torn pages.
- **Table Metadata (`meta.json`):** `Database.close()` flushes all dirty bufferpool frames and writes `<db_path>/<table>/meta.json` containing `name`, `num_columns`, `key`, `next_rid`, per-range slot/page counters, and `page_directory`. `Database.open(path)` reloads table metadata and pages from disk and rebuilds the primary key index.
- **Bufferpool & LRU Eviction:** `BufferPool` holds up to `BUFFERPOOL_SIZE = 256` frames in an `OrderedDict` keyed by `(table, range_idx, is_tail, page_idx, column)`.
  - `get_page(key)` moves hits to the MRU end; on a miss, if at capacity, `_evict()` scans from the LRU end for the first frame with `pins == 0`, flushes it if marked in `dirty`, and removes it.
  - `pin(key)` / `unpin(key)` track active access counts; `mark_dirty(key)` records modified pages; `flush_all()` writes all dirty pages to disk.
  - Protected by a re-entrant lock (`threading.RLock`) so the background merge thread and main thread can safely share the pool.

#### 2. Data Reorganization: Contention-Free Merge (`lstore/table.py`, `lstore/config.py`)
- **BaseRID Metadata Column:** Tail records add a 5th metadata column (`BASE_RID_COLUMN = 4`, raising `NUM_METADATA_COLUMNS` from `4` to `5`) storing the RID of the original base record so merge can map any tail record directly back to its base slot.
- **Tail-Page Sequence Number (TPS):** Because our RID counter (`next_rid`) increases monotonically starting from `1`, each base page's TPS starts at `0` and tracks the highest tail RID consolidated into that page.
  - When reading a base record, if its `INDIRECTION_COLUMN` is `<= TPS`, the merged base page already reflects the latest update and the tail lookup is skipped.
- **Background Merge Process:**
  - Triggered once a `PageRange` accumulates `MERGE_THRESHOLD = 512` tail records.
  - A background thread copies the range's base pages outside the normal bufferpool, scans the tail records in reverse order (newest to oldest), applies only the first (latest) update seen for each `BaseRID` (since tail records are cumulative), and updates the merged copy's TPS.
  - Deleted records (`RID == 0`) remain in place during merge so base RIDs and slots never shift.
  - Swapping the page directory entries to point to the newly consolidated base pages is performed on the foreground thread under a lock so active reads and writes remain contention-free.

#### 3. Secondary Indexing & Query Processing (`lstore/index.py`, `lstore/query.py`)
- **Multi-Column Index Structure:** `Index.indices` holds an array of `num_columns` slots (`None` when unindexed, or a dict `value -> set(base_rids)` when indexed). The primary key column is indexed by default.
- **`create_index(col)` & `drop_index(col)`:** `create_index` iterates all live base records (`not is_tail` and `RID != DELETED_RID`), fetches their latest version via `table.get_version(rid, 0)`, and populates `value -> set(base_rids)`. `drop_index` resets a non-key column's index to `None`. Indexes live in memory outside the bufferpool and are rebuilt on `db.open()`.
- **Point & Version Queries on Any Column:** `select` and `select_version` accept any `search_key_index`. When that column is indexed, `_matching_base_rids` uses `index.locate(column, value)`; otherwise it scans all live base records.
- **Index Upkeep & Page Pinning:** `insert`, `update`, and `delete` automatically update every currently indexed column (`_indexed_columns()`). Queries wrap record accesses in a `with self._pinned(rid):` context manager (`try ... finally`) so pages remain pinned during execution and are always unpinned even if an error occurs.

### Assignment 2 Roles & Division of Work

Each team member keeps the same architectural layer they owned in Assignment 1:

| Role | Person | Assignment 1 Area | Assignment 2 Area |
|---|---|---|---|
| **A** | Madeline Luu | Pages + testing | Bufferpool + binary disk I/O + testing |
| **B** | Vikram T | Table (page directory, storage API) | Bufferpool integration + BaseRID + background merge & TPS |
| **C** | Rayna P | Point queries | Queries on any column, all-index upkeep, `BaseRID` passing, pin/unpin |
| **D** | Anthony C | Index, DB, range/version queries | Secondary indexes (`value → set(rids)`) + `open`/`close` persistence |

#### Person A, Madeline Luu: Bufferpool and Disk (the stacks)
**Files:** `lstore/page.py`, `lstore/bufferpool.py`, `lstore/config.py`
- `Page.to_bytes()` / `Page.from_bytes()` serializing the 4096-byte `bytearray` plus 8-byte `num_records` trailer (no `pickle`).
- Disk layout under `<path>/<table>/r<range>_<b|t><page>_c<col>.page` with atomic `.tmp` + `os.replace` writes.
- `BufferPool(path, capacity)`: `get_page(key)`, `pin(key)` / `unpin(key)`, `mark_dirty(key)`, `flush_all()`, and LRU eviction via `collections.OrderedDict` (skipping pinned pages and flushing dirty pages before eviction).
- `config.py`: `BUFFERPOOL_SIZE = 256`, `MERGE_THRESHOLD = 512`, `BASE_RID_COLUMN = 4`.
- **Testing:** Runs `__main__.py`, `m1_tester.py`, `m2_tester_part1.py`, `m2_tester_part2.py`, `exam_tester_m2_part1.py`, and `exam_tester_m2_part2.py`.

#### Person B, Vikram T: Merge & Bufferpool-Backed Table (reprinting the books)
**Files:** `lstore/table.py`, `lstore/config.py`
- Add `BASE_RID_COLUMN` metadata column (`NUM_METADATA_COLUMNS = 5`) across `insert_base` and `append_tail`.
- Route `Table` / `PageRange` page reads and writes through `BufferPool` with `pin(rid)`, `unpin(rid)`, and `mark_dirty`.
- Track per-base-page **TPS** (starting at `0` for monotonically increasing RIDs) and short-circuit `get_version` / `read_record` to read the base page directly when `indirection <= TPS`.
- Implement background `__merge(range_idx)` triggered at `MERGE_THRESHOLD` tail records, consolidating the latest cumulative tail records into copied base pages in a background thread and swapping the page directory on the foreground thread.

#### Person C, Rayna P: Queries on Any Column & Index/Pin Upkeep (the librarian)
**Files:** `lstore/query.py`
- `select` and `select_version` on any column (`search_key_index`), using `index.locate` when indexed and falling back to a full base-record scan when unindexed.
- `insert`, `update`, and `delete` maintain **all** active secondary indexes (`_indexed_columns()`), removing old values and inserting new values on update.
- Pass `base_rid` to `table.append_tail` when `NUM_METADATA_COLUMNS > BASE_RID_COLUMN`.
- Wrap all record accesses in `with self._pinned(rid):` so pages are pinned before read/write and guaranteed to unpin in `finally`.
- Validate integer bounds (`-(2**63) < v < 2**63`, rejecting booleans and `None` on insert) and return `False` cleanly on any invalid operation.

#### Person D, Anthony C: Secondary Indexes & Database Persistence (the card catalog)
**Files:** `lstore/index.py`, `lstore/db.py`, `lstore/query.py`
- Updated `Index` to store `value -> set(base_rids)` per column so non-unique secondary columns map to all matching records.
- Implemented `create_index(column_number)`, `drop_index(column_number)`, `add(column, value, rid)`, `remove(column, value, rid)`, `locate`, and `locate_range`.
- Implemented `Database.open(path)` and `Database.close()` in `lstore/db.py` to persist non-empty pages as raw binary files and table metadata in `meta.json`, rebuilding the table structure, page directory, and primary key index upon restart.
- Updated `sum`, `sum_version`, and `increment` to work seamlessly with multi-RID index lookups.


### Assignment 2 Part B Notes: Table, BaseRID, TPS & Merge (Vikram)

Implemented in `lstore/table.py` (plus the pool hookup in `page.py` and `db.py`).
- **BaseRID:** `NUM_METADATA_COLUMNS = 5`. Tail records store their base RID; a base record stores its own.
- **Bufferpool:** `PageRange` fetches pages by key from the pool when one is set (otherwise plain lists, so `m1_tester` still works without `db.open`). Writes hold the pool lock from `get_page` to `mark_dirty`, so a page can't be evicted mid-write. `Table.pin/unpin(rid)` pin every column page of a record.
- **TPS:** `table.tps[(range, base_page)]` = highest tail RID merged into that page. `get_version(rid, 0)` skips the tail when the base's indirection is `<= TPS`. Saved in `meta.json`.
- **Merge:** after `MERGE_THRESHOLD` new tails in a range, a thread copies the base user-column pages, applies the newest tail per BaseRID, and queues the result. The foreground thread installs it (`_apply_merges`) at the start of the next table call. `Database.close()` waits for pending merges.
- **Before-copy:** merging overwrites the original values, so the first merge of a book adds a schema-0 tail (BaseRID 0) holding the original as its oldest slip. Old-version reads still work.
- **Checked:** `m1_tester`, `__main__`, `m2_tester_part1/2`, `exam_tester_m1`, `exam_tester_m2_part1/2` all clean (0 errors); scratch test with a 40-frame pool forced eviction and merges.
