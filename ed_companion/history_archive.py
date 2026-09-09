"""Profile-bound, lossless history storage for large runtime datasets."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


class HistoryArchive:
    """Keep displaced records queryable without loading them at app startup."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._counts_cache = None
        self._ensure_schema()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _ensure_schema(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    category TEXT NOT NULL,
                    record_key TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (category, record_key)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS history_category_observed
                ON history(category, observed_at)
            """)
            connection.commit()

    @staticmethod
    def _encoded(record):
        return json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _record_key(record, encoded, key_field):
        explicit = str(record.get(key_field) or "") if key_field else ""
        return explicit or hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def archive(self, category, records: Iterable[dict], key_field=""):
        """Archive every valid record in one durable transaction."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = []
        occurrences = {}
        for record in records or []:
            if not isinstance(record, dict):
                continue
            encoded = self._encoded(record)
            record_key = self._record_key(record, encoded, key_field)
            if not key_field:
                occurrence = occurrences.get(record_key, 0)
                occurrences[record_key] = occurrence + 1
                if occurrence:
                    record_key = f"{record_key}:{occurrence}"
            observed = str(
                record.get("sent_at") or record.get("signal_timestamp")
                or record.get("received_at") or record.get("observedAt")
                or record.get("created") or ""
            )
            rows.append((
                str(category), record_key,
                observed, now, encoded,
            ))
        if not rows:
            return 0
        with self._lock, closing(self._connect()) as connection:
            connection.executemany("""
                INSERT INTO history(
                    category, record_key, observed_at, archived_at, payload
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(category, record_key) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    archived_at=excluded.archived_at,
                    payload=excluded.payload
            """, rows)
            connection.commit()
        self._counts_cache = None
        return len(rows)

    def count(self, category=None):
        with self._lock, closing(self._connect()) as connection:
            if category is None:
                row = connection.execute("SELECT COUNT(*) FROM history").fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) FROM history WHERE category=?", (category,)
                ).fetchone()
        return int(row[0] if row else 0)

    def counts(self):
        with self._lock:
            if self._counts_cache is not None:
                return dict(self._counts_cache)
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute("""
                SELECT category, COUNT(*) FROM history GROUP BY category
                ORDER BY category
            """).fetchall()
            self._counts_cache = {
                str(category): int(count) for category, count in rows
            }
            return dict(self._counts_cache)

    def records(self, category, limit=0):
        """Return one category in chronological order, optionally tail-limited."""
        with self._lock, closing(self._connect()) as connection:
            if limit and int(limit) > 0:
                rows = connection.execute("""
                    SELECT payload FROM (
                        SELECT payload, observed_at, record_key
                        FROM history WHERE category=?
                        ORDER BY observed_at DESC, record_key DESC LIMIT ?
                    ) ORDER BY observed_at, record_key
                """, (str(category), int(limit))).fetchall()
            else:
                rows = connection.execute("""
                    SELECT payload FROM history WHERE category=?
                    ORDER BY observed_at, record_key
                """, (str(category),)).fetchall()
        result = []
        for (payload,) in rows:
            try:
                record = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if isinstance(record, dict):
                result.append(record)
        return result

    def clear(self, category):
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM history WHERE category=?", (str(category),)
            )
            connection.commit()
        self._counts_cache = None
        return max(0, int(cursor.rowcount or 0))

    def export_json(self, path, active=None):
        """Create a complete, portable JSON export without loading it all in RAM."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with self._lock, closing(self._connect()) as connection, temporary.open(
            "w", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write('{\n  "format": "EDOPS_HISTORY_V1",\n  "records": [\n')
            first = True
            for category, observed_at, archived_at, payload in connection.execute("""
                SELECT category, observed_at, archived_at, payload
                FROM history ORDER BY category, observed_at, record_key
            """):
                if not first:
                    handle.write(",\n")
                first = False
                envelope = {
                    "category": category,
                    "observedAt": observed_at,
                    "archivedAt": archived_at,
                    "data": json.loads(payload),
                }
                handle.write("    " + json.dumps(envelope, ensure_ascii=False))
            for category, records in (active or {}).items():
                for record in records or []:
                    if not isinstance(record, dict):
                        continue
                    if not first:
                        handle.write(",\n")
                    first = False
                    envelope = {
                        "category": category,
                        "observedAt": str(
                            record.get("sent_at")
                            or record.get("signal_timestamp")
                            or record.get("received_at")
                            or record.get("observedAt") or ""
                        ),
                        "archivedAt": "",
                        "active": True,
                        "data": record,
                    }
                    handle.write("    " + json.dumps(envelope, ensure_ascii=False))
            handle.write("\n  ]\n}\n")
            handle.flush()
        temporary.replace(destination)
        return destination
