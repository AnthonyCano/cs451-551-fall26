from lstore.table import Table, Record
from lstore.index import Index
from lstore import lock_manager
import itertools

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
