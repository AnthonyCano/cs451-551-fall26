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
