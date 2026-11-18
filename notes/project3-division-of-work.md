# Project 3: Division of Work

**Due:** November 30, 2026 at 11:59pm. `main.py`, `m3_tester_part_1.py` and `m3_tester_part_2.py` must pass. Run part 1 before part 2, because part 2 reopens the data part 1 saved.

**Goal:** add transactions (all of their queries commit, or they abort and every change is rolled back) and multithreading. Concurrency control is strict 2PL with **no wait**: if a lock is busy, the transaction aborts, rolls back, and the worker retries it until it commits.

Everyone keeps the files they worked on in A1/A2 (from git history).

---

## Shared interfaces (agree on these by Nov 18)

```python
# lstore/lock_manager.py  (Anthony) -- done
from lstore.lock_manager import lock, log_undo

lock(table, key, mode) -> bool   # 'S' | 'X'; no wait. Returns True outside a transaction.
                                 # On False it has already set txn.lock_failed: just return False.
log_undo(fn)                     # does nothing outside a transaction; fn() runs in reverse order on abort

# Table undo helpers  (Vik)
table.undo_insert(base_rid)
table.undo_update(base_rid, tail_rid, old_indirection, old_schema)
table.undo_delete(base_rid, rids)
```

Lock key = `(table.name, primary key value)`. There is one global lock manager, so there is no `table.lock_manager`. A transaction may ask again for a lock it already holds, and S upgrades to X when it is the only holder.

---

## Maddie: Workers and testing

**Files you've been working on:** `page.py`, `bufferpool.py`, `config.py`
**A3 files:** `transaction_worker.py`, `bufferpool.py`

- [ ] Fix the mutable default in `TransactionWorker.__init__(self, transactions=[])`. Right now all 8 workers share one list, so every transaction would run 8 times. Use `transactions=None` and then `self.transactions = transactions or []`.
- [ ] `run()` starts a `threading.Thread(target=self.__run)`. `join()` joins it. Both are `pass` today, so the m3 testers run nothing.
- [ ] `__run`: `while not t.run(): if not t.lock_failed: break`. This retries lock aborts and stops on real failures like a duplicate insert. Fill `stats` and `result` the way the stub does.
- [ ] `bufferpool.py`: make sure `get_page`, `pin`, `unpin`, `_evict` and `flush_all` all hold `self.lock`, and that eviction never picks a page another thread has pinned.
- [ ] **Testing:** delete `./CS451`, run `m3_tester_part_1.py` and then `m3_tester_part_2.py`, then the `exam_tester_m3_*` pair, then `main.py` and the m1/m2 testers for regressions. Send failures to the owner of the broken file.

## Vik: Table latching and undo

**Files you've been working on:** `table.py`, `page.py`, `config.py`, `db.py`
**A3 files:** `table.py`, `page.py`

- [ ] Add one `threading.RLock` per table (`table.latch`) around `_new_rid`, `insert_base`, `append_tail`, `read_record`, `write_meta` and every `page_directory` write. One coarse latch is enough: with the GIL, finer ones gain nothing.
- [ ] `_apply_merges()` is marked "foreground thread only", but every worker thread calls it now. Run the install under the latch, so a merge can't be installed while another thread is in the middle of an update.
- [ ] Write the undo helpers from the interface section. Aborted tails get `RID = DELETED_RID`, which `__merge` already skips. Check that the before-copy (`needs_copy`) path skips them too.
- [ ] `get_version` must never land on an aborted tail. Add one assert-based check.

## Rayna: Locks and undo in queries

**Files you've been working on:** `query.py`, `table.py`
**A3 files:** `query.py`

- [ ] Call `lock(self.table, key, mode)` before touching a record and return `False` if it fails. No `current_txn()` check is needed: outside a transaction (the tester's main thread) `lock` returns `True` and `log_undo` does nothing.
- [ ] `select` / `select_version` / `sum` / `sum_version`: S lock on the key of each matched record. If a lock fails, return `False`.
- [ ] `insert`: X lock on the new key **before** the duplicate check. Then `log_undo` → `undo_insert` + remove from every index.
- [ ] `update`: X lock. `m3_tester_part_2` runs a select and then an update on the same key in one transaction, so this has to **upgrade** the transaction's own S lock. Save the old indirection and schema, then `log_undo` → `undo_update` + put the old index values back. A key change locks both the old key and the new key.
- [ ] `delete`: X lock. Collect the RIDs it stamps, then `log_undo` → `undo_delete` + add the index entries back.
- [ ] `increment` should work once select and update do.

## Anthony: Lock manager and transactions

**Files you've been working on:** `index.py`, `db.py`, `query.py` (bottom half)
**A3 files:** new `lock_manager.py`, `transaction.py`, `index.py`

- [x] Lock manager: a global dict `key → [mode, set(txn_ids)]` behind a `threading.Lock`. No-wait grant rules: S is compatible with S, X is exclusive, and S upgrades to X when this transaction is the only holder. A per-transaction set of held keys for `release_all`.
- [x] Thread-local current transaction that `Transaction.run` sets and then clears in a `finally`.
- [x] `Transaction.run`: reset the undo log and `lock_failed`, set the thread-local, run the queries, and abort when a result `is False`.
- [x] `abort()`: run the undo log in reverse, then `release_all`, then return `False`. `commit()`: `release_all`, then return `True`. Strict 2PL: locks are released only here.
- [x] Each transaction gets a unique id from `itertools.count()`. It keeps the same id across retries.
- [x] `index.py`: a `threading.Lock` in `add`, `remove`, `locate`, `locate_range` and `drop_index`. `create_index` builds its dict without the lock, then assigns it under the lock.
- [x] `db.py`: `close()` still flushes correctly once the workers have joined. No change needed.

---

## Timeline

1. **Nov 18:** Anthony pushes `lock_manager.py` and the `Transaction` skeleton. Maddie pushes the worker fix. Vik starts latching.
2. **Nov 21:** Rayna wires locks and undo into the queries. Vik pushes the undo helpers. Anthony finishes abort and commit.
3. **Nov 24:** Maddie runs the m3 testers end to end, plus an abort test: 2 workers updating the same keys, then compare the result against a serial run.
4. **Nov 25–28:** Bug fixes (Thanksgiving is Nov 26) and the README update.
5. **Nov 30:** One person submits the zip on Canvas.

## Open questions

- [x] Lock granularity: primary key value, as `(table.name, key)`. An insert can lock its key before a RID exists.
- [ ] Who submits on Canvas?

---

## Table API notes for Rayna (from Vik)

`table.py` now has `table.latch` (one `RLock`) and the three undo helpers. How to use them from `query.py`:

- **Latch:** every public table call (`insert_base`, `append_tail`, `read_record`, `write_meta`, `get_version`) already takes it. The undo helpers take it too, so don't wrap them. Your X/S locks protect the *record*; the latch protects the *table structures* (page directory, RIDs, merge install).
- **Atomic multi-step work:** `update` does read, `append_tail` and `write_meta` as separate calls. The X lock keeps other transactions out, but a merge install could still land between the calls. Wrap the three steps in `with self.table.latch:`. It is an `RLock`, so nesting is safe.
- **`undo_insert(base_rid)`:** withdraws the base (`RID = 0`). You remove the key from every index yourself.
- **`undo_update(base_rid, tail_rid, old_indirection, old_schema)`:** read `old_indirection` and `old_schema` from `read_record(base_rid)` **before** `append_tail`. `tail_rid` is what `append_tail` returned. It withdraws the tail and restores the base's indirection and schema, and it repairs the base if a merge already folded that tail in. You put the old index values back.
- **`undo_delete(base_rid, rids)`:** `rids` is every RID you stamped to `DELETED_RID`, **base included**. It writes each one back as its own RID. You add the index entries back.
- Aborted rows keep `RID == DELETED_RID`. Scans (`_matching_base_rids`, `create_index`) and `__merge` already skip them.
- `log_undo` order: Anthony runs the undo log in reverse, so log the table undo and the index fix as one function, or table first, then index.
- Self-check: `python test_table_undo.py`.
