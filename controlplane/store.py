import contextlib
import json
import sqlite3
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Transaction:
    def __init__(self, connection):
        self.connection = connection

    def get(self, kind, key):
        row = self.connection.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind, key, value):
        self.connection.execute('INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',
                                (kind, key, encode(value)))

    def list(self, kind):
        return [json.loads(row[0]) for row in self.connection.execute('SELECT body FROM records WHERE kind=? ORDER BY id', (kind,))]

    def emit(self, event_id, event_type, at, data):
        self.connection.execute('INSERT OR IGNORE INTO events(id,type,at,body) VALUES(?,?,?,?)',
                                (event_id, event_type, at, encode(data)))


class Store:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self.connect()) as conn:
            conn.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,type TEXT NOT NULL,at REAL NOT NULL,body TEXT NOT NULL);''')

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('PRAGMA busy_timeout=20000')
        return conn

    @contextlib.contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield Transaction(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, kind, key):
        with contextlib.closing(self.connect()) as conn:
            return Transaction(conn).get(kind, key)

    def list(self, kind):
        with contextlib.closing(self.connect()) as conn:
            return Transaction(conn).list(kind)

    def events(self, after=0, limit=200):
        with contextlib.closing(self.connect()) as conn:
            return [dict(seq=row[0], id=row[1], type=row[2], at=row[3], data=json.loads(row[4]))
                    for row in conn.execute('SELECT seq,id,type,at,body FROM events WHERE seq>? ORDER BY seq LIMIT ?',
                                            (max(0, int(after)), min(500, max(1, int(limit)))))]
