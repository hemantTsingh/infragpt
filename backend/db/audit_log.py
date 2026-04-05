"""
Audit log — backed by PostgreSQL in production, SQLite fallback for local dev.

All writes return the inserted row id (used as audit_id in API responses).
The connection pool is created lazily on first use so that test code that
mocks init_db / log_decision never triggers a real DB connection.
"""
import logging
import os
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# ── pool (PostgreSQL) ─────────────────────────────────────────────────────────

_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                import psycopg2.pool
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=10,
                    dsn=os.environ["DATABASE_URL"],
                )
    return _pool


# ── SQLite fallback (local dev without DATABASE_URL) ─────────────────────────

_sqlite_conn = None
_sqlite_lock = threading.Lock()


def _get_sqlite():
    global _sqlite_conn
    if _sqlite_conn is None:
        with _sqlite_lock:
            if _sqlite_conn is None:
                import sqlite3
                from pathlib import Path
                db_path = Path(os.environ.get("AUDIT_DB_PATH", "/tmp/infragpt_audit.db"))
                db_path.parent.mkdir(parents=True, exist_ok=True)
                _sqlite_conn = sqlite3.connect(str(db_path), check_same_thread=False)
                _sqlite_conn.row_factory = sqlite3.Row
    return _sqlite_conn


def _use_postgres() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


# ── schema ────────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    timestamp   TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    resource    TEXT    NOT NULL,
    decision    TEXT    NOT NULL,
    confidence  REAL    NOT NULL DEFAULT 0.0,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

_CREATE_IDX_ACTION   = "CREATE INDEX IF NOT EXISTS idx_audit_action   ON audit_log(action);"
_CREATE_IDX_RESOURCE = "CREATE INDEX IF NOT EXISTS idx_audit_resource  ON audit_log(resource);"

_CREATE_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    resource    TEXT    NOT NULL,
    decision    TEXT    NOT NULL,
    confidence  REAL    NOT NULL DEFAULT 0.0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db():
    if _use_postgres():
        pool = _get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TABLE)
                cur.execute(_CREATE_IDX_ACTION)
                cur.execute(_CREATE_IDX_RESOURCE)
            conn.commit()
            logger.info("audit_log: PostgreSQL table ready")
        finally:
            pool.putconn(conn)
    else:
        conn = _get_sqlite()
        conn.execute(_CREATE_TABLE_SQLITE)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action   ON audit_log(action);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource  ON audit_log(resource);")
        conn.commit()
        logger.info("audit_log: SQLite table ready (local dev)")


# ── writes ────────────────────────────────────────────────────────────────────

def log_decision(
    action: str,
    resource: str,
    decision: str,
    confidence: float = 0.0,
) -> int:
    ts = datetime.utcnow().isoformat()

    if _use_postgres():
        pool = _get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_log (timestamp, action, resource, decision, confidence)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (ts, action, resource, decision, confidence),
                )
                row_id = cur.fetchone()[0]
            conn.commit()
            return row_id
        finally:
            pool.putconn(conn)
    else:
        conn = _get_sqlite()
        cur = conn.execute(
            "INSERT INTO audit_log (timestamp, action, resource, decision, confidence) VALUES (?, ?, ?, ?, ?)",
            (ts, action, resource, decision, confidence),
        )
        conn.commit()
        return cur.lastrowid


# ── reads ─────────────────────────────────────────────────────────────────────

def get_recent_decisions(limit: int = 50) -> list[dict]:
    if _use_postgres():
        pool = _get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, timestamp, action, resource, decision, confidence FROM audit_log ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            pool.putconn(conn)
    else:
        conn = _get_sqlite()
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_decisions_by_resource(resource: str, limit: int = 20) -> list[dict]:
    if _use_postgres():
        pool = _get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, timestamp, action, resource, decision, confidence FROM audit_log WHERE resource = %s ORDER BY id DESC LIMIT %s",
                    (resource, limit),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            pool.putconn(conn)
    else:
        conn = _get_sqlite()
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT ?",
            (resource, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def get_decisions_by_action(action: str, limit: int = 20) -> list[dict]:
    if _use_postgres():
        pool = _get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, timestamp, action, resource, decision, confidence FROM audit_log WHERE action = %s ORDER BY id DESC LIMIT %s",
                    (action, limit),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            pool.putconn(conn)
    else:
        conn = _get_sqlite()
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT ?",
            (action, limit),
        ).fetchall()
        return [dict(row) for row in rows]
