import sqlite3

class DatabaseManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            cls._instance.connection = sqlite3.connect('database.db', check_same_thread=False)
            cls._instance.connection.execute('PRAGMA foreign_keys = ON')
            cls._instance.connection.execute('PRAGMA journal_mode = WAL')
            cls._instance.connection.execute('PRAGMA synchronous = FULL')
            cls._instance.cursor = cls._instance.connection.cursor()
        return cls._instance

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()

    def execute(self, query, params=None):
        if params:
            return self.cursor.execute(query, params)
        return self.cursor.execute(query)

    def fetchall(self):
        return self.cursor.fetchall()

    def fetchone(self):
        return self.cursor.fetchone()