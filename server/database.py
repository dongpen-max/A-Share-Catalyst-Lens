from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CASE_FIELDS = {"stock_code", "company", "event_title", "event_type", "event_date", "query"}
EVIDENCE_FIELDS = {
    "source_type",
    "source_name",
    "title",
    "url",
    "published_at",
    "quote",
    "claim",
    "direction",
    "reliability",
    "relevance",
    "freshness",
    "materiality",
    "immediacy",
    "novelty",
    "market_alignment",
    "priced_in_risk",
    "counterevidence",
    "status",
    "review_note",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_utc(value: str | datetime) -> str:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("audit timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def session(self):
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    company TEXT NOT NULL DEFAULT '',
                    event_title TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL DEFAULT 'policy',
                    event_date TEXT,
                    query TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    origin TEXT NOT NULL CHECK(origin IN ('automatic', 'manual')),
                    source_type TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    published_at TEXT,
                    fetched_at TEXT NOT NULL,
                    quote TEXT NOT NULL DEFAULT '',
                    claim TEXT NOT NULL DEFAULT '',
                    direction TEXT NOT NULL DEFAULT 'neutral',
                    reliability REAL NOT NULL DEFAULT 0,
                    relevance REAL NOT NULL DEFAULT 0,
                    freshness REAL NOT NULL DEFAULT 0,
                    materiality REAL NOT NULL DEFAULT 0,
                    immediacy REAL NOT NULL DEFAULT 0,
                    novelty REAL NOT NULL DEFAULT 0,
                    market_alignment REAL NOT NULL DEFAULT 0,
                    priced_in_risk REAL NOT NULL DEFAULT 0,
                    counterevidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'rejected')),
                    reviewed_at TEXT,
                    review_note TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS evidence_case_hash
                    ON evidence(case_id, content_hash);
                CREATE INDEX IF NOT EXISTS evidence_case_status
                    ON evidence(case_id, status, published_at DESC);

                CREATE TABLE IF NOT EXISTS evidence_review_history (
                    id TEXT PRIMARY KEY,
                    evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
                    action TEXT NOT NULL CHECK(action IN ('created', 'status_changed', 'note_updated')),
                    from_status TEXT CHECK(from_status IN ('pending', 'accepted', 'rejected')),
                    to_status TEXT NOT NULL CHECK(to_status IN ('pending', 'accepted', 'rejected')),
                    review_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS evidence_review_history_evidence_created
                    ON evidence_review_history(evidence_id, created_at, id);

                CREATE TABLE IF NOT EXISTS score_runs (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    catalyst_score REAL NOT NULL,
                    evidence_confidence REAL NOT NULL,
                    coverage_score REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS score_runs_case_created
                    ON score_runs(case_id, created_at DESC);
                """
            )
            evidence_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(evidence)")
            }
            if "reviewed_at" not in evidence_columns:
                connection.execute("ALTER TABLE evidence ADD COLUMN reviewed_at TEXT")
            if "review_note" not in evidence_columns:
                connection.execute(
                    "ALTER TABLE evidence ADD COLUMN review_note TEXT NOT NULL DEFAULT ''"
                )
            self._backfill_review_history(connection)

    def create_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        case_id = str(uuid.uuid4())
        now = utc_now()
        values = {field: payload.get(field) for field in CASE_FIELDS}
        with self.session() as connection:
            connection.execute(
                """
                INSERT INTO cases (
                    id, stock_code, company, event_title, event_type,
                    event_date, query, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    values["stock_code"],
                    values.get("company") or "",
                    values.get("event_title") or "",
                    values.get("event_type") or "policy",
                    values.get("event_date"),
                    values.get("query") or "",
                    now,
                    now,
                ),
            )
        return self.get_case(case_id)  # type: ignore[return-value]

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return dict(row) if row else None

    def list_cases(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT * FROM cases ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def update_case(self, case_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        clean = {key: value for key, value in updates.items() if key in CASE_FIELDS}
        if not clean:
            return self.get_case(case_id)
        clean["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in clean)
        with self.session() as connection:
            cursor = connection.execute(
                f"UPDATE cases SET {assignments} WHERE id = ?", (*clean.values(), case_id)
            )
            if cursor.rowcount == 0:
                return None
        return self.get_case(case_id)

    def delete_case(self, case_id: str) -> bool:
        with self.session() as connection:
            cursor = connection.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        return cursor.rowcount > 0

    def add_evidence(
        self,
        case_id: str,
        payload: dict[str, Any],
        *,
        origin: str,
    ) -> tuple[dict[str, Any], bool]:
        evidence_id = payload.get("id") or str(uuid.uuid4())
        now = utc_now()
        status_value = (
            "pending"
            if origin == "automatic"
            else (payload.get("status") or "accepted")
        )
        provided_history = payload.get("review_history") or []
        latest_review = next(
            (
                entry
                for entry in reversed(provided_history)
                if entry.get("action") != "created"
            ),
            None,
        )
        review_note = str(
            payload.get("review_note")
            or (latest_review or {}).get("review_note")
            or ""
        )
        reviewed_at_value = (
            payload.get("reviewed_at")
            or (latest_review or {}).get("created_at")
            or (now if origin == "manual" and status_value != "pending" else None)
        )
        reviewed_at = canonical_utc(reviewed_at_value) if reviewed_at_value else None
        content_hash = payload.get("content_hash") or hashlib.sha256(
            f"{payload.get('source_name', '')}|{payload.get('url', '')}|{payload.get('title', '')}".encode(
                "utf-8"
            )
        ).hexdigest()
        metadata = payload.get("metadata") or {}
        values = {
            "id": evidence_id,
            "case_id": case_id,
            "origin": origin,
            "source_type": payload.get("source_type") or "other",
            "source_name": payload.get("source_name") or ("自动发现" if origin == "automatic" else "手动输入"),
            "title": payload.get("title") or "未命名证据",
            "url": payload.get("url") or "",
            "published_at": payload.get("published_at"),
            "fetched_at": payload.get("fetched_at") or now,
            "quote": payload.get("quote") or "",
            "claim": payload.get("claim") or "",
            "direction": payload.get("direction") or "neutral",
            "reliability": float(payload.get("reliability") or 0),
            "relevance": float(payload.get("relevance") or 0),
            "freshness": float(payload.get("freshness") or 0),
            "materiality": float(payload.get("materiality") or 0),
            "immediacy": float(payload.get("immediacy") or 0),
            "novelty": float(payload.get("novelty") or 0),
            "market_alignment": float(payload.get("market_alignment") or 0),
            "priced_in_risk": float(payload.get("priced_in_risk") or 0),
            "counterevidence": float(payload.get("counterevidence") or 0),
            "status": status_value,
            "reviewed_at": reviewed_at,
            "review_note": review_note,
            "content_hash": content_hash,
            "metadata_json": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            "created_at": now,
            "updated_at": now,
        }
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        with self.session() as connection:
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO evidence ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
            created = cursor.rowcount > 0
            if created:
                self._insert_review_history(
                    connection,
                    evidence_id,
                    status=status_value,
                    review_note=review_note,
                    created_at=now,
                    provided_history=provided_history,
                )
                row = connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM evidence WHERE case_id = ? AND content_hash = ?",
                    (case_id, content_hash),
                ).fetchone()
            history = self._load_review_history(connection, row["id"]) if row else []
        if row is None:
            raise RuntimeError("evidence insert failed")
        return self._evidence_row(row, history), created

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
            history = self._load_review_history(connection, evidence_id) if row else []
        return self._evidence_row(row, history) if row else None

    def list_evidence(self, case_id: str) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evidence
                WHERE case_id = ?
                ORDER BY
                    CASE status WHEN 'pending' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END,
                    COALESCE(published_at, created_at) DESC
                """,
                (case_id,),
            ).fetchall()
            histories = {
                row["id"]: self._load_review_history(connection, row["id"])
                for row in rows
            }
        return [self._evidence_row(row, histories[row["id"]]) for row in rows]

    def update_evidence(self, evidence_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        clean = {key: value for key, value in updates.items() if key in EVIDENCE_FIELDS}
        if not clean:
            return self.get_evidence(evidence_id)
        with self.session() as connection:
            # Serialize review writes before reading the current status so the
            # audit chain stays contiguous across tabs and API clients.
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
            if current is None:
                return None

            now = utc_now()
            review_note_supplied = "review_note" in clean
            requested_note = str(clean.pop("review_note", "") or "")
            previous_status = current["status"]
            next_status = clean.get("status", previous_status)
            status_changed = next_status != previous_status
            note_changed = review_note_supplied and requested_note != current["review_note"]
            review_action = (
                "status_changed"
                if status_changed
                else ("note_updated" if note_changed else None)
            )

            if review_action:
                clean["reviewed_at"] = now
                clean["review_note"] = requested_note if review_note_supplied else ""
            if not clean:
                history = self._load_review_history(connection, evidence_id)
                return self._evidence_row(current, history)

            clean["updated_at"] = now
            assignments = ", ".join(f"{key} = ?" for key in clean)
            cursor = connection.execute(
                f"UPDATE evidence SET {assignments} WHERE id = ?", (*clean.values(), evidence_id)
            )
            if cursor.rowcount == 0:
                return None
            if review_action:
                self._append_review_history(
                    connection,
                    evidence_id,
                    action=review_action,
                    from_status=previous_status,
                    to_status=next_status,
                    review_note=clean["review_note"],
                    created_at=now,
                )
            row = connection.execute(
                "SELECT * FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
            history = self._load_review_history(connection, evidence_id)
        return self._evidence_row(row, history) if row else None

    def save_score(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        created_at = utc_now()
        with self.session() as connection:
            connection.execute(
                """
                INSERT INTO score_runs (
                    id, case_id, catalyst_score, evidence_confidence,
                    coverage_score, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    case_id,
                    payload["catalyst_score"],
                    payload["evidence_confidence"],
                    payload["coverage_score"],
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    created_at,
                ),
            )
        return {"id": run_id, "case_id": case_id, "created_at": created_at, **payload}

    @staticmethod
    def _evidence_row(
        row: sqlite3.Row, review_history: list[dict[str, Any]]
    ) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        result["review_history"] = review_history
        return result

    @staticmethod
    def _load_review_history(
        connection: sqlite3.Connection, evidence_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT id, action, from_status, to_status, review_note, created_at
            FROM evidence_review_history
            WHERE evidence_id = ?
            ORDER BY rowid
            """,
            (evidence_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _append_review_history(
        connection: sqlite3.Connection,
        evidence_id: str,
        *,
        action: str,
        from_status: str | None,
        to_status: str,
        review_note: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO evidence_review_history (
                id, evidence_id, action, from_status, to_status,
                review_note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                evidence_id,
                action,
                from_status,
                to_status,
                review_note,
                canonical_utc(created_at),
            ),
        )

    def _insert_review_history(
        self,
        connection: sqlite3.Connection,
        evidence_id: str,
        *,
        status: str,
        review_note: str,
        created_at: str,
        provided_history: list[dict[str, Any]],
    ) -> None:
        if provided_history:
            for entry in provided_history:
                self._append_review_history(
                    connection,
                    evidence_id,
                    action=entry["action"],
                    from_status=entry.get("from_status"),
                    to_status=entry["to_status"],
                    review_note=str(entry.get("review_note") or ""),
                    created_at=entry.get("created_at") or created_at,
                )
            return
        self._append_review_history(
            connection,
            evidence_id,
            action="created",
            from_status=None,
            to_status=status,
            review_note=review_note,
            created_at=created_at,
        )

    def _backfill_review_history(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT evidence.id, evidence.status, evidence.review_note, evidence.created_at
            FROM evidence
            WHERE NOT EXISTS (
                SELECT 1 FROM evidence_review_history
                WHERE evidence_review_history.evidence_id = evidence.id
            )
            """
        ).fetchall()
        for row in rows:
            self._append_review_history(
                connection,
                row["id"],
                action="created",
                from_status=None,
                to_status=row["status"],
                review_note=row["review_note"] or "",
                created_at=row["created_at"],
            )
