"""
A data strucutre holding indices for various columns of a table. Key column should be indexd by default, other columns can be indexed through this object. Indices are usually B-Trees, but other data structures can be used as well.
"""

# Shared constants from config.py
from lstore.config import NUM_METADATA_COLUMNS, DELETED_RID

# Same as RID_COLUMN in table.py
RID_COLUMN = 1

class Index:

    def __init__(self, table):
        self.table = table
        # One dict per indexed column: value -> set of base RIDs. None means not indexed
        self.indices = [None] * table.num_columns
        self.indices[table.key] = {}
    """
    # returns the location of all records with the given value on column "column"
    """

    def locate(self, column, value):
        index = self.indices[column]

        # Column isn't indexed
        if index is None:
            return []

        return list(index.get(value, ()))

    """
    # Returns the RIDs of all records with values in column "column" between "begin" and "end"
    """

    def locate_range(self, begin, end, column):
        index = self.indices[column]
        if index is None:
            return [] # Column isn't indexed, return an empty list

        rids = []
        for value in range(begin, end + 1):
            rids.extend(index.get(value, ()))

        return rids

    """
    # Adds rid under value on column "column". Does nothing if the column isn't indexed
    """

    def add(self, column, value, rid):
        index = self.indices[column]
        if index is not None:
            index.setdefault(value, set()).add(rid)

    """
    # Removes rid from value on column "column". Drops the value once it has no RIDs left
    """

    def remove(self, column, value, rid):
        index = self.indices[column]
        if index is None or value not in index:
            return # Column isn't indexed, or the value was never added

        index[value].discard(rid)
        if not index[value]:
            del index[value]

    """
    # Create index on specific column
    # Indexes the latest value of every base record that isn't deleted
    """

    def create_index(self, column_number):
        self.indices[column_number] = {}

        for rid, (_, is_tail, _, _) in list(self.table.page_directory.items()):
            if is_tail:
                continue # Only index base records, tail records are reached through them

            row = self.table.get_version(rid, 0)
            if row[RID_COLUMN] == DELETED_RID:
                continue # Skip deleted records

            self.add(column_number, row[NUM_METADATA_COLUMNS + column_number], rid)

    """
    # Drop index of specific column. The key column always stays indexed
    """

    def drop_index(self, column_number):
        if column_number != self.table.key:
            self.indices[column_number] = None
