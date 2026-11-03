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

    # Every page in a page range: (is_tail, page_idx, column, page)
    def _pages(self, page_range):
        pages = []

        for page_idx in range(len(page_range.base_pages)):
            for column in range(page_range.num_columns):
                page = page_range.base_pages[page_idx][column]
                pages.append((False, page_idx, column, page))

        for page_idx in range(len(page_range.tail_pages)):
            for column in range(page_range.num_columns):
                page = page_range.tail_pages[page_idx][column]
                pages.append((True, page_idx, column, page))

        return pages

    """ Writes a table's non-empty pages and its meta.json to disk"""
    def _save_table(self, table):
        ranges = []
        for range_idx, page_range in enumerate(table.page_ranges):
            for is_tail, page_idx, column, page in self._pages(page_range):
                if page.num_records == 0:
                    continue
                path = self.bufferpool._path((table.name, range_idx, is_tail, page_idx, column))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(page.to_bytes())
            ranges.append({
                "base_page_idx": page_range.base_page_idx,
                "base_slot": page_range.base_slot,
                "tail_slot": page_range.tail_slot,
                "num_tail_pages": len(page_range.tail_pages),
            })
        meta = {
            "name": table.name,
            "num_columns": table.num_columns,
            "key": table.key,
            "next_rid": table.next_rid,
            "page_ranges": ranges,
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
        table.next_rid = meta["next_rid"]
        num_physical_columns = NUM_METADATA_COLUMNS + table.num_columns
        for range_idx, saved in enumerate(meta["page_ranges"]):
            page_range = PageRange(num_physical_columns)
            page_range.base_page_idx = saved["base_page_idx"]
            page_range.base_slot = saved["base_slot"]
            page_range.tail_slot = saved["tail_slot"]
            page_range.tail_pages = [
                [Page() for _ in range(num_physical_columns)]
                for _ in range(saved["num_tail_pages"])
            ]
            for is_tail, page_idx, column, _ in self._pages(page_range):
                path = self.bufferpool._path((table.name, range_idx, is_tail, page_idx, column))
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        page_range._column_pages(is_tail, page_idx)[column] = Page.from_bytes(f.read())
            table.page_ranges.append(page_range)
        table.page_directory = {
            rid: (range_idx, is_tail, page_idx, slot)
            for rid, range_idx, is_tail, page_idx, slot in meta["page_directory"]
        }
        table.index.create_index(table.key)
        return table

    