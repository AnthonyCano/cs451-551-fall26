import json
import os
import shutil

from lstore.bufferpool import BufferPool
from lstore.config import NUM_METADATA_COLUMNS
from lstore.page import Page, PageRange
from lstore.table import Table

class Database():

    def __init__(self):
        self.tables = {}
        self.path = None
        self.bufferpool = None

    """ Loads every table that is saved under the path. Indexes are rebuilt"""
    def open(self, path):
        self.path = path
        self.bufferpool = BufferPool(path)

        for name in os.listdir(path):
            meta_path = os.path.join(path, name, "meta.json") # Path to our metadata
            
            if os.path.exists(meta_path):
                table = self._load_table(meta_path)
                self.tables[table.name] = table

    """ Writes every table's pages and metadata to disk"""
    def close(self):
        if self.path is None:
            return

        for table in self.tables.values():
            table.finish_merges() # let background merges land before we save
            self._save_table(table)
        self.bufferpool.flush_all() # Write back any dirty pages still in the bufferpool

    """
    # Creates a new table
    :param name: string         #Table name
    :param num_columns: int     #Number of Columns: all columns are integer
    :param key: int             #Index of table key in columns
    """
    def create_table(self, name, num_columns, key_index):
        self._remove_table_files(name)
        table = Table(name, num_columns, key_index)
        table.bufferpool = self.bufferpool
        self.tables[name] = table
        return table

    
    """
    # Deletes the specified table
    """
    def drop_table(self, name):
        self._remove_table_files(name)
        self.tables.pop(name, None)

    
    """
    # Returns table with the passed name
    """
    def get_table(self, name):
        return self.tables.get(name)

    def _remove_table_files(self, name):
        if self.path is not None:
            shutil.rmtree(os.path.join(self.path, name), ignore_errors=True)

    def _meta_path(self, name):
        return os.path.join(self.path, name, "meta.json")

    """ Writes a table's meta.json. The pages themselves reach disk through bufferpool.flush_all()"""
    def _save_table(self, table):
        ranges = []
        for range_idx, page_range in enumerate(table.page_ranges):
            ranges.append({
                "base_page_idx": page_range.base_page_idx,
                "base_slot": page_range.base_slot,
                "tail_slot": page_range.tail_slot,
                "num_tail_pages": page_range.num_tail_pages,
                "merged_tails": page_range.merged_tails,
            })
        meta = {
            "name": table.name,
            "num_columns": table.num_columns,
            "key": table.key,
            "next_rid": table.next_rid,
            "page_ranges": ranges,
            "tps": [[wing, page, tps] for (wing, page), tps in table.tps.items()],
            "page_directory": [[rid, *location] for rid, location in table.page_directory.items()],
        }
        os.makedirs(os.path.dirname(self._meta_path(table.name)), exist_ok=True)
        with open(self._meta_path(table.name), "w") as f:
            json.dump(meta, f)

    """ Rebuilds a table from its meta.json and page files, then rebuilds its primary key index"""
    def _load_table(self, meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        table = Table(meta["name"], meta["num_columns"], meta["key"])
        table.bufferpool = self.bufferpool
        table.next_rid = meta["next_rid"]
        num_physical_columns = NUM_METADATA_COLUMNS + table.num_columns
        for range_idx, saved in enumerate(meta["page_ranges"]):
            # pages are not read here: the bufferpool loads them from disk on demand
            page_range = PageRange(num_physical_columns, self.bufferpool, table.name, range_idx)
            page_range.base_page_idx = saved["base_page_idx"]
            page_range.base_slot = saved["base_slot"]
            page_range.tail_slot = saved["tail_slot"]
            page_range.num_tail_pages = saved["num_tail_pages"]
            page_range.merged_tails = saved["merged_tails"]
            table.page_ranges.append(page_range)
        table.tps = {(wing, page): tps for wing, page, tps in meta["tps"]}
        table.page_directory = {
            rid: (range_idx, is_tail, page_idx, slot)
            for rid, range_idx, is_tail, page_idx, slot in meta["page_directory"]
        }
        table.index.create_index(table.key)
        return table

    