import threading
from types import SimpleNamespace

from lstore import lock_manager
from lstore.index import Index
from lstore.transaction import Transaction

TABLE = SimpleNamespace(name="T")


def reset():
    lock_manager._locks.clear()
    lock_manager._held.clear()
    lock_manager.local.txn = None


def writer(log, undone):
    def write(key, value):
        if not lock_manager.lock(TABLE, key, 'X'):
            return False
        log.append(value)
        lock_manager.log_undo(lambda: (log.remove(value), undone.append(value)))
        return True
    return write


def test_shared_and_exclusive():
    reset()
    assert lock_manager.acquire(1, "a", 'S')
    assert lock_manager.acquire(2, "a", 'S')
    assert not lock_manager.acquire(3, "a", 'X')
    assert lock_manager.acquire(4, "b", 'X')
    assert not lock_manager.acquire(5, "b", 'S')


def test_upgrade():
    reset()
    lock_manager.acquire(1, "a", 'S')
    lock_manager.acquire(2, "a", 'S')
    assert not lock_manager.acquire(1, "a", 'X')
    lock_manager.release_all(2)
    assert lock_manager.acquire(1, "a", 'X')
    assert not lock_manager.acquire(2, "a", 'S')


def test_reacquire_held():
    reset()
    assert lock_manager.acquire(1, "a", 'S') and lock_manager.acquire(1, "a", 'S')
    assert lock_manager.acquire(1, "a", 'X') and lock_manager.acquire(1, "a", 'S')
    assert lock_manager.acquire(1, "a", 'X')


def test_release_all():
    reset()
    lock_manager.acquire(1, "a", 'X')
    lock_manager.acquire(1, "b", 'S')
    lock_manager.release_all(1)
    lock_manager.release_all(99)
    assert not lock_manager._locks and not lock_manager._held
    assert lock_manager.acquire(2, "a", 'X')


def test_helpers_outside_txn():
    reset()
    assert lock_manager.lock(TABLE, 1, 'X')
    lock_manager.log_undo(print)
    assert not lock_manager._locks


def test_commit():
    reset()
    log, undone = [], []
    t = Transaction()
    t.add_query(writer(log, undone), TABLE, 1, "a")
    t.add_query(writer(log, undone), TABLE, 2, "b")
    assert t.run() is True
    assert log == ["a", "b"] and not undone
    assert not lock_manager._locks and lock_manager.current_txn() is None


def test_abort_undoes_in_reverse():
    reset()
    log, undone = [], []
    t = Transaction()
    t.add_query(writer(log, undone), TABLE, 1, "a")
    t.add_query(writer(log, undone), TABLE, 2, "b")
    t.add_query(lambda: False, TABLE)
    assert t.run() is False and not t.lock_failed
    assert log == [] and undone == ["b", "a"]
    assert not lock_manager._locks and lock_manager.current_txn() is None


def test_lock_abort_then_retry():
    reset()
    log, undone = [], []
    lock_manager.acquire(-1, ("T", 2), 'X')
    t = Transaction()
    t.add_query(writer(log, undone), TABLE, 1, "a")
    t.add_query(writer(log, undone), TABLE, 2, "b")
    assert t.run() is False and t.lock_failed and log == []
    lock_manager.release_all(-1)
    assert t.run() is True and not t.lock_failed and log == ["a", "b"]


def test_zero_result_commits():
    reset()
    t = Transaction()
    t.add_query(lambda: 0, TABLE)
    assert t.run() is True


def test_raising_query_aborts_and_releases():
    reset()
    log, undone = [], []
    t = Transaction()
    t.add_query(writer(log, undone), TABLE, 1, "a")
    t.add_query(lambda: 1 / 0, TABLE)
    assert t.run() is False and log == []
    assert not lock_manager._locks and lock_manager.current_txn() is None


def test_index_concurrent_same_value():
    index = Index(SimpleNamespace(num_columns=2, key=0))
    keep = []

    def work(n):
        keep.append(n * 1000)
        index.add(0, 50, n * 1000)
        for i in range(1, 2000):
            index.add(0, 50, n * 1000 + i)
            index.locate(0, 50)
            index.remove(0, 50, n * 1000 + i)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(index.locate(0, 50)) == sorted(keep)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
