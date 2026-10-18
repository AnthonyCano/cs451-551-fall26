# CS 451/551: Database Processing, Fall 2026, University of Oregon

Acknowledgments and Thanks to Prof. Mohammad Sadoghi (UC Davis)

## Design Decisions

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

### Pages and page ranges
- Page = 4096 bytes, 64-bit ints → **512 records per page**.
- Page range = **16 base pages** → 8,192 base records per range.
- Each range has its own tail pages, starting with one; a new one is allocated when the current one fills.

### RIDs
- **One global counter starting at 1**, shared by base and tail records.
- **0 is reserved** as the deleted marker.
- Page directory is a dict: `rid → (range_idx, is_tail, page_idx, slot)`.

### Tail records: cumulative
- Each tail record holds the **latest value of every column**.
- Update: read the latest version, overwrite changed columns (`None` = unchanged), append as a new tail record.
- Select: base → indirection → latest tail. One hop, no lineage walk.

### Indirection
- Base record with no updates points to **its own RID**.
- First tail record points back to the **base RID**; each later tail points to the previous tail.

### Schema encoding
- Base: bit set for every column ever updated.
- Tail: bits set for the columns that update changed.

### NULL
- `NULL = -(2**63)` (min int64). Reserved placeholder; unused in practice since tail records are cumulative.

### Delete
- Set the RID column to **0** on the base record and all its tail records.
- Remove the key from the primary index.
- Physical removal happens during merge.

### Index
- Primary key index is a dict: `key → base RID`.
- `sum` iterates `range(start, end + 1)` and skips missing keys.

## Roles

### Claiming a role
1. Pull the latest `README.md`.
2. Replace `_unclaimed_` in the **Claimed by** column of the role you want with your name (and GitHub handle).
3. Commit and push right away (`git commit -m "Claim Role X"`) so nobody double-claims. If the push is rejected, someone claimed first: pull and pick another role.
4. To release a role, set it back to `_unclaimed_` and push.

| Role | Area | Claimed by |
|---|---|---|
| **A** | Pages (the shelves) + testing | _unclaimed_ |
| **B** | Table (the floor map) | _unclaimed_ |
| **C** | Point queries (the librarian) | _unclaimed_ |
| **D** | Index, DB, range/version queries (the card catalog) | _unclaimed_ |

### Person A, Madeline Luu: Pages (the shelves)
**Files:** `page.py`, new `config.py`
- `Page`: `has_capacity()`, `write(value)`, `read(slot)`, `update(slot, value)` (8-byte ints, 512 per page).
- `PageRange`: 16 base pages plus a growing list of tail pages, with one physical `Page` per column (4 metadata columns + user columns).
- `config.py`: `PAGE_SIZE`, `RECORDS_PER_PAGE`, `BASE_PAGES_PER_RANGE`, `NULL`, `DELETED_RID`.
- **Also owns testing.** This is the smallest coding piece and finishes first. After that, A gets `__main__.py`, `m1_tester.py`, and `exam_tester_m1.py` running and reports failures to whoever owns the broken part.

### Person B, Vikram T: Table (the floor map)
**Files:** `table.py`
- RID counter starting at 1, and `page_directory[rid] = (range_idx, is_tail, page_idx, slot)`.
- The storage API everyone else calls:
  - `insert_base(columns)` → `rid`
  - `append_tail(columns, indirection, schema)` → `rid`
  - `read_record(rid)` → all columns incl. metadata
  - `write_meta(rid, column, value)`
  - `get_version(base_rid, relative_version)` → record; follows the indirection chain (shared by C and D)

### Person C, Rayna P: Point queries (the librarian)
**Files:** `query.py` (top half)
- `insert`, `select`, `update`, `delete`, `increment`.
- `update` is cumulative: read the latest version, apply the non-`None` values, append a tail record.
- `delete` sets RID = 0 on the book and all its slips, and removes the key from the index.
- Return `False` on failure (missing key, duplicate key) and never crash.

### Person D, Anthony C: Index, DB, range/version queries (the card catalog)
**Files:** `index.py`, `db.py`, `query.py` (bottom half)
- `Index`: `locate`, `locate_range` on the primary-key dict; `create_index` / `drop_index` are optional.
- `Database`: `create_table`, `drop_table`, `get_table`.
- `sum`, `sum_version`, `select_version`, all built on B's `get_version`.
