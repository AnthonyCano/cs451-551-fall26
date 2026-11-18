import threading

_latch = threading.Lock()
_locks = {}
_held = {}
local = threading.local()


def current_txn():
    return getattr(local, "txn", None)


def acquire(txn_id, key, mode):
    with _latch:
        entry = _locks.get(key)
        if entry is None:
            _locks[key] = [mode, {txn_id}]
        elif txn_id in entry[1]:
            if mode == 'X' and entry[0] == 'S':
                if len(entry[1]) > 1:
                    return False
                entry[0] = 'X'
        elif mode == 'S' and entry[0] == 'S':
            entry[1].add(txn_id)
        else:
            return False
        _held.setdefault(txn_id, set()).add(key)
        return True


def release_all(txn_id):
    with _latch:
        for key in _held.pop(txn_id, ()):
            holders = _locks[key][1]
            holders.discard(txn_id)
            if not holders:
                del _locks[key]


def lock(table, key, mode):
    txn = current_txn()
    if txn is None:
        return True
    if acquire(txn.id, (table.name, key), mode):
        return True
    txn.lock_failed = True
    return False


def log_undo(fn):
    txn = current_txn()
    if txn is not None:
        txn.undo.append(fn)


if __name__ == "__main__":
    from types import SimpleNamespace

    assert acquire(1, "a", 'S') and acquire(2, "a", 'S')
    assert not acquire(1, "a", 'X')
    assert not acquire(3, "a", 'X')
    release_all(2)
    assert acquire(1, "a", 'X')
    assert acquire(1, "a", 'S') and acquire(1, "a", 'X')
    assert not acquire(2, "a", 'S')
    release_all(1)
    assert acquire(2, "a", 'X')
    release_all(2)
    assert not _locks and not _held

    table = SimpleNamespace(name="T")
    assert lock(table, 5, 'X') and current_txn() is None
    t1 = SimpleNamespace(id=10, undo=[], lock_failed=False)
    t2 = SimpleNamespace(id=11, undo=[], lock_failed=False)
    local.txn = t1
    assert lock(table, 5, 'X')
    log_undo(print)
    assert t1.undo == [print]
    local.txn = t2
    assert not lock(table, 5, 'S') and t2.lock_failed
    local.txn = None
    log_undo(print)
    release_all(10)
    release_all(11)
    assert not _locks and not _held
    print("lock_manager ok")
