import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, created REAL, result TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, repo TEXT, number INTEGER, head TEXT,
            status TEXT DEFAULT 'queued', attempts INTEGER DEFAULT 0, error TEXT, review_id TEXT, updated REAL);""")
            db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, result):
        key = uuid.uuid4().hex
        with self.db() as db:
            db.execute("INSERT INTO reviews VALUES(?,?,?)", (key, time.time(), json.dumps(result)))
        return key

    def get(self, key):
        with self.db() as db:
            r = db.execute("SELECT result FROM reviews WHERE id=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None

    def recent(self):
        with self.db() as db:
            rows = db.execute(
                "SELECT id,created,result FROM reviews ORDER BY created DESC LIMIT 30"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "created": r["created"],
                "summary": json.loads(r["result"])["review"]["summary"],
                "findings": len(json.loads(r["result"])["review"]["findings"]),
            }
            for r in rows
        ]

    def enqueue(self, repo, number, head):
        key = hashlib.sha256(f"{repo.lower()}:{number}:{head}".encode()).hexdigest()
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO jobs(id,repo,number,head,updated) VALUES(?,?,?,?,?)",
                (key, repo, number, head, time.time()),
            )
        return key

    def claim(self):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY updated LIMIT 1"
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE jobs SET status='running',attempts=attempts+1,updated=? WHERE id=?",
                    (time.time(), row["id"]),
                )
        return dict(row) if row else None

    def finish(self, key, status, review_id=None, error=None):
        with self.db() as db:
            db.execute(
                "UPDATE jobs SET status=?,review_id=?,error=?,updated=? WHERE id=?",
                (status, review_id, error, time.time(), key),
            )

    def jobs(self):
        with self.db() as db:
            return [
                dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY updated DESC LIMIT 30")
            ]

    def retry(self, key):
        with self.db() as db:
            return bool(
                db.execute(
                    "UPDATE jobs SET status='queued',error=NULL,updated=? WHERE id=? AND status='failed' AND attempts<3",
                    (time.time(), key),
                ).rowcount
            )
