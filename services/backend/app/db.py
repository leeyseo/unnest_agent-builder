"""SQLite 저장소: flows, runs, kb 카탈로그, documents, credentials."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    with _lock, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS flows (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, flow_id TEXT, status TEXT,
                started_at TEXT, finished_at TEXT, events TEXT
            );
            CREATE TABLE IF NOT EXISTS kb (
                kb_id TEXT PRIMARY KEY, name TEXT NOT NULL, container_name TEXT,
                bolt_uri TEXT, status TEXT, doc_count INTEGER DEFAULT 0,
                embed_model TEXT, dim INTEGER, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, filename TEXT, path TEXT,
                status TEXT, chunks_written INTEGER DEFAULT 0, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS credentials (
                name TEXT PRIMARY KEY, value_encrypted BLOB NOT NULL, created_at TEXT
            );
            """
        )


def query(sql: str, args: tuple = ()) -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def execute(sql: str, args: tuple = ()) -> None:
    with _lock, _conn() as c:
        c.execute(sql, args)


def save_run(run_id: str, flow_id: str | None, status: str,
             started_at: str, events: list[dict]) -> None:
    execute(
        "INSERT OR REPLACE INTO runs (run_id, flow_id, status, started_at, finished_at, events) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, flow_id, status, started_at, now(), json.dumps(events, ensure_ascii=False, default=str)),
    )
