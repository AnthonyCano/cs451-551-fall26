# Small self-check for the table latch and the undo helpers (assignment 3).
# Plain asserts, in-memory table, no bufferpool. Run: python test_table_undo.py
import threading

import lstore.table as table_module
from lstore.table import Table, INDIRECTION_COLUMN, RID_COLUMN, SCHEMA_ENCODING_COLUMN
from lstore.config import NUM_METADATA_COLUMNS, DELETED_RID

USER = NUM_METADATA_COLUMNS


def user_values(row):
    return row[USER:]


def file_update(table, base_rid, new_values, schema=0b10):
    # same steps query.update takes: remember the old pointer, file the slip, move the pointer
    base = table.read_record(base_rid)
    old_indirection, old_schema = base[INDIRECTION_COLUMN], base[SCHEMA_ENCODING_COLUMN]
    tail = table.append_tail(new_values, old_indirection, schema, base_rid)
    table.write_meta(base_rid, INDIRECTION_COLUMN, tail)
    table.write_meta(base_rid, SCHEMA_ENCODING_COLUMN, old_schema | schema)
    return tail, old_indirection, old_schema


def test_undo_update():
    t = Table("t", 3, 0)
    rid = t.insert_base([1, 10, 20])
    first, *_ = file_update(t, rid, [1, 11, 20])
    tail, old_ind, old_schema = file_update(t, rid, [1, 99, 20])
    t.undo_update(rid, tail, old_ind, old_schema)
    # the aborted slip is withdrawn and get_version never lands on it
    assert t.read_record(tail)[RID_COLUMN] == DELETED_RID
    row = t.get_version(rid, 0)
    assert row[RID_COLUMN] != DELETED_RID and user_values(row) == [1, 11, 20]
    # undoing the only slip goes back to the original book
    rid2 = t.insert_base([2, 5, 5])
    tail2, old_ind2, old_schema2 = file_update(t, rid2, [2, 6, 5])
    t.undo_update(rid2, tail2, old_ind2, old_schema2)
    assert user_values(t.get_version(rid2, 0)) == [2, 5, 5]
    assert t.read_record(rid2)[SCHEMA_ENCODING_COLUMN] == 0


def test_undo_insert_and_delete():
    t = Table("t", 3, 0)
    rid = t.insert_base([1, 1, 1])
    t.undo_insert(rid)
    assert t.read_record(rid)[RID_COLUMN] == DELETED_RID
    rid = t.insert_base([2, 2, 2])
    tail, *_ = file_update(t, rid, [2, 3, 2])
    for r in (tail, rid):
        t.write_meta(r, RID_COLUMN, DELETED_RID)  # what query.delete does
    t.undo_delete(rid, [tail, rid])
    assert t.read_record(rid)[RID_COLUMN] == rid
    assert user_values(t.get_version(rid, 0)) == [2, 3, 2]


def test_abort_after_merge():
    # a merge folds a slip into the book, then that slip aborts: the book must be repaired
    old_threshold = table_module.MERGE_THRESHOLD
    table_module.MERGE_THRESHOLD = 4
    try:
        t = Table("t", 3, 0)
        rid = t.insert_base([1, 10, 20])
        slips = [file_update(t, rid, [1, 10 + i, 20]) for i in range(1, 4)]
        tail, old_ind, old_schema = file_update(t, rid, [1, 777, 20])  # 4th slip starts a merge
        t.finish_merges()  # merge copied the 777 slip into the book and installed
        t.undo_update(rid, tail, old_ind, old_schema)
        assert user_values(t.get_version(rid, 0)) == [1, 13, 20]
    finally:
        table_module.MERGE_THRESHOLD = old_threshold


def test_threads_get_unique_rids():
    t = Table("t", 3, 0)
    seen, lock = [], threading.Lock()

    def work(offset):
        mine = [t.insert_base([offset * 1000 + i, 0, 0]) for i in range(200)]
        with lock:
            seen.extend(mine)

    workers = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [w.start() for w in workers]
    [w.join() for w in workers]
    assert len(seen) == len(set(seen)) == 1600


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
