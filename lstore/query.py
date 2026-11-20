from contextlib import contextmanager

from lstore.table import (
    Table,
    Record,
    INDIRECTION_COLUMN,
    RID_COLUMN,
    SCHEMA_ENCODING_COLUMN,
)
from lstore.index import Index
from lstore.config import NUM_METADATA_COLUMNS, DELETED_RID, BASE_RID_COLUMN
from lstore.lock_manager import lock, log_undo

"""
P2 --> P3:
Import lock and log_undo from the global lock manager (Anthony)
lock(table, key, mode) takes an S or X lock on (table.name, key)
log_undo(fn) saves fn to run in reverse order if the transaction aborts
Outside a transaction lock returns True and log_undo does nothing,
so the m1/m2 testers still run unlocked.
"""


class Query:
    """
    # Creates a Query object that can perform different queries on the specified table 
    Queries that fail must return False
    Queries that succeed should return the result or True
    Any query that crashes (due to exceptions) should return False
    """
    def __init__(self, table):
        self.table = table

    # find record by key
    def _base_rid(self, primary_key):
        rids = self.table.index.locate(self.table.key, primary_key)
        if not rids:
            return None
        return rids[0]

    # columns that currently have an index
    def _indexed_columns(self):
        return [c for c, idx in enumerate(self.table.index.indices) if idx is not None]

    # all columns are 64-bit ints: check before writing so a bad value
    # can't leave a half-written row behind.
    # bool is rejected (True is an int in Python), and -(2**63) is reserved as NULL
    @staticmethod
    def _valid_int(value):
        return (isinstance(value, int) and not isinstance(value, bool)
                and -(2**63) < value < 2**63)

    # select arguments must name a real column and project every column
    def _valid_select_args(self, search_key_index, projected_columns_index):
        return (isinstance(search_key_index, int)
                and 0 <= search_key_index < self.table.num_columns
                and len(projected_columns_index) == self.table.num_columns)

    # pin a record's pages for the duration of a block 
    # so bufferpool cannot evict them mid-query
    # uses table.pin/unpin once the table has them
    # finally always unpins, even if the query raises, so pins stay balanced.
    @contextmanager
    def _pinned(self, rid):
        pin = getattr(self.table, "pin", None)
        unpin = getattr(self.table, "unpin", None)
        if pin is None or unpin is None:
            yield
            return
        pin(rid)
        try:
            yield
        finally:
            unpin(rid)

    """
    P1 --> P2: 
    Need to expand index to be more than primary key and keep multiple RIDs
    """

    # find every base record where latest value in column = value.
    # indexed: returns all matches
    # not indexed: scan every live base record
    def _matching_base_rids(self, value, column):
        if self.table.index.indices[column] is not None:
            return self.table.index.locate(column, value)
        matching_rids = []
        for rid, (_, is_tail, _, _) in list(self.table.page_directory.items()):
            if is_tail:
                continue
            with self._pinned(rid):
                row = self.table.get_version(rid, 0)
            if row[RID_COLUMN] == DELETED_RID:
                continue
            if row[NUM_METADATA_COLUMNS + column] == value:
                matching_rids.append(rid)
        return matching_rids

    """
    P2 --> P3:
    New helper. 
    Locks are on the primary key, but select on a non-key column and sum only have RIDs
    Read the key, lock it, then re-read the row under the lock
    Returns False (lock busy), None (deleted meanwhile) or the row
    """

    # 2PL: lock a base record by its primary key, then read its row.
    # returns false -> lock busy (lock() already set, caller returns False)
    # returns none  -> record was deleted before we got the lock (caller skips it)
    # returns row   -> the row at relative_version, read while holding the lock
    def _lock_and_read(self, rid, mode, relative_version=0):
        key_col = NUM_METADATA_COLUMNS + self.table.key
        with self._pinned(rid):
            # take primary key of latest version
            key = self.table.get_version(rid, 0)[key_col]
            if not lock(self.table, key, mode):
                return False
            # re-read under the lock: the first read happened before we held it
            latest = self.table.get_version(rid, 0)
            if latest[key_col] != key and not lock(self.table, latest[key_col], mode):
                return False
            if latest[RID_COLUMN] == DELETED_RID:
                return None
            if relative_version == 0:
                return latest
            return self.table.get_version(rid, relative_version)

    # turn row into a record with projected columns
    def _to_record(self, base_rid, row, projected_columns_index):
        values = row[NUM_METADATA_COLUMNS:]
        columns = [v for v, keep in zip(values, projected_columns_index) if keep]
        return Record(base_rid, values[self.table.key], columns)

    
    """
    # internal Method
    # Read a record with specified RID
    # Returns True upon succesful deletion
    # Return False if record doesn't exist or is locked due to 2PL
    """

    """
    P1 --> P2: 
    Update insert, update, and delete.
    Need to update more than just the primary key index. 
    Need to update all indices that contain the deleted record.
    """

    """
    P2 --> P3:
    Delete takes X lock on the key before the lookup
    Stamping runs under table.latch so a merge install can't land mid-walk
    Every stamped RID (base included) is collected, and log_undo saves undo_delete
    Also saves re-adding the index entries for an abort.
    """

    # stamp all tail records and base record as deleted by setting RID to 0
    # remove the record from every index
    def delete(self, primary_key):
        try:
            # 2PL: X lock on the key before looking it up
            if not lock(self.table, primary_key, 'X'):
                return False
            base_rid = self._base_rid(primary_key)
            if base_rid is None:
                return False
            stamped = []  # every RID we set to DELETED_RID, for undo
            # base record stays pinned while we read it and stamp its tails.
            # latch: a merge install can't land between the reads and the stamps
            with self.table.latch, self._pinned(base_rid):
                # latest values, read before deleting
                latest = self.table.get_version(base_rid, 0)[NUM_METADATA_COLUMNS:]
                # stamp every slip taken out, newest first
                current_rid = self.table.read_record(base_rid)[INDIRECTION_COLUMN]
                while current_rid != base_rid:
                    with self._pinned(current_rid):
                        previous_rid = self.table.read_record(current_rid)[INDIRECTION_COLUMN]
                        self.table.write_meta(current_rid, RID_COLUMN, DELETED_RID)
                    stamped.append(current_rid)
                    current_rid = previous_rid
                self.table.write_meta(base_rid, RID_COLUMN, DELETED_RID)
                stamped.append(base_rid)
            # takes RID out of every index, not just the primary key
            indexed = self._indexed_columns()
            for column in indexed:
                self.table.index.remove(column, latest[column], base_rid)

            # on abort: restore every stamped RID, then put the index entries back
            def undo():
                self.table.undo_delete(base_rid, stamped)
                for column in indexed:
                    self.table.index.add(column, latest[column], base_rid)
            log_undo(undo)
            return True
        except Exception:
            return False
    
    
    """
    # Insert a record with specified columns
    # Return True upon succesful insertion
    # Returns False if insert fails for whatever reason
    """
    """
    P2 --> P3:
    Insert takes an X lock on the new key before the duplicate check
    Two transactions can't both insert the same key.
    log_undo saves undo_insert + removing the RID from every index for an abort
    """
    # has to have same # of columns as table, no none values, no duplicate primary key
    def insert(self, *columns):
        try:
            if len(columns) != self.table.num_columns:
                return False
            if any(c is None for c in columns):
                return False
            if not all(self._valid_int(c) for c in columns):
                return False
            key = columns[self.table.key]
            # 2PL: X lock on the new key before the duplicate check
            # two transactions can't both insert the same key
            if not lock(self.table, key, 'X'):
                return False
            if self._base_rid(key) is not None:
                return False
            rid = self.table.insert_base(columns)
            # add the new record to every indexed column, not only the key
            indexed = self._indexed_columns()
            for column in indexed:
                self.table.index.add(column, columns[column], rid)

            # on abort: withdraw the base record, then take it out of every index
            def undo():
                self.table.undo_insert(rid)
                for column in indexed:
                    self.table.index.remove(column, columns[column], rid)
            log_undo(undo)
            return True
        except Exception:
            return False

    
    """
    # Read matching record with specified search key
    # :param search_key: the value you want to search based on
    # :param search_key_index: the column index you want to search based on
    # :param projected_columns_index: what columns to return. array of 1 or 0 values.
    # Returns a list of Record objects upon success
    # Returns False if record locked by TPL
    # Assume that select will never be called on a key that doesn't exist
    """
    """
    P2 --> P3:
    Select takes an S lock on each matched record's key (through _lock_and_read)
    On a primary key search the key is locked before the lookup
    A busy lock returns False, so the worker retries the transaction
    Rows deleted or no longer matching by the time we hold the lock are skipped
    """
    # find matching record with specified key
    # give latest version of record with newest tail record
    # build new result
    def select(self, search_key, search_key_index, projected_columns_index):
        try:
            if not self._valid_select_args(search_key_index, projected_columns_index):
                return False
            # 2PL: on a primary key search, lock the key before the lookup
            # makes sure set of matches can't change under us
            if search_key_index == self.table.key and not lock(self.table, search_key, 'S'):
                return False
            records = []
            for rid in self._matching_base_rids(search_key, search_key_index):
                row = self._lock_and_read(rid, 'S')
                if row is False:
                    return False   # lock busy: abort, worker retries
                if row is None:
                    continue       # deleted before we got the lock
                if row[NUM_METADATA_COLUMNS + search_key_index] != search_key:
                    continue       # value changed before we got the lock: no longer a match
                records.append(self._to_record(rid, row, projected_columns_index))
            return records
        except Exception:
            return False

    
    """
    # Read matching record with specified search key
    # :param search_key: the value you want to search based on
    # :param search_key_index: the column index you want to search based on
    # :param projected_columns_index: what columns to return. array of 1 or 0 values.
    # :param relative_version: the relative version of the record you need to retreive.
    # Returns a list of Record objects upon success
    # Returns False if record locked by TPL
    # Assume that select will never be called on a key that doesn't exist
    """
    """
    P2 --> P3:
    Same S locking as select
    match is re-checked on the latest version
    (that is what _matching_base_rids matched on)
    older version is read while the lock is held
    lock covers every version of the record.
    """
    def select_version(self, search_key, search_key_index, projected_columns_index, relative_version):
        try:
            if not self._valid_select_args(search_key_index, projected_columns_index):
                return False
            # 2PL: on a primary key search, lock the key before the lookup
            if search_key_index == self.table.key and not lock(self.table, search_key, 'S'):
                return False
            records = []
            for rid in self._matching_base_rids(search_key, search_key_index):
                latest = self._lock_and_read(rid, 'S')
                if latest is False:
                    return False   # lock busy: abort, worker retries
                if latest is None:
                    continue       # deleted before we got the lock
                if latest[NUM_METADATA_COLUMNS + search_key_index] != search_key:
                    continue       # latest value no longer matches
                # lock held: safe to read the older version
                with self._pinned(rid):
                    row = self.table.get_version(rid, relative_version)
                records.append(self._to_record(rid, row, projected_columns_index))
            return records
        except Exception:
            return False

    
    """
    # Update a record with specified key and columns
    # Returns True if update is succesful
    # Returns False if no records exist with given key or if the target record cannot be accessed due to 2PL locking
    """
    """
    P1 --> P2:
    Update must now keep a before copy of the record in a tail page.
    This means adding another list to the update method.
    """

    """
    P2 --> P3:
    Update takes an X lock on the key
    upgrades the transaction's own S lock after a select on the same key
    key change also X locks the new key
    Read, append_tail and write_meta run under table.latch so a merge install can't land between them
    Old indirection and schema are saved before append_tail
    log_undo saves undo_update + restoring the old index values
    """

    # none means to leave as is: no changes
    # check input and check key collision
    # build a tail record, file the tail record, move pointer, update schema encoding,
    # move the record in every index whose column value changed (key included)
    def update(self, primary_key, *columns):
        try:
            if len(columns) != self.table.num_columns:
                return False
            if not all(c is None or self._valid_int(c) for c in columns):
                return False
            # 2PL: X lock on the key. Upgrades this transaction's own S lock
            # when it already selected the same key (m3_tester_part_2)
            if not lock(self.table, primary_key, 'X'):
                return False
            base_rid = self._base_rid(primary_key)
            if base_rid is None:
                return False
            if all(c is None for c in columns):
                return True
            new_key = columns[self.table.key]
            is_key_change = new_key is not None and new_key != primary_key
            # a key change also locks the new key, before the collision check
            if is_key_change and not lock(self.table, new_key, 'X'):
                return False
            if is_key_change and self._base_rid(new_key) is not None:
                return False
            # base record stays pinned from first read to last write.
            # latch: a merge install can't land between the read,
            # append_tail and write_meta
            with self.table.latch, self._pinned(base_rid):
                base_row = self.table.read_record(base_rid)
                # saved for undo_update: must be read before append_tail
                old_indirection = base_row[INDIRECTION_COLUMN]
                old_schema = base_row[SCHEMA_ENCODING_COLUMN]
                old_values = list(self.table.get_version(base_rid, 0)[NUM_METADATA_COLUMNS:])
                new_values = list(old_values)
                schema = 0
                for index, col in enumerate(columns):
                    if col is not None:
                        new_values[index] = col
                        schema |= 1 << index
                # BaseRID: the merge uses it to map each tail back to its base record.
                # only passed once the table has the column
                tail_kwargs = {}
                if NUM_METADATA_COLUMNS > BASE_RID_COLUMN:
                    tail_kwargs["base_rid"] = base_rid
                # file the slip, then move pointer to it
                new_tail_rid = self.table.append_tail(
                    new_values, base_row[INDIRECTION_COLUMN], schema, **tail_kwargs)
                self.table.write_meta(base_rid, INDIRECTION_COLUMN, new_tail_rid)
                self.table.write_meta(base_rid, SCHEMA_ENCODING_COLUMN,
                                      base_row[SCHEMA_ENCODING_COLUMN] | schema)
            # indexes live in memory, not the bufferpool: no pin needed
            changed = [c for c in self._indexed_columns()
                       if old_values[c] != new_values[c]]
            for column in changed:
                self.table.index.remove(column, old_values[column], base_rid)
                self.table.index.add(column, new_values[column], base_rid)

            # on abort: withdraw the tail, restore indirection and schema,
            # then put the old index values back
            def undo():
                self.table.undo_update(base_rid, new_tail_rid,
                                       old_indirection, old_schema)
                for column in changed:
                    self.table.index.remove(column, new_values[column], base_rid)
                    self.table.index.add(column, old_values[column], base_rid)
            log_undo(undo)
            return True
        except Exception:
            return False

    
    """
    :param start_range: int         # Start of the key range to aggregate 
    :param end_range: int           # End of the key range to aggregate 
    :param aggregate_columns: int  # Index of desired column to aggregate
    # this function is only called on the primary key.
    # Returns the summation of the given range upon success
    # Returns False if no record exists in the given range
    """
    def sum(self, start_range, end_range, aggregate_column_index):
        return self.sum_version(start_range, end_range, aggregate_column_index, 0)

    
    """
    :param start_range: int         # Start of the key range to aggregate 
    :param end_range: int           # End of the key range to aggregate 
    :param aggregate_columns: int  # Index of desired column to aggregate
    :param relative_version: the relative version of the record you need to retreive.
    # this function is only called on the primary key.
    # Returns the summation of the given range upon success
    # Returns False if no record exists in the given range
    """
    """
    P2 --> P3:
    Sum takes an S lock on every record in the range before adding it (through _lock_and_read)
    busy lock returns False so the worker retries
    Records deleted meanwhile are skipped. If none are left, return False.
    sum() calls this with version 0, so it gets the same locking.
    """
    def sum_version(self, start_range, end_range, aggregate_column_index, relative_version):
        try:
            rids = self.table.index.locate_range(start_range, end_range, self.table.key)
            if not rids:
                return False
            column = NUM_METADATA_COLUMNS + aggregate_column_index
            total = 0
            found = 0
            # 2PL: S lock every record in the range before reading it
            for rid in rids:
                row = self._lock_and_read(rid, 'S', relative_version)
                if row is False:
                    return False   # lock busy: abort, worker retries
                if row is None:
                    continue       # deleted before we got the lock
                total += row[column]
                found += 1
            # every record in the range was deleted meanwhile: same as an empty range
            if found == 0:
                return False
            return total
        except Exception:
            return False

    
    """
    incremenets one column of the record
    this implementation should work if your select and update queries already work
    :param key: the primary of key of the record to increment
    :param column: the column to increment
    # Returns True is increment is successful
    # Returns False if no record matches key or if target record is locked by 2PL.
    """
    def increment(self, key, column):
        # Fixed from skeleton: select() returns a list of Record objects, so the value
        # is records[0].columns[column] (Record isn't subscriptable). Also check for
        # False/[] before indexing so a missing key returns False instead of crashing.
        try:
            records = self.select(key, self.table.key, [1] * self.table.num_columns)
            if not records:
                return False  # select failed (False) or no such key ([])
            r = records[0]
            updated_columns = [None] * self.table.num_columns
            updated_columns[column] = r.columns[column] + 1
            u = self.update(key, *updated_columns)
            return u
        except Exception:
            return False
