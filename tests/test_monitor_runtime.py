from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from server.database import Database
from server.services.market import MarketProviderError, MarketQuote
from server.services.monitoring import (
    MONITOR_LOCK_NAME,
    MonitorRunLockLostError,
    MonitorRunner,
    MonitorRuntimeSettings,
    MonitorScheduler,
)
from server.services.trading_calendar import AshareTradingSessionPolicy


OPEN_TIME = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
HOLIDAYS = frozenset({date(2026, 1, 1)})


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class SequenceProvider:
    name = "sequence-provider"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failures_remaining: dict[str, int] = {}
        self.always_fail = False

    async def fetch_quote(self, stock_code: str) -> MarketQuote:
        self.calls.append(stock_code)
        if self.always_fail or self.failures_remaining.get(stock_code, 0) > 0:
            remaining = self.failures_remaining.get(stock_code, 0)
            if remaining:
                self.failures_remaining[stock_code] = remaining - 1
            raise MarketProviderError(f"temporary failure for {stock_code}")
        return MarketQuote(
            stock_code=stock_code,
            price=10,
            change_percent=1,
            volume=100,
            turnover=1_000,
            provider_timestamp=OPEN_TIME,
        )


class MonitorRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runtime.db"
        self.database = Database(self.db_path)
        self.database.initialize()
        self.clock = MutableClock(OPEN_TIME)
        self.provider = SequenceProvider()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def settings(self, **overrides) -> MonitorRuntimeSettings:
        values = {
            "scheduler_interval_seconds": 300,
            "scheduled_retry_attempts": 2,
            "retry_base_seconds": 0,
            "lock_ttl_seconds": 60,
            "circuit_failure_threshold": 3,
            "circuit_cooldown_seconds": 600,
            "calendar_year": 2026,
            "market_holidays": HOLIDAYS,
            "calendar_complete": True,
        }
        values.update(overrides)
        return MonitorRuntimeSettings(**values)

    def runtime(
        self, settings: MonitorRuntimeSettings | None = None
    ) -> tuple[MonitorRunner, MonitorScheduler]:
        selected = settings or self.settings()
        runner = MonitorRunner(
            database=self.database,
            provider=self.provider,
            settings=selected,
            clock=self.clock,
        )
        scheduler = MonitorScheduler(
            database=self.database,
            runner=runner,
            settings=selected,
            clock=self.clock,
        )
        return runner, scheduler

    def add_watchlist(self, *stock_codes: str) -> None:
        for stock_code in stock_codes:
            self.database.create_watchlist_item(
                {"stock_code": stock_code, "company": "", "enabled": True}
            )

    async def test_scheduled_retry_succeeds_and_slot_is_idempotent(self) -> None:
        self.add_watchlist("600519")
        self.provider.failures_remaining["600519"] = 1
        _runner, scheduler = self.runtime()

        first = await scheduler.tick()
        duplicate = await scheduler.tick()

        self.assertTrue(first["executed"])
        self.assertEqual(first["decision"], "completed")
        self.assertFalse(duplicate["executed"])
        self.assertEqual(duplicate["decision"], "duplicate_slot")
        self.assertEqual(self.provider.calls, ["600519", "600519"])
        runs = self.database.list_monitor_runs()
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["trigger"], "scheduled")
        self.assertEqual(run["scheduled_for"], OPEN_TIME.isoformat())
        self.assertEqual(
            [attempt["outcome"] for attempt in run["attempts"]],
            ["failed", "success"],
        )
        self.assertTrue(run["trace_id"])
        self.assertEqual(
            {attempt["trace_id"] for attempt in run["attempts"]},
            {run["trace_id"]},
        )

    async def test_retry_backoff_renews_short_runtime_lease(self) -> None:
        self.add_watchlist("600519")
        self.provider.failures_remaining["600519"] = 2
        settings = self.settings(
            scheduled_retry_attempts=3,
            retry_base_seconds=30,
            lock_ttl_seconds=60,
        )

        async def advance_clock(seconds: float) -> None:
            self.clock.value += timedelta(seconds=seconds)

        runner = MonitorRunner(
            database=self.database,
            provider=self.provider,
            settings=settings,
            clock=self.clock,
            sleeper=advance_clock,
        )

        result = await runner.run(trigger="scheduled")

        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(self.provider.calls, ["600519", "600519", "600519"])
        self.assertEqual(
            [attempt["outcome"] for attempt in result["run"]["attempts"]],
            ["failed", "failed", "success"],
        )
        self.assertEqual(self.clock(), OPEN_TIME + timedelta(seconds=90))

    async def test_unknown_calendar_and_closed_session_never_call_provider(self) -> None:
        self.add_watchlist("600519")
        unavailable_settings = self.settings(
            calendar_year=None, market_holidays=frozenset()
        )
        _runner, unavailable = self.runtime(unavailable_settings)

        unavailable_result = await unavailable.tick()

        self.assertEqual(unavailable_result["decision"], "calendar_unavailable")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.database.list_monitor_runs(), [])

        self.clock.value = datetime(2026, 7, 15, 4, 0, tzinfo=timezone.utc)
        _runner, lunch = self.runtime()
        lunch_result = await lunch.tick()
        self.assertEqual(lunch_result["decision"], "outside_session")
        self.assertEqual(self.provider.calls, [])

    async def test_skipped_tick_rechecks_before_long_configured_interval(self) -> None:
        settings = self.settings(scheduler_interval_seconds=24 * 60 * 60)
        _runner, scheduler = self.runtime(settings)
        self.clock.value = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)

        skipped = await scheduler.tick()

        self.assertEqual(skipped["decision"], "outside_session")
        self.assertEqual(scheduler._loop_delay_seconds(skipped), 5 * 60)
        self.assertEqual(
            scheduler._loop_delay_seconds({"executed": True}),
            24 * 60 * 60,
        )

    async def test_circuit_opens_skips_calls_and_manual_probe_recovers(self) -> None:
        self.add_watchlist("600519", "000001")
        settings = self.settings(circuit_failure_threshold=2)
        runner, scheduler = self.runtime(settings)
        self.provider.always_fail = True

        first = await scheduler.tick()

        self.assertEqual(first["decision"], "failed")
        self.assertEqual(self.provider.calls, ["600519", "600519"])
        first_run = first["run"]
        self.assertEqual(first_run["failure_count"], 2)
        self.assertEqual(
            [attempt["outcome"] for attempt in first_run["attempts"]],
            ["failed", "failed", "circuit_open"],
        )
        doctor = scheduler.doctor()
        self.assertTrue(doctor["provider"]["circuit_open"])
        self.assertEqual(doctor["provider"]["consecutive_failures"], 2)

        self.clock.advance(300)
        second = await scheduler.tick()
        self.assertEqual(second["decision"], "failed")
        self.assertEqual(self.provider.calls, ["600519", "600519"])
        self.assertEqual(
            [attempt["outcome"] for attempt in second["run"]["attempts"]],
            ["circuit_open", "circuit_open"],
        )

        self.provider.always_fail = False
        manual = await runner.run(trigger="manual")
        self.assertEqual(manual["run"]["status"], "completed")
        recovered = scheduler.doctor()
        self.assertFalse(recovered["provider"]["circuit_open"])
        self.assertEqual(recovered["provider"]["consecutive_failures"], 0)

    async def test_failed_refresh_preserves_last_good_without_replay_snapshot(self) -> None:
        self.add_watchlist("600519")
        runner, scheduler = self.runtime()
        first = await runner.run(trigger="manual")
        first_snapshot = first["items"][0]
        self.provider.always_fail = True

        failed = await runner.run(trigger="manual")

        self.assertEqual(failed["run"]["status"], "failed")
        snapshots = self.database.list_market_snapshots(stock_code="600519")
        self.assertEqual([item["id"] for item in snapshots], [first_snapshot["id"]])
        coverage = scheduler.doctor()["last_good"]
        self.assertEqual(coverage["strategy"], "preserve_only")
        self.assertEqual(coverage["enabled_count"], 1)
        self.assertEqual(coverage["available_count"], 1)

    async def test_cross_connection_lock_expires_and_recovers(self) -> None:
        second_database = Database(self.db_path)
        first = self.database.acquire_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME,
            owner_token="first",
            trace_id="trace-first",
            now=self.clock(),
            ttl_seconds=60,
        )
        blocked = second_database.acquire_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME,
            owner_token="second",
            trace_id="trace-second",
            now=self.clock(),
            ttl_seconds=60,
        )
        self.clock.advance(61)
        self.assertFalse(
            self.database.renew_monitor_runtime_lock(
                name=MONITOR_LOCK_NAME,
                owner_token="first",
                now=self.clock(),
                ttl_seconds=60,
            )
        )
        recovered = second_database.acquire_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME,
            owner_token="second",
            trace_id="trace-second",
            now=self.clock(),
            ttl_seconds=60,
        )

        self.assertTrue(first["acquired"])
        self.assertFalse(blocked["acquired"])
        self.assertEqual(blocked["lock"]["trace_id"], "trace-first")
        self.assertTrue(recovered["acquired"])
        self.assertEqual(recovered["lock"]["trace_id"], "trace-second")

    async def test_next_lock_acquisition_recovers_orphan_after_expiry(self) -> None:
        second_database = Database(self.db_path)
        self.database.claim_monitor_schedule_slot(
            slot_key="restart-before-expiry",
            trace_id="trace-first",
            scheduled_for=self.clock(),
            now=self.clock(),
        )
        first = self.database.acquire_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME,
            owner_token="first-owner",
            trace_id="trace-first",
            now=self.clock(),
            ttl_seconds=60,
        )
        run = self.database.create_monitor_run(
            provider=self.provider.name,
            requested_count=1,
            trigger="scheduled",
            trace_id="trace-first",
            scheduled_for=self.clock(),
            started_at=self.clock(),
        )
        self.database.update_monitor_schedule_slot(
            "restart-before-expiry", outcome="running", run_id=run["id"]
        )

        self.clock.advance(30)
        skipped = second_database.recover_interrupted_monitor_runtime(
            lock_name=MONITOR_LOCK_NAME,
            now=self.clock(),
            stale_slot_seconds=60,
        )
        self.assertTrue(first["acquired"])
        self.assertEqual(skipped["recovered_run_count"], 0)
        self.assertEqual(self.database.get_monitor_run(run["id"])["status"], "running")

        self.clock.advance(31)
        acquired = second_database.acquire_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME,
            owner_token="second-owner",
            trace_id="trace-second",
            now=self.clock(),
            ttl_seconds=60,
        )

        self.assertTrue(acquired["acquired"])
        restored = self.database.get_monitor_run(run["id"])
        self.assertEqual(restored["status"], "failed")
        self.assertIn("process interruption", restored["errors"][0]["message"])
        slot = self.database.list_monitor_schedule_slots()[0]
        self.assertEqual(slot["outcome"], "failed_internal")

    async def test_concurrent_schedule_slot_claims_only_once(self) -> None:
        second_database = Database(self.db_path)

        async def claim(database: Database, trace_id: str) -> bool:
            return await asyncio.to_thread(
                database.claim_monitor_schedule_slot,
                slot_key="shared-slot",
                trace_id=trace_id,
                scheduled_for=self.clock(),
                now=self.clock(),
            )

        results = await asyncio.gather(
            claim(self.database, "trace-one"),
            claim(second_database, "trace-two"),
        )

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(self.database.list_monitor_schedule_slots()), 1)

    async def test_runner_aborts_if_lock_is_stolen_during_fetch(self) -> None:
        self.add_watchlist("600519")
        second_database = Database(self.db_path)
        replacement: dict[str, object] = {}

        async def fetch_after_takeover(stock_code: str) -> MarketQuote:
            self.provider.calls.append(stock_code)
            self.clock.advance(61)
            replacement.update(
                second_database.acquire_monitor_runtime_lock(
                    name=MONITOR_LOCK_NAME,
                    owner_token="replacement-owner",
                    trace_id="replacement-trace",
                    now=self.clock(),
                    ttl_seconds=60,
                )
            )
            return MarketQuote(
                stock_code=stock_code,
                price=10,
                change_percent=1,
                volume=100,
                turnover=1_000,
                provider_timestamp=self.clock(),
            )

        self.provider.fetch_quote = fetch_after_takeover
        runner, _scheduler = self.runtime()

        with self.assertRaises(MonitorRunLockLostError):
            await runner.run(trigger="manual")

        self.assertTrue(replacement["acquired"])
        run = self.database.list_monitor_runs()[0]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["failure_count"], 1)
        self.assertEqual(self.database.list_market_snapshots(), [])
        lock = second_database.get_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME, now=self.clock()
        )
        self.assertEqual(lock["trace_id"], "replacement-trace")
        second_database.release_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME, owner_token="replacement-owner"
        )

    async def test_scheduler_stop_finalizes_active_run_and_slot(self) -> None:
        self.add_watchlist("600519")
        fetch_started = asyncio.Event()
        never_finishes = asyncio.Event()

        async def blocking_fetch(stock_code: str) -> MarketQuote:
            self.provider.calls.append(stock_code)
            fetch_started.set()
            await never_finishes.wait()
            raise AssertionError("unreachable")

        self.provider.fetch_quote = blocking_fetch
        _runner, scheduler = self.runtime()

        await scheduler.start()
        await asyncio.wait_for(fetch_started.wait(), timeout=2)
        await scheduler.stop()

        run = self.database.list_monitor_runs()[0]
        self.assertEqual(run["status"], "failed")
        self.assertIsNotNone(run["completed_at"])
        self.assertIn("cancelled", run["errors"][0]["message"])
        slots = self.database.list_monitor_schedule_slots()
        self.assertEqual(slots[0]["outcome"], "failed_internal")
        self.assertEqual(slots[0]["run_id"], run["id"])
        self.assertFalse(
            self.database.get_monitor_runtime_lock(
                name=MONITOR_LOCK_NAME, now=self.clock()
            )
        )
        self.assertEqual(scheduler.doctor()["scheduler"]["state"], "stopped")

    async def test_unavailable_items_do_not_poison_global_circuit(self) -> None:
        stock_codes = ("000001", "000002", "000003", "600519")
        self.add_watchlist(*stock_codes)

        async def fetch_mixed(stock_code: str) -> MarketQuote:
            self.provider.calls.append(stock_code)
            if stock_code != "600519":
                return MarketQuote(stock_code=stock_code)
            return MarketQuote(
                stock_code=stock_code,
                price=10,
                change_percent=1,
                volume=100,
                turnover=1_000,
                provider_timestamp=self.clock(),
            )

        self.provider.fetch_quote = fetch_mixed
        _runner, scheduler = self.runtime(
            self.settings(
                scheduled_retry_attempts=1,
                circuit_failure_threshold=1,
            )
        )

        result = await scheduler.tick()

        self.assertEqual(result["decision"], "partial")
        self.assertEqual(self.provider.calls, list(stock_codes))
        run = result["run"]
        self.assertEqual(run["success_count"], 1)
        self.assertEqual(run["failure_count"], 3)
        self.assertEqual(
            [attempt["outcome"] for attempt in run["attempts"]],
            ["unavailable", "unavailable", "unavailable", "success"],
        )
        snapshots = self.database.list_market_snapshots()
        self.assertEqual(len(snapshots), 4)
        health = scheduler.doctor()["provider"]
        self.assertFalse(health["circuit_open"])
        self.assertEqual(health["consecutive_failures"], 0)
        self.assertIsNotNone(health["last_failure_at"])
        self.assertIsNotNone(health["last_success_at"])

    async def test_internal_health_write_failure_finalizes_and_unlocks(self) -> None:
        self.add_watchlist("600519")
        runner, _scheduler = self.runtime()
        original_record = self.database.record_market_provider_result

        def fail_health_write(*_args, **_kwargs):
            raise sqlite3.OperationalError("health store unavailable")

        self.database.record_market_provider_result = fail_health_write
        try:
            with self.assertRaises(sqlite3.OperationalError):
                await runner.run(trigger="manual")
        finally:
            self.database.record_market_provider_result = original_record

        failed = self.database.list_monitor_runs()[0]
        self.assertEqual(failed["status"], "failed")
        self.assertIsNotNone(failed["completed_at"])
        self.assertFalse(
            self.database.get_monitor_runtime_lock(
                name=MONITOR_LOCK_NAME, now=self.clock()
            )
        )
        recovered = await runner.run(trigger="manual")
        self.assertEqual(recovered["run"]["status"], "completed")

    async def test_startup_recovery_closes_orphaned_run_and_slot(self) -> None:
        run = self.database.create_monitor_run(
            provider=self.provider.name,
            requested_count=1,
            trigger="scheduled",
            trace_id="orphan-trace",
            scheduled_for=self.clock(),
            started_at=self.clock(),
        )
        self.database.claim_monitor_schedule_slot(
            slot_key="orphan-slot",
            trace_id="orphan-trace",
            scheduled_for=self.clock(),
            now=self.clock(),
        )
        self.database.update_monitor_schedule_slot(
            "orphan-slot", outcome="running", run_id=run["id"]
        )
        old_time = self.clock() - timedelta(seconds=120)
        self.database.acquire_monitor_runtime_lock(
            name=MONITOR_LOCK_NAME,
            owner_token="orphan-owner",
            trace_id="orphan-trace",
            now=old_time,
            ttl_seconds=60,
        )

        recovered = self.database.recover_interrupted_monitor_runtime(
            lock_name=MONITOR_LOCK_NAME,
            now=self.clock(),
            stale_slot_seconds=60,
        )

        self.assertEqual(recovered["recovered_run_count"], 1)
        self.assertEqual(recovered["recovered_slot_count"], 1)
        restored_run = self.database.get_monitor_run(run["id"])
        self.assertEqual(restored_run["status"], "failed")
        self.assertEqual(restored_run["trigger"], "scheduled")
        self.assertIn("process interruption", restored_run["errors"][0]["message"])
        self.assertIn(
            "finding evaluation may be incomplete",
            restored_run["finding_errors"][0]["message"],
        )
        slot = self.database.list_monitor_schedule_slots()[0]
        self.assertEqual(slot["outcome"], "failed_internal")


class TradingSessionPolicyTests(unittest.TestCase):
    def test_session_boundaries_weekends_holidays_and_unknown_year(self) -> None:
        policy = AshareTradingSessionPolicy(
            calendar_year=2026,
            holidays=HOLIDAYS,
            calendar_complete=True,
        )
        for value, expected in (
            (datetime(2026, 7, 15, 1, 29, tzinfo=timezone.utc), "outside_session"),
            (datetime(2026, 7, 15, 1, 30, tzinfo=timezone.utc), "open"),
            (datetime(2026, 7, 15, 3, 30, tzinfo=timezone.utc), "open"),
            (datetime(2026, 7, 15, 3, 31, tzinfo=timezone.utc), "outside_session"),
            (datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc), "open"),
            (datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc), "open"),
            (datetime(2026, 7, 15, 7, 1, tzinfo=timezone.utc), "outside_session"),
            (datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc), "weekend"),
            (datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc), "holiday"),
            (datetime(2027, 7, 15, 2, 0, tzinfo=timezone.utc), "calendar_unavailable"),
        ):
            with self.subTest(value=value):
                self.assertEqual(policy.decision(value).code, expected)

    def test_naive_timestamp_is_rejected(self) -> None:
        policy = AshareTradingSessionPolicy(
            calendar_year=2026,
            holidays=HOLIDAYS,
            calendar_complete=True,
        )
        with self.assertRaisesRegex(ValueError, "timezone"):
            policy.decision(datetime(2026, 7, 15, 10, 0))


class MonitorRuntimeSettingsTests(unittest.TestCase):
    def test_environment_defaults_keep_scheduler_disabled(self) -> None:
        settings = MonitorRuntimeSettings.from_environment({})
        self.assertFalse(settings.scheduler_enabled)
        self.assertFalse(settings.calendar_ready)
        self.assertEqual(settings.scheduled_retry_attempts, 2)

    def test_explicit_empty_environment_does_not_read_process_values(self) -> None:
        with patch.dict(
            os.environ,
            {"CATALYST_MONITOR_INTERVAL_SECONDS": "300"},
        ):
            settings = MonitorRuntimeSettings.from_environment({})
        self.assertFalse(settings.scheduler_enabled)

    def test_environment_validation_is_strict(self) -> None:
        invalid_environments = (
            {"CATALYST_MONITOR_INTERVAL_SECONDS": "30"},
            {"CATALYST_MONITOR_RETRY_ATTEMPTS": "4"},
            {"CATALYST_MONITOR_LOCK_SECONDS": "10"},
            {"CATALYST_MARKET_HOLIDAYS": "2026-01-01"},
            {
                "CATALYST_MARKET_CALENDAR_YEAR": "2026",
                "CATALYST_MARKET_HOLIDAYS": "2027-01-01",
            },
            {"CATALYST_MONITOR_CIRCUIT_SECONDS": "not-a-number"},
            {"CATALYST_MARKET_CALENDAR_COMPLETE": "maybe"},
            {"CATALYST_MARKET_CALENDAR_COMPLETE": "true"},
        )
        for environment in invalid_environments:
            with self.subTest(environment=environment):
                with self.assertRaises(ValueError):
                    MonitorRuntimeSettings.from_environment(environment)

        valid = MonitorRuntimeSettings.from_environment(
            {
                "CATALYST_MONITOR_INTERVAL_SECONDS": "300",
                "CATALYST_MONITOR_RETRY_ATTEMPTS": "3",
                "CATALYST_MARKET_CALENDAR_YEAR": "2026",
                "CATALYST_MARKET_HOLIDAYS": "2026-01-01, 2026-02-17",
                "CATALYST_MARKET_CALENDAR_COMPLETE": "true",
            }
        )
        self.assertTrue(valid.scheduler_enabled)
        self.assertTrue(valid.calendar_ready)
        self.assertEqual(len(valid.market_holidays), 2)


class LegacyMonitorRuntimeMigrationTests(unittest.TestCase):
    def test_pre_runtime_monitor_run_is_preserved_with_compatibility_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy-runtime.db"
            connection = sqlite3.connect(path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE monitor_runs (
                        id TEXT PRIMARY KEY,
                        trigger TEXT NOT NULL CHECK(trigger = 'manual'),
                        status TEXT NOT NULL CHECK(status IN (
                            'running', 'completed', 'partial', 'failed'
                        )),
                        provider TEXT NOT NULL,
                        requested_count INTEGER NOT NULL CHECK(requested_count >= 0),
                        success_count INTEGER NOT NULL DEFAULT 0 CHECK(success_count >= 0),
                        failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
                        errors_json TEXT NOT NULL DEFAULT '[]',
                        started_at TEXT NOT NULL,
                        completed_at TEXT
                    );
                    INSERT INTO monitor_runs (
                        id, trigger, status, provider, requested_count,
                        success_count, failure_count, errors_json,
                        started_at, completed_at
                    ) VALUES (
                        'legacy-run', 'manual', 'completed', 'legacy-provider',
                        1, 1, 0, '[]',
                        '2026-07-14T02:00:00+00:00',
                        '2026-07-14T02:00:01+00:00'
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()

            database = Database(path)
            database.initialize()
            database.initialize()

            legacy = database.get_monitor_run("legacy-run")
            self.assertEqual(legacy["trigger"], "manual")
            self.assertIsNone(legacy["trace_id"])
            self.assertIsNone(legacy["scheduled_for"])
            self.assertEqual(legacy["attempts"], [])
            self.assertEqual(legacy["finding_errors"], [])

            scheduled = database.create_monitor_run(
                provider="new-provider",
                requested_count=0,
                trigger="scheduled",
                trace_id="new-trace",
                scheduled_for=OPEN_TIME,
                started_at=OPEN_TIME,
            )
            self.assertEqual(scheduled["trigger"], "scheduled")
            self.assertEqual(scheduled["trace_id"], "new-trace")
            with database.session() as connection:
                cursor = connection.execute("PRAGMA foreign_key_check")
                foreign_key_errors = cursor.fetchall()
                cursor.close()
            self.assertEqual(foreign_key_errors, [])


if __name__ == "__main__":
    unittest.main()
