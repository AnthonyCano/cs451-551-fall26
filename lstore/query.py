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
        for rid, (_, is_tail, _, _) in self.table.page_directory.items():
            if is_tail:
                continue
            with self._pinned(rid):
                row = self.table.get_version(rid, 0)
            if row[RID_COLUMN] == DELETED_RID:
                continue
            if row[NUM_METADATA_COLUMNS + column] == value:
                matching_rids.append(rid)
        return matching_rids

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

    # stamp all tail records and base record as deleted by setting RID to 0
    # remove the record from every index
    def delete(self, primary_key):
        try:
            base_rid = self._base_rid(primary_key)
            if base_rid is None:
                return False
            # base record stays pinned while we read it and stamp its tails
            with self._pinned(base_rid):
                # latest values, read before deleting
                latest = self.table.get_version(base_rid, 0)[NUM_METADATA_COLUMNS:]
                # stamp every slip taken out, newest first
                current_rid = self.table.read_record(base_rid)[INDIRECTION_COLUMN]
                while current_rid != base_rid:
                    with self._pinned(current_rid):
                        previous_rid = self.table.read_record(current_rid)[INDIRECTION_COLUMN]
                        self.table.write_meta(current_rid, RID_COLUMN, DELETED_RID)
                    current_rid = previous_rid
                self.table.write_meta(base_rid, RID_COLUMN, DELETED_RID)
            # takes RID out of every index, not just the primary key
            for column in self._indexed_columns():
                self.table.index.remove(column, latest[column], base_rid)
            return True
        except Exception:
            return False
    
    
    """
    # Insert a record with specified columns
    # Return True upon succesful insertion
    # Returns False if insert fails for whatever reason
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
            if self._base_rid(key) is not None:
                return False 
            rid = self.table.insert_base(columns)
            # add the new record to every indexed column, not only the key
            for column in self._indexed_columns():
                self.table.index.add(column, columns[column], rid)
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
    # find matching record with specified key
    # give latest version of record with newest tail record
    # build new result
    def select(self, search_key, search_key_index, projected_columns_index):
        try:
            if not self._valid_select_args(search_key_index, projected_columns_index):
                return False
            records = []
            for rid in self._matching_base_rids(search_key, search_key_index):
                with self._pinned(rid):
                    row = self.table.get_version(rid, 0)
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
    def select_version(self, search_key, search_key_index, projected_columns_index, relative_version):
        try:
            if not self._valid_select_args(search_key_index, projected_columns_index):
                return False
            records = []
            for rid in self._matching_base_rids(search_key, search_key_index):
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
            base_rid = self._base_rid(primary_key)
            if base_rid is None:
                return False
            if all(c is None for c in columns):
                return True
            new_key = columns[self.table.key]
            is_key_change = new_key is not None and new_key != primary_key
            if is_key_change and self._base_rid(new_key) is not None:
                return False 
            # base record stays pinned from first read to last write
            with self._pinned(base_rid):
                base_row = self.table.read_record(base_rid)
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
            for column in self._indexed_columns():
                if old_values[column] != new_values[column]:
                    self.table.index.remove(column, old_values[column], base_rid)
                    self.table.index.add(column, new_values[column], base_rid)
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
    def sum_version(self, start_range, end_range, aggregate_column_index, relative_version):
        try:
            rids = self.table.index.locate_range(start_range, end_range, self.table.key)
            if not rids:
                return False
            column = NUM_METADATA_COLUMNS + aggregate_column_index
            return sum(self.table.get_version(rid, relative_version)[column] for rid in rids)
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
