import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("AUDIT_DB_PATH", "/tmp/infragpt_audit.db"))

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            action      TEXT    NOT NULL,
            resource    TEXT    NOT NULL,
            decision    TEXT    NOT NULL,
            confidence  REAL    NOT NULL DEFAULT 0.0,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource)"
    )
    conn.commit()


def log_decision(
    action: str,
    resource: str,
    decision: str,
    confidence: float = 0.0,
) -> int:
    conn = _get_conn()
    cursor = conn.execute(
        """
        INSERT INTO audit_log (timestamp, action, resource, decision, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        (datetime.utcnow().isoformat(), action, resource, decision, confidence),
    )
    conn.commit()
    return cursor.lastrowid


def get_recent_decisions(limit: int = 50) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_decisions_by_resource(resource: str, limit: int = 20) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT ?",
        (resource, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def get_decisions_by_action(action: str, limit: int = 20) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT ?",
        (action, limit),
    ).fetchall()
    return [dict(row) for row in rows]
