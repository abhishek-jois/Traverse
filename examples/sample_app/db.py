"""Database connection and the ORM base class."""

from config import DATABASE_URL


class Base:
    """Minimal ORM base — models inherit from this."""

    def save(self):
        raise NotImplementedError


class Database:
    def __init__(self, url=DATABASE_URL):
        self.url = url
        self._rows = {}

    def insert(self, table, row):
        self._rows.setdefault(table, []).append(row)
        return row

    def query(self, table):
        return self._rows.get(table, [])


db = Database()
