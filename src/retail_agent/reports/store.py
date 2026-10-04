"""The Saved Reports library.

Reports belong to the user who created them: every read and write is filtered by owner, so one
user can never see or delete another user's reports. Deleting is a soft delete, which is what makes
"undo" possible, and every change is written to an audit log.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    delete_batch TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    owner TEXT NOT NULL,
    action TEXT NOT NULL,
    report_ids TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


@dataclass(frozen=True)
class Report:
    id: int
    owner: str
    conversation_id: str
    title: str
    content: str
    created_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ReportStore:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def _reports(self, where: str, params: list) -> list[Report]:
        rows = self._db.execute(
            "SELECT id, owner, conversation_id, title, content, created_at FROM reports "
            f"WHERE {where} ORDER BY id",
            params,
        ).fetchall()
        return [Report(**dict(row)) for row in rows]

    def log(self, owner: str, action: str, report_ids: list[int], detail: str = "") -> None:
        self._db.execute(
            "INSERT INTO audit_log (at, owner, action, report_ids, detail) VALUES (?, ?, ?, ?, ?)",
            (_now(), owner, action, json.dumps(report_ids), detail),
        )
        self._db.commit()

    def save(self, owner: str, conversation_id: str, title: str, content: str) -> Report:
        cursor = self._db.execute(
            "INSERT INTO reports (owner, conversation_id, title, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (owner, conversation_id, title, content, _now()),
        )
        self.log(owner, "save", [cursor.lastrowid], title)
        return self.get(owner, cursor.lastrowid)

    def list(self, owner: str) -> list[Report]:
        return self._reports("owner = ? AND deleted_at IS NULL", [owner])

    def get(self, owner: str, report_id: int) -> Report | None:
        found = self._reports("owner = ? AND id = ? AND deleted_at IS NULL", [owner, report_id])
        return found[0] if found else None

    def find(
        self,
        owner: str,
        *,
        mentioning: str | None = None,
        conversation_id: str | None = None,
        report_ids: list[int] | None = None,
    ) -> list[Report]:
        """The owner's reports matching every filter given. No filter matches all of them."""
        where, params = ["owner = ?", "deleted_at IS NULL"], [owner]
        if mentioning:
            term = mentioning.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')")
            params += [f"%{term}%", f"%{term}%"]
        if conversation_id:
            where.append("conversation_id = ?")
            params.append(conversation_id)
        if report_ids is not None:
            where.append(f"id IN ({', '.join('?' * len(report_ids)) or 'NULL'})")
            params += report_ids
        return self._reports(" AND ".join(where), params)

    def delete(self, owner: str, report_ids: list[int]) -> list[Report]:
        """Soft-delete exactly these reports if they belong to `owner`. Returns what was deleted."""
        doomed = self.find(owner, report_ids=report_ids)
        if not doomed:
            return []
        ids = [r.id for r in doomed]
        batch = f"{owner}:{_now()}:{ids[0]}"
        self._db.execute(
            f"UPDATE reports SET deleted_at = ?, delete_batch = ? "
            f"WHERE owner = ? AND id IN ({', '.join('?' * len(ids))})",
            [_now(), batch, owner, *ids],
        )
        self.log(owner, "delete", ids)
        return doomed

    def restore_last(self, owner: str) -> list[Report]:
        """Undo the owner's most recent delete."""
        row = self._db.execute(
            "SELECT delete_batch FROM reports WHERE owner = ? AND deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC, id DESC LIMIT 1",
            [owner],
        ).fetchone()
        if row is None:
            return []
        batch = row["delete_batch"]
        ids = [
            r["id"]
            for r in self._db.execute("SELECT id FROM reports WHERE delete_batch = ?", [batch])
        ]
        self._db.execute(
            "UPDATE reports SET deleted_at = NULL, delete_batch = NULL WHERE delete_batch = ?",
            [batch],
        )
        self.log(owner, "restore", ids)
        return self.find(owner, report_ids=ids)

    def audit(self, owner: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT at, action, report_ids, detail FROM audit_log WHERE owner = ? ORDER BY id",
            [owner],
        ).fetchall()
        return [{**dict(row), "report_ids": json.loads(row["report_ids"])} for row in rows]
