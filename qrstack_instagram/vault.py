import json
import sqlite3
from pathlib import Path
from cryptography.fernet import Fernet


class Vault:
    """Local test vault: encryption key is supplied externally, never stored here."""
    def __init__(self, path, key):
        self.cipher = Fernet(key)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, payload BLOB NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (account TEXT, id TEXT, status TEXT, story TEXT, PRIMARY KEY(account,id))")
        self.db.commit()

    def get(self, account):
        row = self.db.execute("SELECT payload FROM accounts WHERE id=?", (account,)).fetchone()
        if not row:
            return {"account": account, "state": "RECONNECT_REQUIRED", "settings": None}
        value = json.loads(self.cipher.decrypt(row[0]))
        if value["account"] != account:
            raise ValueError("Account binding mismatch")
        return value

    def put(self, account, value):
        value = {**value, "account": account}
        encrypted = self.cipher.encrypt(json.dumps(value).encode())
        with self.db:
            self.db.execute("INSERT INTO accounts VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (account, encrypted))

    def reserve(self, account, job):
        # Transactional account lock and durable job ID survive process restarts.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT 1 FROM jobs WHERE account=? AND (id=? OR status IN ('PENDING','UNKNOWN'))", (account, job)).fetchone():
                raise ValueError("Job already exists or account has an unresolved publication")
            self.db.execute("INSERT INTO jobs VALUES (?,?,'PENDING',NULL)", (account, job))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, account, job, status, story=None):
        with self.db:
            self.db.execute("UPDATE jobs SET status=?,story=? WHERE account=? AND id=?", (status, story, account, job))

    def close(self):
        self.db.close()
