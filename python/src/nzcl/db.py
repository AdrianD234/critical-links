"""Database access.

A single connection pool, plus schema migration. Everything here speaks psycopg3.

Design note: geometry crosses the boundary as EWKB hex, not WKT. WKT costs
precision and is far larger on the wire; at 375,485 links that matters.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import REPO_ROOT, get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().database_url,
            min_size=1,
            max_size=8,
            kwargs={"autocommit": True},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """A pooled connection with dict rows."""
    with get_pool().connection() as conn:
        conn.row_factory = dict_row
        yield conn


@contextmanager
def direct_connection(autocommit: bool = True) -> Iterator[psycopg.Connection]:
    """A dedicated connection outside the pool, for bulk load and COPY."""
    conn = psycopg.connect(get_settings().database_url, autocommit=autocommit)
    conn.row_factory = dict_row
    try:
        yield conn
    finally:
        conn.close()


def query(sql: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            return cur.fetchall()


def query_one(sql: str, params: Any = None) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Any = None) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def migrate() -> list[str]:
    """Apply every migration in sql/migrations, in filename order.

    The migrations are written to be idempotent (CREATE ... IF NOT EXISTS), so
    re-running is safe and there is no separate applied-migrations ledger to
    drift out of step with reality.
    """
    applied: list[str] = []
    migrations_dir = REPO_ROOT / "sql" / "migrations"
    with direct_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            with conn.cursor() as cur:
                cur.execute(Path(path).read_text(encoding="utf-8"))
            applied.append(path.name)
    return applied


def server_versions() -> dict[str, str]:
    rows = query("SELECT extname, extversion FROM pg_extension ORDER BY extname")
    out = {r["extname"]: r["extversion"] for r in rows}
    pg = query_one("SHOW server_version")
    if pg:
        out["postgresql"] = pg["server_version"]
    return out
