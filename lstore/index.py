"""
A data strucutre holding indices for various columns of a table. Key column should be indexd by default, other columns can be indexed through this object. Indices are usually B-Trees, but other data structures can be used as well.
"""

class Index:

    def __init__(self, table):
        self.table = table 
        self.indices = [None] * table.num_columns
        self.indices[table.key] = {}
    """
    # returns the location of all records with the given value on column "column"
    """

    def locate(self, column, value):
        index = self.indices[column]

        # Column isn't indexed, or the value isn't in the index
        if index is None or value not in index:
            return []

        rids = []
        rids.append(index[value])
        return rids
    """
    # Returns the RIDs of all records with values in column "column" between "begin" and "end"
    """

    def locate_range(self, begin, end, column):
        index = self.indices[column]
        if index is None:
            return [] # Column isn't indexed, return an empty list
        
        rids = []
        for value in range(begin, end + 1):
            if value in index:
                rids.append(index[value])
        return rids

    def add(self, key, rid):
        self.indices[self.table.key][key] = rid

    def remove(self, key):
        self.indices[self.table.key].pop(key, None)

    """
    # optional: Create index on specific column
    """

    def create_index(self, column_number):
        pass

    """
    # optional: Drop index of specific column
    """

    def drop_index(self, column_number):
        pass
