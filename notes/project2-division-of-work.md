# Project 2: Implementation and Division of Work Plan

## Overview

Assignment 2 makes our L-Store durable, adds a background merge, and adds secondary indexes. It is due **November 12, 2026 at 11:59pm**, and `main.py`, `m2_tester_part1.py` and `m2_tester_part2.py` must pass.

1. **Durability and bufferpool:** all data lives on disk. A fixed-size bufferpool, sized at `open()`, holds pages in memory with pin counts, dirty tracking and an eviction policy (LRU). `close()` flushes every dirty page. No `pickle`.
2. **Merge:** a background thread folds tail records into copies of base pages. It tracks the TPS (last merged tail RID) per base page and uses a new BaseRID column. The page directory swap happens on the main thread.
3. **Indexing:** `create_index` / `drop_index` on any column. `select` can search on any column and return several rows. Indexes are rebuilt after restart, not saved to disk.

## Team and roles

Each person keeps the same layer they built in Assignment 1, so each person extends code they already know.

| Role | Person | Assignment 1 area | Assignment 2 area |
|---|---|---|---|
| A | Maddie | Pages + testing | Bufferpool + disk I/O + testing |
| B | Vik | Table (page directory, storage API) | Background merge + TPS |
| C | Rayna | Point queries | Queries on any column, index upkeep, pin/unpin |
| D | Anthony | Index, DB, range/version queries | Secondary indexes + `open`/`close` persistence |

## Division of work

### Person A, Maddie: Bufferpool and disk (the stacks)
**Files:** `page.py`, new `bufferpool.py`, `config.py`

- [ ] `Page.to_bytes()` / `Page.from_bytes()` so a page is saved as its raw 4096-byte `bytearray` (no pickle).
- [ ] Disk layout: one file per table per page range per column (e.g. `CS451/<table>/r<range>_<b|t><page>_c<col>.page`).
- [ ] `BufferPool(capacity)`: `get_page(key)` loads from disk on a miss, `pin(key)` / `unpin(key)`, `mark_dirty(key)`, `flush_all()`.
- [ ] Eviction: LRU via `collections.OrderedDict`. Skip pages with pin > 0 and write dirty pages back before evicting.
- [ ] `config.py`: `BUFFERPOOL_SIZE`, `MERGE_THRESHOLD`, and the BaseRID column index.
- [ ] **Testing:** finishes first again, then runs `m2_tester_part1.py`, `m2_tester_part2.py` and `exam_tester_m2_part1/2.py` and reports failures to the owner of the broken part.

### Person B, Vik: Merge (reprinting the books)
**Files:** `table.py`

- [ ] Add a **BaseRID** metadata column to tail records (`NUM_METADATA_COLUMNS` goes from 4 to 5).
- [ ] Route `PageRange` page access through A's bufferpool instead of in-memory lists.
- [ ] Keep a TPS per base page, starting at 0 since our RIDs increase.
- [ ] `__merge(range_idx)`: copy the range's base pages outside the bufferpool. Walk its tail records newest to oldest and apply only the latest version per BaseRID. Set the TPS.
- [ ] Run merge in a `threading.Thread` once a range has `MERGE_THRESHOLD` tail records. The swap of the page directory to the new base pages happens on the main thread under a `threading.Lock`.
- [ ] `get_version` / `read_record`: if a base record's indirection is at or below the page TPS, read the merged base page and skip the tail.
- [ ] Deleted records stay in place during merge (no RID changes).

### Person C, Rayna: Queries (the librarian)
**Files:** `query.py` (top half)

- [ ] `select` / `select_version` on any column: use `index.locate` when the column is indexed, else scan all base records. Return every match, not just one.
- [ ] `insert`, `update`, `delete`: keep **every** existing index current, not just the primary key (remove old value, add new value on update).
- [ ] Write the BaseRID on every tail record that `update` creates.
- [ ] Pin pages before a read or write and unpin them after, through B's table API.
- [ ] Keep returning `False` on bad input and never crash.

### Person D, Anthony: Indexes and persistence (the card catalog)
**Files:** `index.py`, `db.py`, `query.py` (bottom half)

- [ ] Change index maps to `value => set(rids)` so non-unique columns work. `locate` / `locate_range` return all matches.
- [ ] `create_index(column)`: scan all base records, read the latest version, and fill the map. `drop_index(column)`: set it back to `None`.
- [ ] `add(column, value, rid)` / `remove(column, value, rid)` helpers for C to call.
- [ ] `Database.open(path)`: make the folder if missing, read each table's metadata, rebuild the page directory, and create the primary key index.
- [ ] `Database.close()`: flush the bufferpool and write metadata per table (name, num_columns, key, next_rid, page directory, range and tail-page counts, TPS) as JSON or `struct`.
- [ ] `sum`, `sum_version`, `select_version`: work with the new index return shape and the merged base pages.

## Integration and timeline

The work has to land in dependency order: A's bufferpool first, then B's table changes, then C and D build on the table. D's index changes can start right away.

1. **Nov 1:** A ships `Page` serialization and the `BufferPool` API. D ships the new index shape (`value => set(rids)`) plus `add`/`remove`.
2. **Nov 4:** B moves `PageRange` onto the bufferpool and adds the BaseRID column. C switches queries to all-index upkeep.
3. **Nov 7:** D finishes `open`/`close`. B finishes the background merge and TPS. A runs the `m2` testers end to end.
4. **Nov 10:** Bug fixes from A's test reports, then a README update with Part notes for each role.
5. **Nov 12:** One person submits on Canvas.

Open questions:

- [ ] Eviction granularity: one page per column (simplest) or a whole page range?
- [ ] Merge trigger: a tail-record count per range, or a timer?
- [ ] Who submits on Canvas?
