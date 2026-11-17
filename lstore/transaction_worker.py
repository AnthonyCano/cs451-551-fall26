import threading


class TransactionWorker:

    """
    # Creates a transaction worker object.
    """
    def __init__(self, transactions=None):
        self.stats = []
        self.transactions = transactions or []  # not a shared default list
        self.result = 0
        self.thread = None

    """
    Appends t to transactions
    """
    def add_transaction(self, t):
        self.transactions.append(t)

    """
    Runs all transaction as a thread
    """
    def run(self):
        self.thread = threading.Thread(target=self.__run)
        self.thread.start()

    """
    Waits for the worker to finish
    """
    def join(self):
        if self.thread is not None:
            self.thread.join()

    def __run(self):
        for transaction in self.transactions:
            # no-wait 2PL: retry lock aborts, give up on real failures (e.g. duplicate key)
            # shortcut: drop getattr once Transaction always sets lock_failed
            while not (committed := transaction.run()):
                if not getattr(transaction, "lock_failed", False):
                    break
            # each transaction returns True if committed or False if aborted
            self.stats.append(committed)
        # stores the number of transactions that committed
        self.result = len(list(filter(lambda x: x, self.stats)))
