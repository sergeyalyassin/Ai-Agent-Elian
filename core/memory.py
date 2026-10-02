from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

class PersistentMemory:
    """Local durable episodic/semantic memory with SQLite FTS and JSON-compatible records."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS memories(
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                created REAL NOT NULL,
                importance REAL NOT NULL DEFAULT 0.5,
                source TEXT NOT NULL DEFAULT ''
            )""")
            c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                         USING fts5(id UNINDEXED, text, tags)""")
            c.commit()

    def add(self, kind: str, text: str, tags: list[str] | None = None,
            importance: float = 0.5, source: str = "") -> dict[str, Any]:
        value = str(text)[:12000]
        tags = [str(x)[:80] for x in (tags or [])[:30]]
        digest = hashlib.sha256(
            (kind + "|" + value).encode("utf-8", "ignore")
        ).hexdigest()[:24]
        entry = {
            "id": f"{kind}-{digest}",
            "kind": kind,
            "text": value,
            "tags": tags,
            "created": time.time(),
            "importance": max(0.0, min(1.0, float(importance))),
            "source": str(source)[:200],
        }
        with self._lock, self._connect() as c:
            c.execute("INSERT OR REPLACE INTO memories VALUES (?,?,?,?,?,?,?)",
                      (entry["id"], entry["kind"], entry["text"],
                       json.dumps(tags, ensure_ascii=False), entry["created"],
                       entry["importance"], entry["source"]))
            c.execute("DELETE FROM memories_fts WHERE id=?", (entry["id"],))
            c.execute("INSERT INTO memories_fts(id,text,tags) VALUES (?,?,?)",
                      (entry["id"], entry["text"], " ".join(tags)))
            c.commit()
        return entry

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        query = str(query).strip()
        if not query:
            return []
        words = re.findall(r"[\w\u0600-\u06ff]+", query.lower())
        if not words:
            return []
        fts = " OR ".join('"' + w.replace('"', ' ') + '"' for w in words[:12])
        with self._lock, self._connect() as c:
            try:
                rows = c.execute(
                    """SELECT m.* FROM memories m
                       JOIN memories_fts f ON f.id=m.id
                       WHERE memories_fts MATCH ?
                       ORDER BY bm25(memories_fts), m.importance DESC, m.created DESC
                       LIMIT ?""", (fts, int(limit))
                ).fetchall()
            except sqlite3.OperationalError:
                like = "%" + "%".join(words[:5]) + "%"
                rows = c.execute(
                    "SELECT * FROM memories WHERE lower(text) LIKE ? ORDER BY importance DESC, created DESC LIMIT ?",
                    (like, int(limit))
                ).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM memories ORDER BY created DESC LIMIT ?",
                (int(limit),)
            ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row):
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        return d
