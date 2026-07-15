from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from server.database import (
    Database,
    MonitorFindingConversionConflictError,
    MonitorFindingConversionNotFoundError,
)
from server.models import (
    CaseCreate,
    CasePatch,
    DiscoveryRequest,
    EvidenceCreate,
    EvidencePatch,
    MonitorFindingConversionRequest,
    MonitorRefreshRequest,
    ScoreRequest,
    WatchlistCreate,
    WatchlistPatch,
)
from server.scoring import score_case
from server.services.cninfo import CninfoConnector, CninfoError
from server.services.findings import evidence_candidate_from_finding
from server.services.market import (
    MarketDataProvider,
    TencentMarketProvider,
)
from server.services.monitoring import (
    MONITOR_LOCK_NAME,
    MonitorRunLockLostError,
    MonitorRunLockedError,
    MonitorRunner,
    MonitorRuntimeSettings,
    MonitorScheduler,
)


ROOT = Path(__file__).resolve().parents[1]


def _findings_for_snapshots(
    database: Database, snapshots: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    findings_by_id: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        candidates = database.list_monitor_findings_for_observation(snapshot)
        for finding in candidates:
            same_observation = (
                finding["provider"] == snapshot["provider"]
                and finding.get("provider_timestamp")
                and finding.get("provider_timestamp")
                == snapshot.get("provider_timestamp")
            )
            if same_observation or finding["snapshot_id"] == snapshot["id"]:
                findings_by_id[finding["id"]] = finding
    return list(findings_by_id.values())


def validate_stored_url(value: str | None) -> None:
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="evidence URL must use http or https")


def create_app(
    *,
    db_path: str | Path | None = None,
    connector: Any | None = None,
    market_provider: MarketDataProvider | None = None,
    monitor_settings: MonitorRuntimeSettings | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    database = Database(db_path or os.getenv("CATALYST_DB_PATH", ROOT / "data" / "catalyst.db"))
    provider = market_provider or TencentMarketProvider()
    runtime_settings = monitor_settings or MonitorRuntimeSettings.from_environment()
    monitor_runner = MonitorRunner(
        database=database,
        provider=provider,
        settings=runtime_settings,
    )
    monitor_scheduler = MonitorScheduler(
        database=database,
        runner=monitor_runner,
        settings=runtime_settings,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database.initialize()
        database.recover_interrupted_monitor_runtime(
            lock_name=MONITOR_LOCK_NAME,
            now=datetime.now(timezone.utc),
            stale_slot_seconds=runtime_settings.lock_ttl_seconds,
        )
        await application.state.monitor_scheduler.start()
        try:
            yield
        finally:
            await application.state.monitor_scheduler.stop()

    application = FastAPI(
        title="A-Share Catalyst Lens API",
        version="0.7.0",
        lifespan=lifespan,
    )
    application.state.database = database
    application.state.connector = connector or CninfoConnector()
    application.state.market_provider = provider
    application.state.monitor_runner = monitor_runner
    application.state.monitor_scheduler = monitor_scheduler
    application.state.monitor_settings = runtime_settings

    allowed_origins = [
        origin.strip()
        for origin in os.getenv(
            "CATALYST_ALLOWED_ORIGINS", ""
        ).split(",")
        if origin.strip()
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    def require_case(case_id: str) -> dict[str, Any]:
        case = database.get_case(case_id)
        if not case:
            raise HTTPException(status_code=404, detail="case not found")
        return case

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": application.version,
            "database": "sqlite",
            "connectors": [application.state.connector.name],
            "market_provider": application.state.market_provider.name,
            "monitor_findings": True,
            "monitor_runtime": True,
            "monitor_doctor": True,
            "monitor_scheduler_enabled": runtime_settings.scheduler_enabled,
            "mode": "local-first",
        }

    @application.get("/api/watchlist")
    def list_watchlist() -> dict[str, Any]:
        return {"items": database.list_watchlist_items()}

    @application.post("/api/watchlist", status_code=status.HTTP_201_CREATED)
    def create_watchlist_item(payload: WatchlistCreate) -> dict[str, Any]:
        item, created = database.create_watchlist_item(payload.model_dump(mode="json"))
        return {"item": item, "created": created}

    @application.patch("/api/watchlist/{item_id}")
    def update_watchlist_item(
        item_id: str, payload: WatchlistPatch
    ) -> dict[str, Any]:
        item = database.update_watchlist_item(
            item_id, payload.model_dump(mode="json", exclude_unset=True)
        )
        if not item:
            raise HTTPException(status_code=404, detail="watchlist item not found")
        return item

    @application.delete(
        "/api/watchlist/{item_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    def delete_watchlist_item(item_id: str) -> Response:
        if not database.delete_watchlist_item(item_id):
            raise HTTPException(status_code=404, detail="watchlist item not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.post("/api/monitor/refresh")
    async def refresh_monitor(_payload: MonitorRefreshRequest) -> dict[str, Any]:
        try:
            return await application.state.monitor_runner.run(
                trigger="manual",
                provider=application.state.market_provider,
            )
        except MonitorRunLockedError as exc:
            trace_id = (exc.lock or {}).get("trace_id") or "unknown"
            expires_at = (exc.lock or {}).get("expires_at") or "unknown"
            raise HTTPException(
                status_code=409,
                detail=(
                    "monitor refresh already running; "
                    f"trace_id={trace_id}; expires_at={expires_at}"
                ),
            ) from exc
        except MonitorRunLockLostError as exc:
            run_id = getattr(exc, "monitor_run_id", None) or "unknown"
            raise HTTPException(
                status_code=409,
                detail=(
                    "monitor refresh lost its runtime lock; "
                    f"run_id={run_id}; inspect /api/monitor/doctor"
                ),
            ) from exc

    @application.get("/api/monitor/doctor")
    def monitor_doctor() -> dict[str, Any]:
        database.recover_interrupted_monitor_runtime(
            lock_name=MONITOR_LOCK_NAME,
            now=datetime.now(timezone.utc),
            stale_slot_seconds=runtime_settings.lock_ttl_seconds,
        )
        return application.state.monitor_scheduler.doctor()

    @application.get("/api/monitor/latest")
    def latest_monitor() -> dict[str, Any]:
        run = database.get_latest_monitor_run()
        items = database.list_latest_market_snapshots()
        return {
            "run": run,
            "items": items,
            "errors": run["errors"] if run else [],
            "findings": _findings_for_snapshots(database, items),
            "finding_errors": run["finding_errors"] if run else [],
        }

    @application.get("/api/monitor/runs")
    def list_monitor_runs(limit: int = 50) -> dict[str, Any]:
        return {"items": database.list_monitor_runs(min(max(limit, 1), 100))}

    @application.get("/api/monitor/snapshots")
    def list_market_snapshots(
        run_id: str | None = None,
        stock_code: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return {
            "items": database.list_market_snapshots(
                run_id=run_id,
                stock_code=stock_code,
                limit=min(max(limit, 1), 500),
            )
        }

    @application.get("/api/monitor/findings")
    def list_monitor_findings(
        run_id: str | None = None,
        stock_code: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return {
            "items": database.list_monitor_findings(
                run_id=run_id,
                stock_code=stock_code,
                limit=min(max(limit, 1), 500),
            )
        }

    @application.post("/api/cases/{case_id}/monitor/findings")
    def convert_monitor_findings(
        case_id: str,
        payload: MonitorFindingConversionRequest,
    ) -> dict[str, Any]:
        case = require_case(case_id)
        findings: list[dict[str, Any]] = []
        for finding_id in payload.finding_ids:
            finding = database.get_monitor_finding(finding_id)
            if finding is None:
                raise HTTPException(status_code=404, detail="monitor finding not found")
            if finding["stock_code"] != case["stock_code"]:
                raise HTTPException(
                    status_code=409,
                    detail="monitor finding stock code does not match case",
                )
            findings.append(finding)

        candidates = [
            (finding["id"], evidence_candidate_from_finding(finding))
            for finding in findings
        ]
        try:
            return database.convert_monitor_findings(case_id, candidates)
        except MonitorFindingConversionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except MonitorFindingConversionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/cases", status_code=status.HTTP_201_CREATED)
    def create_case(payload: CaseCreate) -> dict[str, Any]:
        return database.create_case(payload.model_dump(mode="json"))

    @application.get("/api/cases")
    def list_cases(limit: int = 50) -> dict[str, Any]:
        return {"items": database.list_cases(min(max(limit, 1), 100))}

    @application.get("/api/cases/{case_id}")
    def get_case(case_id: str) -> dict[str, Any]:
        return require_case(case_id)

    @application.patch("/api/cases/{case_id}")
    def update_case(case_id: str, payload: CasePatch) -> dict[str, Any]:
        updated = database.update_case(
            case_id, payload.model_dump(mode="json", exclude_unset=True)
        )
        if not updated:
            raise HTTPException(status_code=404, detail="case not found")
        return updated

    @application.delete("/api/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_case(case_id: str) -> Response:
        if not database.delete_case(case_id):
            raise HTTPException(status_code=404, detail="case not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.get("/api/cases/{case_id}/evidence")
    def list_evidence(case_id: str) -> dict[str, Any]:
        require_case(case_id)
        return {"items": database.list_evidence(case_id)}

    @application.post(
        "/api/cases/{case_id}/evidence", status_code=status.HTTP_201_CREATED
    )
    def create_manual_evidence(case_id: str, payload: EvidenceCreate) -> dict[str, Any]:
        require_case(case_id)
        values = payload.model_dump(mode="json")
        validate_stored_url(values.get("url"))
        evidence, created = database.add_evidence(case_id, values, origin="manual")
        return {"item": evidence, "created": created}

    @application.patch("/api/evidence/{evidence_id}")
    def update_evidence(evidence_id: str, payload: EvidencePatch) -> dict[str, Any]:
        updates = payload.model_dump(mode="json", exclude_unset=True)
        if "url" in updates:
            validate_stored_url(updates.get("url"))
        evidence = database.update_evidence(evidence_id, updates)
        if not evidence:
            raise HTTPException(status_code=404, detail="evidence not found")
        return evidence

    @application.post("/api/cases/{case_id}/discover")
    async def discover_evidence(case_id: str, payload: DiscoveryRequest) -> dict[str, Any]:
        case = require_case(case_id)
        query = payload.query or case.get("query") or ""
        try:
            discovered = await application.state.connector.discover(
                stock_code=case["stock_code"],
                start_date=payload.start_date,
                end_date=payload.end_date,
                query=query,
                limit=payload.limit,
            )
        except CninfoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        items: list[dict[str, Any]] = []
        created_count = 0
        for candidate in discovered:
            item, created = database.add_evidence(case_id, candidate, origin="automatic")
            items.append(item)
            created_count += int(created)
        return {
            "connector": application.state.connector.name,
            "discovered_count": len(discovered),
            "created_count": created_count,
            "duplicate_count": len(discovered) - created_count,
            "items": items,
        }

    @application.post("/api/cases/{case_id}/score")
    def calculate_score(case_id: str, payload: ScoreRequest) -> dict[str, Any]:
        case = require_case(case_id)
        evidence = database.list_evidence(case_id)
        overrides = (
            payload.metrics.model_dump(exclude_none=True)
            if payload.metrics is not None
            else None
        )
        result = score_case(case, evidence, overrides)
        return database.save_score(case_id, result)

    web_root = Path(static_dir or ROOT / "web")
    application.mount("/", StaticFiles(directory=web_root, html=True), name="web")
    return application


app = create_app()
