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
