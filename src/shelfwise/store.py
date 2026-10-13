"""SQLite persistence: import batches, product records (as JSON documents) and an append-only audit log."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shelfwise.catalog.models import ProductRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES batches(id),
    status TEXT NOT NULL,
    position INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS products_batch ON products(batch_id, position);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    product_id TEXT,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL" if self.path != ":memory:" else "PRAGMA journal_mode=MEMORY")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- batches
    def create_batch(self, name: str, data: dict[str, Any]) -> str:
        batch_id = "b_" + uuid.uuid4().hex[:10]
        with self._tx() as c:
            c.execute("INSERT INTO batches VALUES (?, ?, ?, ?)", (batch_id, name, now(), json.dumps(data, ensure_ascii=False)))
        return batch_id

    def batches(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT b.id, b.name, b.created_at, b.data, COUNT(p.id) AS products FROM batches b "
                "LEFT JOIN products p ON p.batch_id = b.id GROUP BY b.id ORDER BY b.created_at DESC"
            ).fetchall()
        return [{"id": r["id"], "name": r["name"], "created_at": r["created_at"], "products": r["products"],
                 **json.loads(r["data"])} for r in rows]

    def batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if r is None:
            return None
        return {"id": r["id"], "name": r["name"], "created_at": r["created_at"], **json.loads(r["data"])}

    def latest_batch_id(self) -> str | None:
        with self._lock:
            r = self._conn.execute("SELECT id FROM batches ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
        return str(r["id"]) if r else None

    # ---- products
    def save(self, record: ProductRecord, position: int | None = None) -> None:
        data = record.model_dump_json()
        with self._tx() as c:
            if position is None:
                row = c.execute("SELECT position FROM products WHERE id = ?", (record.id,)).fetchone()
                if row is not None:
                    position = int(row["position"])
                else:
                    position = int(c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM products").fetchone()[0])
            c.execute(
                "INSERT INTO products (id, batch_id, status, position, updated_at, data) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at, "
                "data = excluded.data, batch_id = excluded.batch_id",
                (record.id, record.batch_id, record.status, position, now(), data),
            )

    def get(self, product_id: str) -> ProductRecord | None:
        with self._lock:
            r = self._conn.execute("SELECT data FROM products WHERE id = ?", (product_id,)).fetchone()
        return ProductRecord.model_validate_json(r["data"]) if r else None

    def products(self, batch_id: str | None = None, status: str | None = None) -> list[ProductRecord]:
        query = "SELECT data FROM products"
        args: list[Any] = []
        where = []
        if batch_id:
            where.append("batch_id = ?")
            args.append(batch_id)
        if status:
            where.append("status = ?")
            args.append(status)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY position"
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return [ProductRecord.model_validate_json(r["data"]) for r in rows]

    def status_counts(self, batch_id: str | None = None) -> dict[str, int]:
        query = "SELECT status, COUNT(*) AS n FROM products"
        args: list[Any] = []
        if batch_id:
            query += " WHERE batch_id = ?"
            args.append(batch_id)
        query += " GROUP BY status"
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def delete_batch(self, batch_id: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM products WHERE batch_id = ?", (batch_id,))
            c.execute("DELETE FROM batches WHERE id = ?", (batch_id,))

    # ---- audit log
    def log(self, action: str, detail: dict[str, Any], *, actor: str = "system", product_id: str | None = None) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO events (at, actor, action, product_id, detail) VALUES (?, ?, ?, ?, ?)",
                      (now(), actor, action, product_id, json.dumps(detail, ensure_ascii=False)))

    def events(self, product_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM events"
        args: list[Any] = []
        if product_id:
            query += " WHERE product_id = ?"
            args.append(product_id)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return [{"id": r["id"], "at": r["at"], "actor": r["actor"], "action": r["action"], "product_id": r["product_id"],
                 "detail": json.loads(r["detail"])} for r in rows]

    # ---- background jobs
    def put_job(self, job_id: str, kind: str, status: str, data: dict[str, Any]) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data",
                (job_id, kind, status, now(), json.dumps(data, ensure_ascii=False)),
            )

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if r is None:
            return None
        return {"id": r["id"], "kind": r["kind"], "status": r["status"], "created_at": r["created_at"], **json.loads(r["data"])}

    def jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "kind": r["kind"], "status": r["status"], "created_at": r["created_at"], **json.loads(r["data"])} for r in rows]
