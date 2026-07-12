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
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                    content_hash TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS evidence_case_hash
                    ON evidence(case_id, content_hash);
                CREATE INDEX IF NOT EXISTS evidence_case_status
                    ON evidence(case_id, status, published_at DESC);

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
            "status": payload.get("status") or ("pending" if origin == "automatic" else "accepted"),
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
                row = connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM evidence WHERE case_id = ? AND content_hash = ?",
                    (case_id, content_hash),
                ).fetchone()
        if row is None:
            raise RuntimeError("evidence insert failed")
        return self._evidence_row(row), created

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        return self._evidence_row(row) if row else None

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
        return [self._evidence_row(row) for row in rows]

    def update_evidence(self, evidence_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        clean = {key: value for key, value in updates.items() if key in EVIDENCE_FIELDS}
        if not clean:
            return self.get_evidence(evidence_id)
        clean["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in clean)
        with self.session() as connection:
            cursor = connection.execute(
                f"UPDATE evidence SET {assignments} WHERE id = ?", (*clean.values(), evidence_id)
            )
            if cursor.rowcount == 0:
                return None
        return self.get_evidence(evidence_id)

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
    def _evidence_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        return result
