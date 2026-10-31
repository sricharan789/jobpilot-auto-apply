from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Job, NEW

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid TEXT PRIMARY KEY,
    dedupe_key TEXT,
    source TEXT, external_id TEXT,
    company TEXT, title TEXT, location TEXT,
    url TEXT, apply_url TEXT, description TEXT,
    posted_at TEXT, ats TEXT, ats_meta TEXT, salary TEXT, remote INTEGER,
    status TEXT NOT NULL,
    score INTEGER, assessment TEXT,
    cover_letter TEXT, answers TEXT, screenshot TEXT, error TEXT,
    discovered_at TEXT, updated_at TEXT, applied_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_dedupe ON jobs(dedupe_key);
CREATE TABLE IF NOT EXISTS companies (
    name TEXT PRIMARY KEY, ats TEXT, meta TEXT, checked_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, uid TEXT, kind TEXT, message TEXT
);
"""

JSON_COLS = {"ats_meta", "assessment", "answers"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
        if r is None:
            return None
        d = dict(r)
        for col in JSON_COLS:
            if col in d and d[col]:
                try:
                    d[col] = json.loads(d[col])
                except json.JSONDecodeError:
                    pass
        return d

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, tuple(params))

    # ------------------------------------------------------------ jobs
    def insert_job(self, job: Job) -> bool:
        """Insert if unseen (by uid or dedupe key). Returns True if new."""
        with self.lock:
            if self.conn.execute(
                "SELECT 1 FROM jobs WHERE uid=? OR dedupe_key=?", (job.uid, job.dedupe_key)
            ).fetchone():
                return False
            ts = now()
            self.conn.execute(
                """INSERT INTO jobs (uid, dedupe_key, source, external_id, company, title, location,
                   url, apply_url, description, posted_at, ats, ats_meta, salary, remote, status,
                   discovered_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.uid, job.dedupe_key, job.source, job.external_id, job.company, job.title,
                    job.location, job.url, job.apply_url or job.url, job.description,
                    job.posted_at.isoformat() if job.posted_at else None,
                    job.ats, json.dumps(job.ats_meta), job.salary,
                    None if job.remote is None else int(job.remote), NEW, ts, ts,
                ),
            )
            return True

    def update_job(self, uid: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now()
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(json.dumps(v) if k in JSON_COLS and not isinstance(v, str) and v is not None else v)
        vals.append(uid)
        self.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE uid=?", vals)

    def get_job(self, uid: str) -> dict[str, Any] | None:
        return self._row(self.execute("SELECT * FROM jobs WHERE uid=?", (uid,)).fetchone())

    def jobs_by_status(self, *statuses: str, order: str = "score DESC, discovered_at DESC",
                       limit: int = 500) -> list[dict[str, Any]]:
        q = ",".join("?" * len(statuses))
        rows = self.execute(
            f"SELECT * FROM jobs WHERE status IN ({q}) ORDER BY {order} LIMIT ?", (*statuses, limit)
        ).fetchall()
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.execute(
            "SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()}

    def applied_since(self, iso_ts: str) -> int:
        return self.execute("SELECT COUNT(*) FROM jobs WHERE applied_at >= ?", (iso_ts,)).fetchone()[0]

    # ------------------------------------------------------------ companies
    def get_company(self, name: str) -> dict[str, Any] | None:
        r = self.execute("SELECT * FROM companies WHERE name=?", (name,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["meta"] = json.loads(d["meta"] or "{}")
        return d

    def set_company(self, name: str, ats: str, meta: dict[str, Any]) -> None:
        self.execute(
            "INSERT OR REPLACE INTO companies (name, ats, meta, checked_at) VALUES (?,?,?,?)",
            (name, ats, json.dumps(meta), now()),
        )

    # ------------------------------------------------------------ events
    def log(self, kind: str, message: str, uid: str = "") -> None:
        self.execute("INSERT INTO events (ts, uid, kind, message) VALUES (?,?,?,?)",
                     (now(), uid, kind, message[:2000]))

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in self.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
