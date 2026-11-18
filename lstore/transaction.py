import itertools

from lstore import lock_manager

_ids = itertools.count(1)


class Transaction:

    """
    # Creates a transaction object.
    """
    def __init__(self):
        self.queries = []
        self.id = next(_ids)
        self.undo = []
        self.lock_failed = False

    """
    # Adds the given query to this transaction
    # Example:
    # q = Query(grades_table)
    # t = Transaction()
    # t.add_query(q.update, grades_table, 0, *[None, 1, None, 2, None])
    """
    def add_query(self, query, table, *args):
        self.queries.append((query, args))

    # If you choose to implement this differently this method must still return True if transaction commits or False on abort
    def run(self):
        self.undo = []
        self.lock_failed = False
        lock_manager.local.txn = self
        try:
            for query, args in self.queries:
                if query(*args) is False:
                    return self.abort()
        except Exception:
            return self.abort()
        finally:
            lock_manager.local.txn = None
        return self.commit()

    def abort(self):
        try:
            for fn in reversed(self.undo):
                try:
                    fn()
                except Exception:
                    pass
        finally:
            self.undo = []
            lock_manager.release_all(self.id)
        return False

    def commit(self):
        self.undo = []
        lock_manager.release_all(self.id)
        return True


if __name__ == "__main__":
    from types import SimpleNamespace

    table = SimpleNamespace(name="T")
    log = []
    undone = []

    def write(key, value):
        if not lock_manager.lock(table, key, 'X'):
            return False
        log.append(value)
        lock_manager.log_undo(lambda: (log.remove(value), undone.append(value)))
        return True

    t1 = Transaction()
    t1.add_query(write, table, 1, "a")
    t1.add_query(write, table, 2, "b")
    assert t1.run() is True and log == ["a", "b"]
    assert lock_manager.current_txn() is None and not lock_manager._locks

    t2 = Transaction()
    t2.add_query(write, table, 3, "c")
    t2.add_query(write, table, 4, "d")
    t2.add_query(lambda: False, table)
    assert t2.run() is False and not t2.lock_failed
    assert log == ["a", "b"] and undone == ["d", "c"]
    assert not lock_manager._locks

    assert lock_manager.acquire(-1, ("T", 6), 'X')
    t3 = Transaction()
    t3.add_query(write, table, 5, "e")
    t3.add_query(write, table, 6, "f")
    assert t3.run() is False and t3.lock_failed and log == ["a", "b"]
    lock_manager.release_all(-1)
    assert t3.run() is True and not t3.lock_failed and log == ["a", "b", "e", "f"]

    t4 = Transaction()
    t4.add_query(lambda: 0, table)
    assert t4.run() is True

    assert not lock_manager._locks and lock_manager.current_txn() is None
    print("transaction ok")
