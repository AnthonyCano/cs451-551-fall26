from email.mime import base

from lstore.table import (
    Table,
    Record,
    INDIRECTION_COLUMN,
    RID_COLUMN,
    SCHEMA_ENCODING_COLUMN,
)
from lstore.index import Index
from lstore.config import NUM_METADATA_COLUMNS, DELETED_RID


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

    # find records by non-key column
    def _matching_base_rids(self, value, column):
        if column == self.table.key:
            rid = self._base_rid(value)
            return [] if rid is None else [rid]
        matching_rids = []
        for rid, (_, is_tail, _, _) in self.table.page_directory.items():
            if is_tail:
                continue
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
    # stamp all tail records and base record as deleted by setting index to 0
    # remove key from index
    def delete(self, primary_key):
        try:
            base_rid = self._base_rid(primary_key)
            if base_rid is None:
                return False
            # stamp every slip WITHDRAWN, newest first, until we're back at the book
            current_rid = self.table.read_record(base_rid)[INDIRECTION_COLUMN]
            while current_rid != base_rid:
                previous_rid = self.table.read_record(current_rid)[INDIRECTION_COLUMN]
                self.table.write_meta(current_rid, RID_COLUMN, DELETED_RID)
                current_rid = previous_rid
            self.table.write_meta(base_rid, RID_COLUMN, DELETED_RID)
            self.table.index.remove(primary_key)
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
            key = columns[self.table.key]
            if self._base_rid(key) is not None:
                return False 
            rid = self.table.insert_base(columns)
            self.table.index.add(key, rid)
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
            records = []
            for rid in self._matching_base_rids(search_key, search_key_index):
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
        pass

    
    """
    # Update a record with specified key and columns
    # Returns True if update is succesful
    # Returns False if no records exist with given key or if the target record cannot be accessed due to 2PL locking
    """
    # none means to leave as is: no changes
    # check input and check key collision
    # build a tail record, file the tail record, move pointer, update scheme encoding, update index (if key changes)
    def update(self, primary_key, *columns):
        try:
            if len(columns) != self.table.num_columns:
                return False
            base_rid = self._base_rid(primary_key)
            if base_rid is None:
                return False
            if all(c is None for c in columns):
                return True  # nothing to correct
            new_key = columns[self.table.key]
            is_key_change = new_key is not None and new_key != primary_key
            if is_key_change and self._base_rid(new_key) is not None:
                return False  # new call number already taken
            base_row = self.table.read_record(base_rid)
            new_row = list(self.table.get_version(base_rid, 0)[NUM_METADATA_COLUMNS:])
            schema = 0
            for index, col in enumerate(columns):
                if col is not None:
                    new_row[index] = col
                    schema |= 1 << index
            # file the slip, then move the book's sticky note to it
            new_tail_rid = self.table.append_tail(new_row, base_row[INDIRECTION_COLUMN], schema)
            self.table.write_meta(base_rid, INDIRECTION_COLUMN, new_tail_rid)
            self.table.write_meta(base_rid, SCHEMA_ENCODING_COLUMN,
                                  base_row[SCHEMA_ENCODING_COLUMN] | schema)
            if is_key_change:
                self.table.index.remove(primary_key)
                self.table.index.add(new_key, base_rid)
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
        pass

    
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
        pass

    
    """
    incremenets one column of the record
    this implementation should work if your select and update queries already work
    :param key: the primary of key of the record to increment
    :param column: the column to increment
    # Returns True is increment is successful
    # Returns False if no record matches key or if target record is locked by 2PL.
    """
    def increment(self, key, column):
        r = self.select(key, self.table.key, [1] * self.table.num_columns)[0]
        if r is not False:
            updated_columns = [None] * self.table.num_columns
            updated_columns[column] = r[column] + 1
            u = self.update(key, *updated_columns)
            return u
        return False
