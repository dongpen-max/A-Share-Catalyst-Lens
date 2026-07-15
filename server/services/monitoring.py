from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from server.database import Database, MonitorRunLockLostError
from server.services.findings import evaluate_market_snapshot
from server.services.market import (
    MarketDataProvider,
    MarketProviderError,
    snapshot_from_quote,
)
from server.services.trading_calendar import AshareTradingSessionPolicy


MONITOR_LOCK_NAME = "market-refresh"
MONITOR_SCHEDULER_NAME = "catalyst-watch"
MIN_SCHEDULER_INTERVAL_SECONDS = 5 * 60
SCHEDULER_RECHECK_SECONDS = 5 * 60

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class MonitorRuntimeSettings:
    scheduler_interval_seconds: int = 0
    scheduled_retry_attempts: int = 2
    retry_base_seconds: float = 1.0
    lock_ttl_seconds: int = 15 * 60
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: int = 5 * 60
    calendar_year: int | None = None
    market_holidays: frozenset[date] = frozenset()
    calendar_complete: bool = False

    @property
    def scheduler_enabled(self) -> bool:
        return self.scheduler_interval_seconds > 0

    @property
    def calendar_ready(self) -> bool:
        return (
            self.calendar_complete
            and self.calendar_year is not None
            and bool(self.market_holidays)
        )

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "MonitorRuntimeSettings":
        values = os.environ if environ is None else environ
        interval = _environment_int(
            values, "CATALYST_MONITOR_INTERVAL_SECONDS", 0
        )
        if interval != 0 and interval < MIN_SCHEDULER_INTERVAL_SECONDS:
            raise ValueError(
                "CATALYST_MONITOR_INTERVAL_SECONDS must be 0 or at least 300"
            )
        if interval > 24 * 60 * 60:
            raise ValueError(
                "CATALYST_MONITOR_INTERVAL_SECONDS cannot exceed 86400"
            )

        retry_attempts = _environment_int(
            values, "CATALYST_MONITOR_RETRY_ATTEMPTS", 2
        )
        if not 1 <= retry_attempts <= 3:
            raise ValueError("CATALYST_MONITOR_RETRY_ATTEMPTS must be 1 to 3")
        retry_base = _environment_float(
            values, "CATALYST_MONITOR_RETRY_BASE_SECONDS", 1.0
        )
        if not 0 <= retry_base <= 30:
            raise ValueError(
                "CATALYST_MONITOR_RETRY_BASE_SECONDS must be 0 to 30"
            )

        lock_ttl = _environment_int(values, "CATALYST_MONITOR_LOCK_SECONDS", 900)
        if not 60 <= lock_ttl <= 3600:
            raise ValueError("CATALYST_MONITOR_LOCK_SECONDS must be 60 to 3600")
        failure_threshold = _environment_int(
            values, "CATALYST_MONITOR_CIRCUIT_FAILURES", 3
        )
        if not 1 <= failure_threshold <= 20:
            raise ValueError(
                "CATALYST_MONITOR_CIRCUIT_FAILURES must be 1 to 20"
            )
        cooldown = _environment_int(
            values, "CATALYST_MONITOR_CIRCUIT_SECONDS", 300
        )
        if not 60 <= cooldown <= 3600:
            raise ValueError("CATALYST_MONITOR_CIRCUIT_SECONDS must be 60 to 3600")

        raw_year = values.get("CATALYST_MARKET_CALENDAR_YEAR", "").strip()
        calendar_year = int(raw_year) if raw_year else None
        if calendar_year is not None and not 2000 <= calendar_year <= 2100:
            raise ValueError("CATALYST_MARKET_CALENDAR_YEAR is invalid")
        holidays = _environment_dates(values.get("CATALYST_MARKET_HOLIDAYS", ""))
        calendar_complete = _environment_bool(
            values, "CATALYST_MARKET_CALENDAR_COMPLETE", False
        )
        if holidays and calendar_year is None:
            raise ValueError(
                "CATALYST_MARKET_CALENDAR_YEAR is required with market holidays"
            )
        if calendar_year is not None and any(
            item.year != calendar_year for item in holidays
        ):
            raise ValueError("market holidays must match the configured calendar year")
        if calendar_complete and (calendar_year is None or not holidays):
            raise ValueError(
                "a complete market calendar requires a year and holiday dates"
            )

        return cls(
            scheduler_interval_seconds=interval,
            scheduled_retry_attempts=retry_attempts,
            retry_base_seconds=retry_base,
            lock_ttl_seconds=lock_ttl,
            circuit_failure_threshold=failure_threshold,
            circuit_cooldown_seconds=cooldown,
            calendar_year=calendar_year,
            market_holidays=holidays,
            calendar_complete=calendar_complete,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "scheduler_enabled": self.scheduler_enabled,
            "scheduler_interval_seconds": self.scheduler_interval_seconds,
            "minimum_interval_seconds": MIN_SCHEDULER_INTERVAL_SECONDS,
            "scheduled_retry_attempts": self.scheduled_retry_attempts,
            "lock_ttl_seconds": self.lock_ttl_seconds,
            "circuit_failure_threshold": self.circuit_failure_threshold,
            "circuit_cooldown_seconds": self.circuit_cooldown_seconds,
            "calendar_year": self.calendar_year,
            "calendar_complete": self.calendar_complete,
            "calendar_ready": self.calendar_ready,
            "holiday_count": len(self.market_holidays),
        }


class MonitorRunLockedError(RuntimeError):
    def __init__(self, lock: dict[str, Any] | None) -> None:
        super().__init__("monitor refresh is already running")
        self.lock = lock


class MonitorRunner:
    def __init__(
        self,
        *,
        database: Database,
        provider: MarketDataProvider,
        settings: MonitorRuntimeSettings,
        clock: Clock = _utc_now,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self.database = database
        self.provider = provider
        self.settings = settings
        self.clock = clock
        self.sleeper = sleeper

    async def run(
        self,
        *,
        trigger: str,
        trace_id: str | None = None,
        scheduled_for: datetime | None = None,
        provider: MarketDataProvider | None = None,
    ) -> dict[str, Any]:
        if trigger not in {"manual", "scheduled"}:
            raise ValueError("monitor trigger must be manual or scheduled")
        trace_id = trace_id or str(uuid.uuid4())
        selected_provider = provider or self.provider
        owner_token = str(uuid.uuid4())
        started_at = self._now()
        acquired = self.database.acquire_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME,
            owner_token=owner_token,
            trace_id=trace_id,
            now=started_at,
            ttl_seconds=self.settings.lock_ttl_seconds,
        )
        if not acquired["acquired"]:
            raise MonitorRunLockedError(acquired["lock"])

        try:
            return await self._run_locked(
                trigger=trigger,
                trace_id=trace_id,
                scheduled_for=scheduled_for,
                started_at=started_at,
                owner_token=owner_token,
                provider=selected_provider,
            )
        finally:
            self.database.release_monitor_runtime_lock(
                name=MONITOR_LOCK_NAME, owner_token=owner_token
            )

    async def _run_locked(
        self,
        *,
        trigger: str,
        trace_id: str,
        scheduled_for: datetime | None,
        started_at: datetime,
        owner_token: str,
        provider: MarketDataProvider,
    ) -> dict[str, Any]:
        watchlist = self.database.list_watchlist_items(enabled_only=True)
        run = self.database.create_monitor_run(
            provider=provider.name,
            requested_count=len(watchlist),
            trigger=trigger,
            trace_id=trace_id,
            scheduled_for=scheduled_for,
            started_at=started_at,
        )
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        attempts: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        finding_errors: list[dict[str, str]] = []
        created_finding_count = 0
        try:
            for item_index, watchlist_item in enumerate(watchlist):
                self._renew_lock(owner_token)
                snapshot, error = await self._fetch_snapshot(
                    run=run,
                    watchlist_item=watchlist_item,
                    trigger=trigger,
                    attempts=attempts,
                    owner_token=owner_token,
                    provider=provider,
                )
                if snapshot is None:
                    errors.append(
                        _monitor_error(
                            watchlist_item, error or "market data unavailable"
                        )
                    )
                    continue

                self._renew_lock(owner_token)
                try:
                    stored_snapshot = self.database.add_market_snapshot(
                        run["id"],
                        watchlist_item,
                        provider=provider.name,
                        payload=snapshot,
                        lock_name=MONITOR_LOCK_NAME,
                        owner_token=owner_token,
                        lock_now=self._now(),
                    )
                except MonitorRunLockLostError:
                    raise
                except Exception:
                    interrupted_errors = [*errors]
                    for remaining_index, remaining_item in enumerate(
                        watchlist[item_index:]
                    ):
                        message = (
                            "snapshot storage failed"
                            if remaining_index == 0
                            else "refresh skipped after snapshot storage failure"
                        )
                        interrupted_errors.append(
                            _monitor_error(remaining_item, message)
                        )
                    try:
                        self.database.finalize_monitor_run(
                            run["id"],
                            success_count=len(items),
                            errors=interrupted_errors,
                            attempts=attempts,
                            finding_errors=finding_errors,
                        )
                    except Exception:
                        pass
                    raise
                if error or stored_snapshot["data_quality"] == "unavailable":
                    errors.append(
                        _monitor_error(
                            watchlist_item, error or "market data unavailable"
                        )
                    )
                    continue
                items.append(stored_snapshot)

            for snapshot in items:
                self._renew_lock(owner_token)
                try:
                    history = self.database.list_market_snapshots(
                        stock_code=snapshot["stock_code"], limit=500
                    )
                    candidates = evaluate_market_snapshot(snapshot, history)
                    for candidate in candidates:
                        finding, created = self.database.add_monitor_finding(
                            candidate,
                            lock_name=MONITOR_LOCK_NAME,
                            owner_token=owner_token,
                            lock_now=self._now(),
                        )
                        findings.append(finding)
                        created_finding_count += int(created)
                except MonitorRunLockLostError:
                    raise
                except Exception as exc:
                    message = str(exc).strip() or type(exc).__name__
                    finding_errors.append(
                        {
                            "watchlist_item_id": snapshot.get("watchlist_item_id") or "",
                            "stock_code": snapshot["stock_code"],
                            "company": snapshot.get("company") or "",
                            "message": f"finding evaluation failed: {message}"[:500],
                        }
                    )

            self._renew_lock(owner_token)
            final_run = self.database.finalize_monitor_run(
                run["id"],
                success_count=len(items),
                errors=errors,
                attempts=attempts,
                finding_errors=finding_errors,
                lock_name=MONITOR_LOCK_NAME,
                owner_token=owner_token,
                lock_now=self._now(),
            )
            if final_run is None:
                raise RuntimeError("monitor run could not be finalized")
            return {
                "run": final_run,
                "items": items,
                "errors": errors,
                "findings": findings,
                "created_finding_count": created_finding_count,
                "finding_errors": finding_errors,
            }
        except BaseException as exc:
            try:
                self._finalize_interrupted_run(
                    run=run,
                    watchlist=watchlist,
                    items=items,
                    errors=errors,
                    attempts=attempts,
                    finding_errors=finding_errors,
                    exc=exc,
                )
            except Exception:
                pass
            setattr(exc, "monitor_run_id", run["id"])
            raise

    async def _fetch_snapshot(
        self,
        *,
        run: dict[str, Any],
        watchlist_item: dict[str, Any],
        trigger: str,
        attempts: list[dict[str, Any]],
        owner_token: str,
        provider: MarketDataProvider,
    ) -> tuple[dict[str, Any] | None, str | None]:
        allowed_attempts = (
            self.settings.scheduled_retry_attempts if trigger == "scheduled" else 1
        )
        for attempt_number in range(1, allowed_attempts + 1):
            self._renew_lock(owner_token)
            attempt_started = self._now()
            if trigger == "scheduled":
                health = self.database.get_market_provider_health(provider.name)
                if _circuit_is_open(health, attempt_started):
                    message = (
                        "provider circuit open until "
                        f"{health['circuit_open_until']}"
                    )
                    attempts.append(
                        _attempt_record(
                            run=run,
                            watchlist_item=watchlist_item,
                            provider=provider.name,
                            attempt=attempt_number,
                            outcome="circuit_open",
                            started_at=attempt_started,
                            completed_at=self._now(),
                            error=message,
                        )
                    )
                    return None, message

            try:
                quote = await provider.fetch_quote(watchlist_item["stock_code"])
                if quote.stock_code != watchlist_item["stock_code"]:
                    raise MarketProviderError(
                        "provider returned a different stock code"
                    )
                snapshot = snapshot_from_quote(quote, fetched_at=self._now())
            except MarketProviderError as exc:
                message = str(exc).strip() or type(exc).__name__
                completed_at = self._now()
                self._renew_lock(owner_token, now=completed_at)
                health = self.database.record_market_provider_result(
                    provider.name,
                    success=False,
                    now=completed_at,
                    error=message,
                    failure_threshold=self.settings.circuit_failure_threshold,
                    cooldown_seconds=self.settings.circuit_cooldown_seconds,
                    affects_circuit=exc.affects_circuit,
                    lock_name=MONITOR_LOCK_NAME,
                    owner_token=owner_token,
                )
                attempts.append(
                    _attempt_record(
                        run=run,
                        watchlist_item=watchlist_item,
                        provider=provider.name,
                        attempt=attempt_number,
                        outcome=("failed" if exc.affects_circuit else "item_failed"),
                        started_at=attempt_started,
                        completed_at=completed_at,
                        error=message,
                    )
                )
                can_retry = (
                    trigger == "scheduled"
                    and attempt_number < allowed_attempts
                    and not _circuit_is_open(health, completed_at)
                )
                if not can_retry:
                    return None, message
                delay = self.settings.retry_base_seconds * (2 ** (attempt_number - 1))
                if delay:
                    await self._sleep_with_lock_renewal(owner_token, delay)
                continue
            except ValueError as exc:
                message = str(exc).strip() or type(exc).__name__
                completed_at = self._now()
                self._renew_lock(owner_token, now=completed_at)
                self.database.record_market_provider_result(
                    provider.name,
                    success=False,
                    now=completed_at,
                    error=message,
                    failure_threshold=self.settings.circuit_failure_threshold,
                    cooldown_seconds=self.settings.circuit_cooldown_seconds,
                    lock_name=MONITOR_LOCK_NAME,
                    owner_token=owner_token,
                )
                attempts.append(
                    _attempt_record(
                        run=run,
                        watchlist_item=watchlist_item,
                        provider=provider.name,
                        attempt=attempt_number,
                        outcome="invalid_data",
                        started_at=attempt_started,
                        completed_at=completed_at,
                        error=message,
                    )
                )
                return None, message
            except Exception as exc:
                message = str(exc).strip() or type(exc).__name__
                completed_at = self._now()
                self._renew_lock(owner_token, now=completed_at)
                self.database.record_market_provider_result(
                    provider.name,
                    success=False,
                    now=completed_at,
                    error=message,
                    failure_threshold=self.settings.circuit_failure_threshold,
                    cooldown_seconds=self.settings.circuit_cooldown_seconds,
                    lock_name=MONITOR_LOCK_NAME,
                    owner_token=owner_token,
                )
                attempts.append(
                    _attempt_record(
                        run=run,
                        watchlist_item=watchlist_item,
                        provider=provider.name,
                        attempt=attempt_number,
                        outcome="unexpected_failure",
                        started_at=attempt_started,
                        completed_at=completed_at,
                        error=message,
                    )
                )
                return None, f"unexpected provider failure: {message}"

            completed_at = self._now()
            self._renew_lock(owner_token, now=completed_at)
            if snapshot["data_quality"] == "unavailable":
                message = "market data unavailable"
                health = self.database.record_market_provider_result(
                    provider.name,
                    success=False,
                    now=completed_at,
                    error=message,
                    failure_threshold=self.settings.circuit_failure_threshold,
                    cooldown_seconds=self.settings.circuit_cooldown_seconds,
                    affects_circuit=False,
                    lock_name=MONITOR_LOCK_NAME,
                    owner_token=owner_token,
                )
                attempts.append(
                    _attempt_record(
                        run=run,
                        watchlist_item=watchlist_item,
                        provider=provider.name,
                        attempt=attempt_number,
                        outcome="unavailable",
                        started_at=attempt_started,
                        completed_at=completed_at,
                        error=message,
                    )
                )
                can_retry = (
                    trigger == "scheduled"
                    and attempt_number < allowed_attempts
                    and not _circuit_is_open(health, completed_at)
                )
                if can_retry:
                    delay = self.settings.retry_base_seconds * (
                        2 ** (attempt_number - 1)
                    )
                    if delay:
                        await self._sleep_with_lock_renewal(owner_token, delay)
                    continue
                return snapshot, message

            self.database.record_market_provider_result(
                provider.name,
                success=True,
                now=completed_at,
                failure_threshold=self.settings.circuit_failure_threshold,
                cooldown_seconds=self.settings.circuit_cooldown_seconds,
                lock_name=MONITOR_LOCK_NAME,
                owner_token=owner_token,
            )
            attempts.append(
                _attempt_record(
                    run=run,
                    watchlist_item=watchlist_item,
                    provider=provider.name,
                    attempt=attempt_number,
                    outcome="success",
                    started_at=attempt_started,
                    completed_at=completed_at,
                )
            )
            return snapshot, None
        return None, "provider attempts exhausted"

    async def _sleep_with_lock_renewal(
        self, owner_token: str, delay_seconds: float
    ) -> None:
        remaining = delay_seconds
        heartbeat_seconds = self.settings.lock_ttl_seconds / 2
        while remaining > 0:
            sleep_seconds = min(remaining, heartbeat_seconds)
            await self.sleeper(sleep_seconds)
            remaining -= sleep_seconds
            self._renew_lock(owner_token)

    def _renew_lock(
        self, owner_token: str, *, now: datetime | None = None
    ) -> None:
        if not self.database.renew_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME,
            owner_token=owner_token,
            now=now or self._now(),
            ttl_seconds=self.settings.lock_ttl_seconds,
        ):
            raise MonitorRunLockLostError("monitor run lock was lost")

    def _finalize_interrupted_run(
        self,
        *,
        run: dict[str, Any],
        watchlist: list[dict[str, Any]],
        items: list[dict[str, Any]],
        errors: list[dict[str, str]],
        attempts: list[dict[str, Any]],
        finding_errors: list[dict[str, str]],
        exc: BaseException,
    ) -> None:
        current = self.database.get_monitor_run(run["id"])
        if not current or current["status"] != "running":
            return
        handled_ids = {
            item.get("watchlist_item_id")
            for item in [*items, *errors]
            if item.get("watchlist_item_id")
        }
        if isinstance(exc, asyncio.CancelledError):
            message = "monitor run cancelled"
        elif isinstance(exc, MonitorRunLockLostError):
            message = "monitor run lock was lost"
        else:
            detail = str(exc).strip() or type(exc).__name__
            message = f"monitor run interrupted: {detail}"[:500]
        interrupted_errors = [*errors]
        interrupted_errors.extend(
            _monitor_error(item, message)
            for item in watchlist
            if item["id"] not in handled_ids
        )
        if isinstance(exc, asyncio.CancelledError):
            finding_errors = [
                *finding_errors,
                {
                    "watchlist_item_id": "",
                    "stock_code": "",
                    "company": "",
                    "message": "finding evaluation interrupted by scheduler shutdown",
                },
            ]
        elif (
            isinstance(exc, MonitorRunLockLostError)
            and bool(items)
            and len(handled_ids) == len(watchlist)
        ):
            finding_errors = [
                *finding_errors,
                {
                    "watchlist_item_id": "",
                    "stock_code": "",
                    "company": "",
                    "message": "finding evaluation may be incomplete after runtime lock loss",
                },
            ]
        try:
            self.database.finalize_monitor_run(
                run["id"],
                success_count=len(items),
                errors=interrupted_errors,
                attempts=attempts,
                finding_errors=finding_errors,
            )
        except Exception:
            pass

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("monitor clock must include a timezone")
        return value.astimezone(timezone.utc)


class MonitorScheduler:
    def __init__(
        self,
        *,
        database: Database,
        runner: MonitorRunner,
        settings: MonitorRuntimeSettings,
        clock: Clock = _utc_now,
    ) -> None:
        self.database = database
        self.runner = runner
        self.settings = settings
        self.clock = clock
        self.policy = AshareTradingSessionPolicy(
            calendar_year=settings.calendar_year,
            holidays=settings.market_holidays,
            calendar_complete=settings.calendar_complete,
        )
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self.settings.scheduler_enabled:
            return
        if self._task is not None and not self._task.done():
            return
        if self._task is not None:
            try:
                self._task.exception()
            except (asyncio.CancelledError, Exception):
                pass
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run_loop(), name="catalyst-watch-scheduler"
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            self._task = None
            self._stop_event = None

    async def tick(self, now: datetime | None = None) -> dict[str, Any]:
        tick_at = self._normalized_now(now or self.clock())
        if not self.settings.scheduler_enabled:
            state = self.database.update_monitor_scheduler_state(
                name=MONITOR_SCHEDULER_NAME,
                tick_at=tick_at,
                decision="disabled",
                message="scheduler is disabled",
            )
            return {"executed": False, "decision": "disabled", "state": state}

        session = self.policy.decision(tick_at)
        if not session.is_open:
            state = self.database.update_monitor_scheduler_state(
                name=MONITOR_SCHEDULER_NAME,
                tick_at=tick_at,
                decision=session.code,
                message="scheduled refresh skipped by trading session policy",
            )
            return {
                "executed": False,
                "decision": session.code,
                "session": session.as_dict(),
                "state": state,
            }

        scheduled_for = _schedule_slot_start(
            tick_at, self.settings.scheduler_interval_seconds
        )
        provider = self.runner.provider
        trace_id = str(uuid.uuid4())
        slot_key = (
            f"{provider.name}:"
            f"{self.settings.scheduler_interval_seconds}:"
            f"{scheduled_for.isoformat()}"
        )
        claimed = self.database.claim_monitor_schedule_slot(
            slot_key=slot_key,
            trace_id=trace_id,
            scheduled_for=scheduled_for,
            now=tick_at,
        )
        if not claimed:
            state = self.database.update_monitor_scheduler_state(
                name=MONITOR_SCHEDULER_NAME,
                tick_at=tick_at,
                decision="duplicate_slot",
                message="scheduled slot was already claimed",
            )
            return {
                "executed": False,
                "decision": "duplicate_slot",
                "slot_key": slot_key,
                "state": state,
            }

        self.database.update_monitor_schedule_slot(slot_key, outcome="running")
        try:
            payload = await self.runner.run(
                trigger="scheduled",
                trace_id=trace_id,
                scheduled_for=scheduled_for,
                provider=provider,
            )
        except MonitorRunLockedError as exc:
            details = {
                "message": str(exc),
                "active_trace_id": (exc.lock or {}).get("trace_id"),
                "expires_at": (exc.lock or {}).get("expires_at"),
            }
            slot = self.database.update_monitor_schedule_slot(
                slot_key, outcome="skipped_locked", details=details
            )
            state = self.database.update_monitor_scheduler_state(
                name=MONITOR_SCHEDULER_NAME,
                tick_at=tick_at,
                decision="skipped_locked",
                message=str(exc),
                trace_id=trace_id,
            )
            return {
                "executed": False,
                "decision": "skipped_locked",
                "slot": slot,
                "state": state,
            }
        except asyncio.CancelledError as exc:
            message = "scheduled refresh cancelled during shutdown"
            run_id = getattr(exc, "monitor_run_id", None)
            self.database.update_monitor_schedule_slot(
                slot_key,
                outcome="failed_internal",
                run_id=run_id,
                details={"message": message},
            )
            self.database.update_monitor_scheduler_state(
                name=MONITOR_SCHEDULER_NAME,
                tick_at=tick_at,
                decision="failed_internal",
                message=message,
                trace_id=trace_id,
                run_id=run_id,
            )
            raise
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            run_id = getattr(exc, "monitor_run_id", None)
            slot = self.database.update_monitor_schedule_slot(
                slot_key,
                outcome="failed_internal",
                run_id=run_id,
                details={"message": message[:500]},
            )
            state = self.database.update_monitor_scheduler_state(
                name=MONITOR_SCHEDULER_NAME,
                tick_at=tick_at,
                decision="failed_internal",
                message=message,
                trace_id=trace_id,
                run_id=run_id,
            )
            return {
                "executed": False,
                "decision": "failed_internal",
                "slot": slot,
                "state": state,
            }

        run = payload["run"]
        slot = self.database.update_monitor_schedule_slot(
            slot_key,
            outcome=run["status"],
            run_id=run["id"],
            details={
                "success_count": run["success_count"],
                "failure_count": run["failure_count"],
                "created_finding_count": payload["created_finding_count"],
            },
        )
        state = self.database.update_monitor_scheduler_state(
            name=MONITOR_SCHEDULER_NAME,
            tick_at=tick_at,
            decision=run["status"],
            message="scheduled refresh completed",
            trace_id=trace_id,
            run_id=run["id"],
        )
        return {
            "executed": True,
            "decision": run["status"],
            "slot": slot,
            "run": run,
            "state": state,
        }

    def doctor(self, now: datetime | None = None) -> dict[str, Any]:
        checked_at = self._normalized_now(now or self.clock())
        session = self.policy.decision(checked_at)
        health = self.database.get_market_provider_health(self.runner.provider.name)
        provider = {
            **health,
            "circuit_open": _circuit_is_open(health, checked_at),
        }
        lock = self.database.get_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME, now=checked_at
        )
        if lock:
            lock = {key: value for key, value in lock.items() if key != "owner_token"}
        return {
            "checked_at": checked_at.isoformat(),
            "settings": self.settings.as_dict(),
            "scheduler": self._scheduler_task_status(),
            "session": session.as_dict(),
            "lock": lock or {"active": False},
            "provider": provider,
            "scheduler_state": self.database.get_monitor_scheduler_state(
                name=MONITOR_SCHEDULER_NAME
            ),
            "recent_schedule_slots": self.database.list_monitor_schedule_slots(
                limit=10
            ),
            "last_run": self.database.get_latest_monitor_run(),
            "last_good": {
                **self.database.get_last_good_snapshot_coverage(),
                "strategy": "preserve_only",
            },
        }

    async def _run_loop(self) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            return
        while not stop_event.is_set():
            result: dict[str, Any] = {"executed": False}
            try:
                result = await self.tick()
            except Exception as exc:
                try:
                    now = self._normalized_now(self.clock())
                    self.database.update_monitor_scheduler_state(
                        name=MONITOR_SCHEDULER_NAME,
                        tick_at=now,
                        decision="failed_internal",
                        message=str(exc).strip() or type(exc).__name__,
                    )
                except Exception:
                    pass
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._loop_delay_seconds(result),
                )
            except TimeoutError:
                continue

    def _loop_delay_seconds(self, result: Mapping[str, Any]) -> int:
        if result.get("executed"):
            return self.settings.scheduler_interval_seconds
        return min(
            self.settings.scheduler_interval_seconds,
            SCHEDULER_RECHECK_SECONDS,
        )

    def _scheduler_task_status(self) -> dict[str, Any]:
        task = self._task
        if task is None:
            return {
                "configured_enabled": self.settings.scheduler_enabled,
                "state": "stopped",
                "running": False,
                "error": "",
            }
        if not task.done():
            return {
                "configured_enabled": self.settings.scheduler_enabled,
                "state": "running",
                "running": True,
                "error": "",
            }
        if task.cancelled():
            state = "cancelled"
            error = ""
        else:
            exception = task.exception()
            state = "failed" if exception else "stopped"
            error = (str(exception).strip() or type(exception).__name__) if exception else ""
        return {
            "configured_enabled": self.settings.scheduler_enabled,
            "state": state,
            "running": False,
            "error": error[:500],
        }

    @staticmethod
    def _normalized_now(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduler clock must include a timezone")
        return value.astimezone(timezone.utc)


def _environment_int(
    environ: Mapping[str, str], name: str, default: int
) -> int:
    raw = environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _environment_float(
    environ: Mapping[str, str], name: str, default: float
) -> float:
    raw = environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _environment_bool(
    environ: Mapping[str, str], name: str, default: bool
) -> bool:
    raw = environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _environment_dates(raw: str) -> frozenset[date]:
    values: set[date] = set()
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            values.add(date.fromisoformat(text))
        except ValueError as exc:
            raise ValueError(
                "CATALYST_MARKET_HOLIDAYS must contain ISO dates"
            ) from exc
    return frozenset(values)


def _monitor_error(
    watchlist_item: dict[str, Any], message: str
) -> dict[str, str]:
    return {
        "watchlist_item_id": watchlist_item["id"],
        "stock_code": watchlist_item["stock_code"],
        "company": watchlist_item.get("company") or "",
        "message": message[:500],
    }


def _attempt_record(
    *,
    run: dict[str, Any],
    watchlist_item: dict[str, Any],
    provider: str,
    attempt: int,
    outcome: str,
    started_at: datetime,
    completed_at: datetime,
    error: str = "",
) -> dict[str, Any]:
    return {
        "trace_id": run["trace_id"],
        "watchlist_item_id": watchlist_item["id"],
        "stock_code": watchlist_item["stock_code"],
        "provider": provider,
        "attempt": attempt,
        "outcome": outcome,
        "error": error[:500],
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
    }


def _circuit_is_open(health: dict[str, Any], now: datetime) -> bool:
    value = health.get("circuit_open_until")
    if not value:
        return False
    try:
        open_until = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return open_until.astimezone(timezone.utc) > now.astimezone(timezone.utc)


def _schedule_slot_start(value: datetime, interval_seconds: int) -> datetime:
    epoch = int(value.timestamp())
    slot_epoch = epoch - (epoch % interval_seconds)
    return datetime.fromtimestamp(slot_epoch, tz=timezone.utc)
