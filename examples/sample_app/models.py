"""Data models — inherit the ORM Base from db."""

from db import Base, db


class User(Base):
    def __init__(self, username, password_hash):
        self.username = username
        self.password_hash = password_hash

    def save(self):
        return db.insert("users", self)

    @classmethod
    def find(cls, username):
        for row in db.query("users"):
            if row.username == username:
                return row
        return None
