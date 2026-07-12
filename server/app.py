from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from server.database import Database
from server.models import (
    CaseCreate,
    CasePatch,
    DiscoveryRequest,
    EvidenceCreate,
    EvidencePatch,
    ScoreRequest,
)
from server.scoring import score_case
from server.services.cninfo import CninfoConnector, CninfoError


ROOT = Path(__file__).resolve().parents[1]


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
    static_dir: str | Path | None = None,
) -> FastAPI:
    database = Database(db_path or os.getenv("CATALYST_DB_PATH", ROOT / "data" / "catalyst.db"))

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database.initialize()
        yield

    application = FastAPI(
        title="A-Share Catalyst Lens API",
        version="0.3.0",
        lifespan=lifespan,
    )
    application.state.database = database
    application.state.connector = connector or CninfoConnector()

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
            "mode": "local-first",
        }

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
