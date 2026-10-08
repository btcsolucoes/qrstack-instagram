import json
import sqlite3
import math
import time
from pathlib import Path
from cryptography.fernet import Fernet
from .pacing import PUBLISH_WINDOW_SECONDS


class AccountChanged(ValueError):
    """Another operation changed the account while a request was in flight."""


class PublicationDeferred(ValueError):
    def __init__(self, seconds):
        self.retry_after_seconds = max(1, math.ceil(seconds))
        super().__init__("Account publication window is not yet available")


class Vault:
    """Local test vault: encryption key is supplied externally, never stored here."""
    def __init__(self, path, key):
        self.path = Path(path).resolve()
        self.cipher = Fernet(key)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, payload BLOB NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (account TEXT, id TEXT, status TEXT, story TEXT, PRIMARY KEY(account,id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS platform_deliveries (id TEXT PRIMARY KEY, payload BLOB NOT NULL)")
        # Old jobs have no timestamps. Give every legacy account a full window
        # once on upgrade, rather than assuming it has not recently published.
        self.db.execute("CREATE TABLE IF NOT EXISTS publication_windows (account TEXT PRIMARY KEY, next_publish_at REAL NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO publication_windows SELECT DISTINCT account, ? FROM jobs",
                        (time.time() + PUBLISH_WINDOW_SECONDS,))
        self.db.commit()

    def get(self, account):
        row = self.db.execute("SELECT payload FROM accounts WHERE id=?", (account,)).fetchone()
        if not row:
            return {"account": account, "state": "RECONNECT_REQUIRED", "settings": None}
        value = json.loads(self.cipher.decrypt(row[0]))
        if value["account"] != account:
            raise ValueError("Account binding mismatch")
        return value

    def _write(self, account, value):
        encrypted = self.cipher.encrypt(json.dumps(value).encode())
        self.db.execute("INSERT INTO accounts VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (account, encrypted))

    def put(self, account, value, *, expected_revision=None, merge=False):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.get(account)
            revision = current.get("revision", 0)
            if expected_revision is not None and revision != expected_revision:
                raise AccountChanged("Account changed; review before continuing")
            value = {**(current if merge else {}), **value,
                     "account": account, "revision": revision + 1}
            self._write(account, value)
            self.db.commit()
            return value
        except BaseException:
            self.db.rollback()
            raise

    def get_job(self, account, job):
        row = self.db.execute("SELECT status,story FROM jobs WHERE account=? AND id=?", (account, job)).fetchone()
        return {"status": row[0], "story": row[1]} if row else None

    def has_unresolved_job(self, account):
        return self.db.execute("SELECT 1 FROM jobs WHERE account=? AND status IN ('PENDING','UNKNOWN')", (account,)).fetchone() is not None

    def publication_delay(self, account, *, now=None):
        row = self.db.execute("SELECT next_publish_at FROM publication_windows WHERE account=?", (account,)).fetchone()
        return max(0, math.ceil(row[0] - (time.time() if now is None else now))) if row else 0

    def reserve(self, account, job):
        # Transactional account lock and durable job ID survive process restarts.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT 1 FROM jobs WHERE account=? AND (id=? OR status IN ('PENDING','UNKNOWN'))", (account, job)).fetchone():
                raise ValueError("Job already exists or account has an unresolved publication")
            now = time.time()
            delay = self.publication_delay(account, now=now)
            if delay:
                raise PublicationDeferred(delay)
            self.db.execute("INSERT INTO jobs VALUES (?,?,'PENDING',NULL)", (account, job))
            self.db.execute("INSERT INTO publication_windows VALUES (?,?) ON CONFLICT(account) DO UPDATE SET next_publish_at=excluded.next_publish_at",
                            (account, now + PUBLISH_WINDOW_SECONDS))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, account, job, status, story=None):
        with self.db:
            self.db.execute("UPDATE jobs SET status=?,story=? WHERE account=? AND id=?", (status, story, account, job))
            if status == "PUBLISHED":
                self.db.execute("UPDATE publication_windows SET next_publish_at=MAX(next_publish_at, ?) WHERE account=?",
                                (time.time() + PUBLISH_WINDOW_SECONDS, account))

    def platform_deliveries(self, scope):
        """Claim tokens are credentials: encrypt the complete delivery journal."""
        values = []
        for row in self.db.execute("SELECT id,payload FROM platform_deliveries ORDER BY rowid"):
            value = json.loads(self.cipher.decrypt(row[1]))
            if value["id"] != row[0]:
                raise ValueError("Platform delivery binding mismatch")
            if value["scope"] == scope and not value.get("acked"):
                values.append(value)
        return values

    def save_platform_delivery(self, value, *, new=False):
        payload = self.cipher.encrypt(json.dumps(value).encode())
        with self.db:
            if new:
                self.db.execute("INSERT INTO platform_deliveries VALUES (?,?)", (value["id"], payload))
            else:
                updated = self.db.execute("UPDATE platform_deliveries SET payload=? WHERE id=?", (payload, value["id"]))
                if updated.rowcount != 1:
                    raise ValueError("Unknown platform delivery")

    def close(self):
        self.db.close()
