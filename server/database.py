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
WATCHLIST_FIELDS = {"company", "enabled", "sort_order"}
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


class MonitorFindingConversionNotFoundError(LookupError):
    pass


class MonitorFindingConversionConflictError(ValueError):
    pass


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

                CREATE TABLE IF NOT EXISTS watchlist_items (
                    id TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL UNIQUE,
                    company TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    sort_order INTEGER NOT NULL CHECK(sort_order >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS watchlist_items_sort
                    ON watchlist_items(sort_order, created_at, id);

                CREATE TABLE IF NOT EXISTS monitor_runs (
                    id TEXT PRIMARY KEY,
                    trigger TEXT NOT NULL CHECK(trigger = 'manual'),
                    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'partial', 'failed')),
                    provider TEXT NOT NULL,
                    requested_count INTEGER NOT NULL CHECK(requested_count >= 0),
                    success_count INTEGER NOT NULL DEFAULT 0 CHECK(success_count >= 0),
                    failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
                    errors_json TEXT NOT NULL DEFAULT '[]',
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS monitor_runs_started
                    ON monitor_runs(started_at DESC, id);

                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES monitor_runs(id) ON DELETE CASCADE,
                    watchlist_item_id TEXT REFERENCES watchlist_items(id) ON DELETE SET NULL,
                    stock_code TEXT NOT NULL,
                    company TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL,
                    price REAL,
                    change_percent REAL,
                    volume REAL,
                    turnover REAL,
                    fetched_at TEXT NOT NULL,
                    provider_timestamp TEXT,
                    is_stale INTEGER CHECK(is_stale IN (0, 1)),
                    stale_seconds INTEGER CHECK(stale_seconds >= 0),
                    fallback_from TEXT,
                    data_quality TEXT NOT NULL CHECK(data_quality IN ('ok', 'partial', 'unavailable')),
                    missing_fields_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE INDEX IF NOT EXISTS market_snapshots_run_fetched
                    ON market_snapshots(run_id, fetched_at DESC, id);
                CREATE INDEX IF NOT EXISTS market_snapshots_stock_fetched
                    ON market_snapshots(stock_code, fetched_at DESC, id);

                CREATE TABLE IF NOT EXISTS monitor_findings (
                    id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL REFERENCES market_snapshots(id) ON DELETE CASCADE,
                    run_id TEXT NOT NULL REFERENCES monitor_runs(id) ON DELETE CASCADE,
                    watchlist_item_id TEXT REFERENCES watchlist_items(id) ON DELETE SET NULL,
                    stock_code TEXT NOT NULL,
                    company TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL,
                    provider_timestamp TEXT,
                    fetched_at TEXT NOT NULL,
                    rule_type TEXT NOT NULL CHECK(rule_type IN ('change_percent_threshold', 'volume_ratio')),
                    rule_version TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('positive', 'negative', 'neutral')),
                    observed_value REAL NOT NULL,
                    threshold_value REAL NOT NULL,
                    baseline_value REAL,
                    baseline_count INTEGER NOT NULL DEFAULT 0 CHECK(baseline_count >= 0),
                    details_json TEXT NOT NULL DEFAULT '{}',
                    dedupe_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS monitor_findings_run_created
                    ON monitor_findings(run_id, created_at DESC, id);
                CREATE INDEX IF NOT EXISTS monitor_findings_stock_created
                    ON monitor_findings(stock_code, created_at DESC, id);
                CREATE INDEX IF NOT EXISTS monitor_findings_snapshot
                    ON monitor_findings(snapshot_id, id);
                CREATE INDEX IF NOT EXISTS monitor_findings_observation
                    ON monitor_findings(
                        stock_code, provider, provider_timestamp, created_at DESC, id
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

                CREATE TABLE IF NOT EXISTS monitor_finding_evidence (
                    finding_id TEXT NOT NULL REFERENCES monitor_findings(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (finding_id, case_id),
                    UNIQUE (evidence_id)
                );

                CREATE INDEX IF NOT EXISTS monitor_finding_evidence_case
                    ON monitor_finding_evidence(case_id, created_at DESC, finding_id);

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
            self._migrate_market_snapshot_contract(connection)
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

    @staticmethod
    def _migrate_market_snapshot_contract(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'market_snapshots'"
        ).fetchone()
        table_sql = row["sql"] if row else ""
        if "'complete'" not in table_sql and "is_stale INTEGER NOT NULL" not in table_sql:
            return

        try:
            connection.executescript(
                """
            BEGIN IMMEDIATE;
            DROP TABLE IF EXISTS market_snapshots_v2;
            CREATE TABLE market_snapshots_v2 (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES monitor_runs(id) ON DELETE CASCADE,
                watchlist_item_id TEXT REFERENCES watchlist_items(id) ON DELETE SET NULL,
                stock_code TEXT NOT NULL,
                company TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL,
                price REAL,
                change_percent REAL,
                volume REAL,
                turnover REAL,
                fetched_at TEXT NOT NULL,
                provider_timestamp TEXT,
                is_stale INTEGER CHECK(is_stale IN (0, 1)),
                stale_seconds INTEGER CHECK(stale_seconds >= 0),
                fallback_from TEXT,
                data_quality TEXT NOT NULL CHECK(data_quality IN ('ok', 'partial', 'unavailable')),
                missing_fields_json TEXT NOT NULL DEFAULT '[]'
            );
            INSERT INTO market_snapshots_v2 (
                id, run_id, watchlist_item_id, stock_code, company, provider,
                price, change_percent, volume, turnover, fetched_at,
                provider_timestamp, is_stale, stale_seconds, fallback_from,
                data_quality, missing_fields_json
            )
            SELECT
                id, run_id, watchlist_item_id, stock_code, company, provider,
                price, change_percent, volume, turnover, fetched_at,
                provider_timestamp, is_stale, stale_seconds, fallback_from,
                CASE data_quality
                    WHEN 'complete' THEN 'ok'
                    WHEN 'ok' THEN 'ok'
                    WHEN 'partial' THEN 'partial'
                    WHEN 'unavailable' THEN 'unavailable'
                    ELSE data_quality
                END,
                missing_fields_json
            FROM market_snapshots;
            DROP TABLE market_snapshots;
            ALTER TABLE market_snapshots_v2 RENAME TO market_snapshots;
            CREATE INDEX market_snapshots_run_fetched
                ON market_snapshots(run_id, fetched_at DESC, id);
            CREATE INDEX market_snapshots_stock_fetched
                ON market_snapshots(stock_code, fetched_at DESC, id);
            COMMIT;
                """
            )
        except sqlite3.Error:
            if connection.in_transaction:
                connection.rollback()
            raise

    def create_watchlist_item(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        item_id = str(uuid.uuid4())
        now = utc_now()
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            next_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM watchlist_items"
            ).fetchone()[0]
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO watchlist_items (
                    id, stock_code, company, enabled, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    payload["stock_code"],
                    payload.get("company") or "",
                    int(payload.get("enabled", True)),
                    next_order,
                    now,
                    now,
                ),
            )
            created = cursor.rowcount > 0
            row = connection.execute(
                "SELECT * FROM watchlist_items WHERE stock_code = ?",
                (payload["stock_code"],),
            ).fetchone()
        if row is None:
            raise RuntimeError("watchlist insert failed")
        return self._watchlist_row(row), created

    def get_watchlist_item(self, item_id: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                "SELECT * FROM watchlist_items WHERE id = ?", (item_id,)
            ).fetchone()
        return self._watchlist_row(row) if row else None

    def list_watchlist_items(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM watchlist_items
                {"WHERE enabled = 1" if enabled_only else ""}
                ORDER BY sort_order, created_at, id
                """
            ).fetchall()
        return [self._watchlist_row(row) for row in rows]

    def update_watchlist_item(
        self, item_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        clean = {key: value for key, value in updates.items() if key in WATCHLIST_FIELDS}
        if not clean:
            return self.get_watchlist_item(item_id)

        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM watchlist_items WHERE id = ?", (item_id,)
            ).fetchone()
            if current is None:
                return None

            target_order = clean.pop("sort_order", None)
            if target_order is not None:
                item_count = connection.execute(
                    "SELECT COUNT(*) FROM watchlist_items"
                ).fetchone()[0]
                target_order = min(int(target_order), max(item_count - 1, 0))
                current_order = int(current["sort_order"])
                if target_order < current_order:
                    connection.execute(
                        """
                        UPDATE watchlist_items
                        SET sort_order = sort_order + 1
                        WHERE sort_order >= ? AND sort_order < ?
                        """,
                        (target_order, current_order),
                    )
                elif target_order > current_order:
                    connection.execute(
                        """
                        UPDATE watchlist_items
                        SET sort_order = sort_order - 1
                        WHERE sort_order > ? AND sort_order <= ?
                        """,
                        (current_order, target_order),
                    )
                clean["sort_order"] = target_order

            clean["updated_at"] = utc_now()
            assignments = ", ".join(f"{key} = ?" for key in clean)
            connection.execute(
                f"UPDATE watchlist_items SET {assignments} WHERE id = ?",
                (*clean.values(), item_id),
            )
            row = connection.execute(
                "SELECT * FROM watchlist_items WHERE id = ?", (item_id,)
            ).fetchone()
        return self._watchlist_row(row) if row else None

    def delete_watchlist_item(self, item_id: str) -> bool:
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT sort_order FROM watchlist_items WHERE id = ?", (item_id,)
            ).fetchone()
            if current is None:
                return False
            connection.execute("DELETE FROM watchlist_items WHERE id = ?", (item_id,))
            connection.execute(
                """
                UPDATE watchlist_items
                SET sort_order = sort_order - 1
                WHERE sort_order > ?
                """,
                (current["sort_order"],),
            )
        return True

    def create_monitor_run(self, *, provider: str, requested_count: int) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        started_at = utc_now()
        with self.session() as connection:
            connection.execute(
                """
                INSERT INTO monitor_runs (
                    id, trigger, status, provider, requested_count,
                    success_count, failure_count, errors_json, started_at, completed_at
                ) VALUES (?, 'manual', 'running', ?, ?, 0, 0, '[]', ?, NULL)
                """,
                (run_id, provider, requested_count, started_at),
            )
        return self.get_monitor_run(run_id)  # type: ignore[return-value]

    def finish_monitor_run(
        self,
        run_id: str,
        *,
        success_count: int,
        errors: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        failure_count = len(errors)
        run_status = (
            "completed"
            if failure_count == 0
            else ("partial" if success_count > 0 else "failed")
        )
        with self.session() as connection:
            cursor = connection.execute(
                """
                UPDATE monitor_runs
                SET status = ?, success_count = ?, failure_count = ?,
                    errors_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    run_status,
                    success_count,
                    failure_count,
                    json.dumps(errors, ensure_ascii=False, separators=(",", ":")),
                    utc_now(),
                    run_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_monitor_run(run_id)

    def get_monitor_run(self, run_id: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                "SELECT * FROM monitor_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return self._monitor_run_row(row) if row else None

    def get_latest_monitor_run(self) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT * FROM monitor_runs
                ORDER BY started_at DESC, rowid DESC
                LIMIT 1
                """
            ).fetchone()
        return self._monitor_run_row(row) if row else None

    def list_monitor_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM monitor_runs
                ORDER BY started_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._monitor_run_row(row) for row in rows]

    def add_market_snapshot(
        self,
        run_id: str,
        watchlist_item: dict[str, Any],
        *,
        provider: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot_id = str(uuid.uuid4())
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_watchlist_item = connection.execute(
                "SELECT id FROM watchlist_items WHERE id = ?",
                (watchlist_item["id"],),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO market_snapshots (
                    id, run_id, watchlist_item_id, stock_code, company, provider,
                    price, change_percent, volume, turnover, fetched_at,
                    provider_timestamp, is_stale, stale_seconds, fallback_from,
                    data_quality, missing_fields_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    run_id,
                    current_watchlist_item["id"] if current_watchlist_item else None,
                    watchlist_item["stock_code"],
                    payload.get("company") or watchlist_item.get("company") or "",
                    provider,
                    payload.get("price"),
                    payload.get("change_percent"),
                    payload.get("volume"),
                    payload.get("turnover"),
                    payload["fetched_at"],
                    payload.get("provider_timestamp"),
                    (
                        None
                        if payload.get("is_stale") is None
                        else int(bool(payload["is_stale"]))
                    ),
                    payload.get("stale_seconds"),
                    payload.get("fallback_from"),
                    payload["data_quality"],
                    json.dumps(
                        payload.get("missing_fields") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            )
            row = connection.execute(
                "SELECT * FROM market_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("market snapshot insert failed")
        return self._market_snapshot_row(row)

    def list_market_snapshots(
        self,
        *,
        run_id: str | None = None,
        stock_code: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            values.append(run_id)
        if stock_code:
            clauses.append("stock_code = ?")
            values.append(stock_code)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self.session() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM market_snapshots
                {where}
                ORDER BY fetched_at DESC, rowid DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._market_snapshot_row(row) for row in rows]

    def list_latest_market_snapshots(self) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT snapshot.*
                FROM watchlist_items AS watchlist
                JOIN market_snapshots AS snapshot
                    ON snapshot.id = (
                        SELECT candidate.id
                        FROM market_snapshots AS candidate
                        WHERE candidate.watchlist_item_id = watchlist.id
                          AND candidate.data_quality != 'unavailable'
                        ORDER BY candidate.fetched_at DESC, candidate.rowid DESC
                        LIMIT 1
                    )
                ORDER BY watchlist.sort_order, watchlist.created_at, watchlist.id
                """
            ).fetchall()
        return [self._market_snapshot_row(row) for row in rows]

    def add_monitor_finding(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        finding_id = str(uuid.uuid4())
        created_at = utc_now()
        with self.session() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO monitor_findings (
                    id, snapshot_id, run_id, watchlist_item_id, stock_code,
                    company, provider, provider_timestamp, fetched_at,
                    rule_type, rule_version, direction, observed_value,
                    threshold_value, baseline_value, baseline_count,
                    details_json, dedupe_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding_id,
                    payload["snapshot_id"],
                    payload["run_id"],
                    payload.get("watchlist_item_id"),
                    payload["stock_code"],
                    payload.get("company") or "",
                    payload["provider"],
                    payload.get("provider_timestamp"),
                    payload["fetched_at"],
                    payload["rule_type"],
                    payload["rule_version"],
                    payload["direction"],
                    payload["observed_value"],
                    payload["threshold_value"],
                    payload.get("baseline_value"),
                    payload.get("baseline_count") or 0,
                    json.dumps(
                        payload.get("details") or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    payload["dedupe_key"],
                    created_at,
                ),
            )
            created = cursor.rowcount > 0
            row = connection.execute(
                """
                SELECT finding.*, (
                    SELECT COUNT(*)
                    FROM monitor_finding_evidence AS link
                    WHERE link.finding_id = finding.id
                ) AS evidence_count
                FROM monitor_findings AS finding
                WHERE finding.dedupe_key = ?
                """,
                (payload["dedupe_key"],),
            ).fetchone()
        if row is None:
            raise RuntimeError("monitor finding insert failed")
        return self._monitor_finding_row(row), created

    def get_monitor_finding(self, finding_id: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT finding.*, (
                    SELECT COUNT(*)
                    FROM monitor_finding_evidence AS link
                    WHERE link.finding_id = finding.id
                ) AS evidence_count
                FROM monitor_findings AS finding
                WHERE finding.id = ?
                """,
                (finding_id,),
            ).fetchone()
        return self._monitor_finding_row(row) if row else None

    def list_monitor_findings_for_observation(
        self, snapshot: dict[str, Any]
    ) -> list[dict[str, Any]]:
        provider_timestamp = snapshot.get("provider_timestamp")
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT finding.*, (
                    SELECT COUNT(*)
                    FROM monitor_finding_evidence AS link
                    WHERE link.finding_id = finding.id
                ) AS evidence_count
                FROM monitor_findings AS finding
                WHERE finding.snapshot_id = ?
                   OR (
                        ? IS NOT NULL
                        AND finding.stock_code = ?
                        AND finding.provider = ?
                        AND finding.provider_timestamp = ?
                   )
                ORDER BY finding.created_at DESC, finding.rowid DESC
                """,
                (
                    snapshot["id"],
                    provider_timestamp,
                    snapshot["stock_code"],
                    snapshot["provider"],
                    provider_timestamp,
                ),
            ).fetchall()
        return [self._monitor_finding_row(row) for row in rows]

    def list_monitor_findings(
        self,
        *,
        run_id: str | None = None,
        stock_code: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if run_id:
            clauses.append("finding.run_id = ?")
            values.append(run_id)
        if stock_code:
            clauses.append("finding.stock_code = ?")
            values.append(stock_code)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self.session() as connection:
            rows = connection.execute(
                f"""
                SELECT finding.*, (
                    SELECT COUNT(*)
                    FROM monitor_finding_evidence AS link
                    WHERE link.finding_id = finding.id
                ) AS evidence_count
                FROM monitor_findings AS finding
                {where}
                ORDER BY finding.created_at DESC, finding.rowid DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._monitor_finding_row(row) for row in rows]

    def convert_monitor_findings(
        self,
        case_id: str,
        finding_candidates: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        finding_ids = [finding_id for finding_id, _candidate in finding_candidates]
        placeholders = ", ".join("?" for _ in finding_ids)
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            case = connection.execute(
                "SELECT stock_code FROM cases WHERE id = ?", (case_id,)
            ).fetchone()
            if case is None:
                raise MonitorFindingConversionNotFoundError("case not found")

            finding_rows = connection.execute(
                f"SELECT id, stock_code FROM monitor_findings WHERE id IN ({placeholders})",
                finding_ids,
            ).fetchall()
            findings_by_id = {row["id"]: row for row in finding_rows}
            for finding_id in finding_ids:
                finding = findings_by_id.get(finding_id)
                if finding is None:
                    raise MonitorFindingConversionNotFoundError(
                        "monitor finding not found"
                    )
                if finding["stock_code"] != case["stock_code"]:
                    raise MonitorFindingConversionConflictError(
                        "monitor finding stock code does not match case"
                    )

            items: list[dict[str, Any]] = []
            links: list[dict[str, Any]] = []
            created_count = 0
            for finding_id, candidate in finding_candidates:
                linked = connection.execute(
                    """
                    SELECT evidence.*
                    FROM monitor_finding_evidence AS link
                    JOIN evidence ON evidence.id = link.evidence_id
                    WHERE link.finding_id = ? AND link.case_id = ?
                    """,
                    (finding_id, case_id),
                ).fetchone()
                if linked is not None:
                    history = self._load_review_history(connection, linked["id"])
                    evidence = self._evidence_row(linked, history)
                    created = False
                else:
                    evidence, created = self._add_evidence(
                        connection,
                        case_id,
                        candidate,
                        origin="automatic",
                    )
                    connection.execute(
                        """
                        INSERT INTO monitor_finding_evidence (
                            finding_id, case_id, evidence_id, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (finding_id, case_id, evidence["id"], utc_now()),
                    )

                items.append(evidence)
                links.append(
                    {
                        "finding_id": finding_id,
                        "evidence_id": evidence["id"],
                        "created": created,
                    }
                )
                created_count += int(created)

        return {"items": items, "links": links, "created_count": created_count}

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
        with self.session() as connection:
            return self._add_evidence(connection, case_id, payload, origin=origin)

    def _add_evidence(
        self,
        connection: sqlite3.Connection,
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
            row = connection.execute(
                "SELECT * FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
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
    def _watchlist_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result

    @staticmethod
    def _monitor_run_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["errors"] = json.loads(result.pop("errors_json") or "[]")
        return result

    @staticmethod
    def _market_snapshot_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        if result["is_stale"] is not None:
            result["is_stale"] = bool(result["is_stale"])
        result["missing_fields"] = json.loads(
            result.pop("missing_fields_json") or "[]"
        )
        return result

    @staticmethod
    def _monitor_finding_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["details"] = json.loads(result.pop("details_json") or "{}")
        result["evidence_count"] = int(result.get("evidence_count") or 0)
        return result

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
