from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.database import Database
from server.services.cninfo import ANNOUNCEMENT_URL, TOP_SEARCH_URL, CninfoConnector
from server.services.market import MarketProviderError, MarketQuote


class FakeConnector:
    name = "fake-cninfo"

    def __init__(self) -> None:
        self.discover_calls = 0

    async def discover(self, **_kwargs):
        self.discover_calls += 1
        return [
            {
                "source_type": "exchange_announcement",
                "source_name": "巨潮资讯",
                "title": "重大合同公告",
                "url": "https://static.cninfo.com.cn/demo.pdf",
                "published_at": "2026-07-12T00:00:00+00:00",
                "quote": "重大合同公告",
                "claim": "公司发布重大合同公告",
                "direction": "positive",
                "reliability": 5,
                "relevance": 4,
                "freshness": 5,
                "materiality": 4,
                "immediacy": 5,
                "novelty": 5,
                "market_alignment": 0,
                "priced_in_risk": 0,
                "counterevidence": 0,
                "status": "accepted",  # The persistence boundary must override this.
                "content_hash": "official-demo-1",
                "metadata": {"announcement_id": "demo-1"},
            }
        ]


class FakeMarketProvider:
    name = "fake-market"

    def __init__(
        self,
        *,
        quotes: dict[str, MarketQuote] | None = None,
        failures: dict[str, str] | None = None,
    ) -> None:
        self.quotes = quotes or {}
        self.failures = failures or {}
        self.calls: list[str] = []

    async def fetch_quote(self, stock_code: str) -> MarketQuote:
        self.calls.append(stock_code)
        if stock_code in self.failures:
            raise MarketProviderError(self.failures[stock_code])
        if stock_code in self.quotes:
            return self.quotes[stock_code]
        return MarketQuote(
            stock_code=stock_code,
            price=10.5,
            change_percent=1.2,
            volume=123_400,
            turnover=2_500_000,
            provider_timestamp=datetime.now(timezone.utc),
        )


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.connector = FakeConnector()
        self.market_provider = FakeMarketProvider()
        app = create_app(
            db_path=self.db_path,
            connector=self.connector,
            market_provider=self.market_provider,
        )
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def create_case(self) -> dict:
        response = self.client.post(
            "/api/cases",
            json={
                "stock_code": "000001",
                "company": "平安银行",
                "event_title": "测试公告",
                "event_type": "order",
                "event_date": "2026-07-12",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_health_and_case_lifecycle(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["version"], "0.5.0")
        self.assertEqual(health.json()["connectors"], ["fake-cninfo"])
        self.assertEqual(health.json()["market_provider"], "fake-market")

        case = self.create_case()
        patched = self.client.patch(
            f"/api/cases/{case['id']}", json={"query": "重大合同"}
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["query"], "重大合同")

        deleted = self.client.delete(f"/api/cases/{case['id']}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(f"/api/cases/{case['id']}").status_code, 404)

    def test_watchlist_lifecycle_ordering_and_persistence(self) -> None:
        created_items = []
        for stock_code, company in [
            ("600519", "贵州茅台"),
            ("000001", "平安银行"),
            ("832982", "锦波生物"),
        ]:
            response = self.client.post(
                "/api/watchlist",
                json={"stock_code": stock_code, "company": company},
            )
            self.assertEqual(response.status_code, 201)
            payload = response.json()
            self.assertTrue(payload["created"])
            created_items.append(payload["item"])

        self.assertEqual([item["sort_order"] for item in created_items], [0, 1, 2])
        self.assertTrue(all(item["enabled"] is True for item in created_items))

        disabled = self.client.patch(
            f"/api/watchlist/{created_items[1]['id']}", json={"enabled": False}
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertIs(disabled.json()["enabled"], False)

        moved = self.client.patch(
            f"/api/watchlist/{created_items[2]['id']}", json={"sort_order": 0}
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.json()["sort_order"], 0)
        ordered = self.client.get("/api/watchlist").json()["items"]
        self.assertEqual(
            [item["stock_code"] for item in ordered],
            ["832982", "600519", "000001"],
        )
        self.assertEqual([item["sort_order"] for item in ordered], [0, 1, 2])

        deleted = self.client.delete(f"/api/watchlist/{created_items[0]['id']}")
        self.assertEqual(deleted.status_code, 204)
        remaining = self.client.get("/api/watchlist").json()["items"]
        self.assertEqual(
            [item["stock_code"] for item in remaining], ["832982", "000001"]
        )
        self.assertEqual([item["sort_order"] for item in remaining], [0, 1])

        second_app = create_app(db_path=self.db_path, connector=FakeConnector())
        with TestClient(second_app) as second_client:
            persisted = second_client.get("/api/watchlist").json()["items"]
        self.assertEqual(
            [(item["stock_code"], item["enabled"]) for item in persisted],
            [("832982", True), ("000001", False)],
        )
        self.assertEqual([item["sort_order"] for item in persisted], [0, 1])
        self.assertEqual(self.connector.discover_calls, 0)

    def test_manual_market_refresh_records_complete_and_partial_snapshots(self) -> None:
        now = datetime.now(timezone.utc)
        self.market_provider.quotes = {
            "600519": MarketQuote(
                stock_code="600519",
                company="贵州茅台",
                price=1420.5,
                change_percent=2.35,
                volume=345_600,
                turnover=490_000_000,
                provider_timestamp=now,
            ),
            "000001": MarketQuote(
                stock_code="000001",
                company="平安银行",
                price=12.34,
                change_percent=-0.8,
                volume=None,
                turnover=None,
                provider_timestamp=None,
            ),
        }
        created = []
        for stock_code, company in [
            ("600519", "贵州茅台"),
            ("000001", "平安银行"),
            ("832982", "锦波生物"),
        ]:
            created.append(
                self.client.post(
                    "/api/watchlist",
                    json={"stock_code": stock_code, "company": company},
                ).json()["item"]
            )
        self.client.patch(
            f"/api/watchlist/{created[2]['id']}", json={"enabled": False}
        )

        self.assertEqual(self.client.get("/api/monitor/runs").json()["items"], [])
        self.assertEqual(self.client.get("/api/monitor/latest").json()["items"], [])
        self.assertEqual(self.market_provider.calls, [])

        response = self.client.post("/api/monitor/refresh", json={})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(self.market_provider.calls, ["600519", "000001"])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["run"]["trigger"], "manual")
        self.assertEqual(payload["run"]["status"], "completed")
        self.assertEqual(payload["run"]["requested_count"], 2)
        self.assertEqual(payload["run"]["success_count"], 2)
        self.assertEqual(payload["run"]["failure_count"], 0)

        snapshots = {item["stock_code"]: item for item in payload["items"]}
        complete = snapshots["600519"]
        self.assertEqual(complete["provider"], "fake-market")
        self.assertEqual(complete["price"], 1420.5)
        self.assertEqual(complete["change_percent"], 2.35)
        self.assertEqual(complete["volume"], 345_600)
        self.assertEqual(complete["turnover"], 490_000_000)
        self.assertIsNotNone(complete["fetched_at"])
        self.assertIsNotNone(complete["provider_timestamp"])
        self.assertIs(complete["is_stale"], False)
        self.assertGreaterEqual(complete["stale_seconds"], 0)
        self.assertIsNone(complete["fallback_from"])
        self.assertEqual(complete["data_quality"], "ok")
        self.assertEqual(complete["missing_fields"], [])

        partial = snapshots["000001"]
        self.assertEqual(partial["data_quality"], "partial")
        self.assertEqual(
            partial["missing_fields"],
            ["volume", "turnover", "provider_timestamp"],
        )
        self.assertIsNone(partial["volume"])
        self.assertIsNone(partial["turnover"])
        self.assertIsNone(partial["provider_timestamp"])
        self.assertIsNone(partial["is_stale"])
        self.assertIsNone(partial["stale_seconds"])

        run_id = payload["run"]["id"]
        runs = self.client.get("/api/monitor/runs").json()["items"]
        self.assertEqual([run["id"] for run in runs], [run_id])
        listed = self.client.get(
            "/api/monitor/snapshots", params={"run_id": run_id}
        ).json()["items"]
        self.assertEqual({item["stock_code"] for item in listed}, {"600519", "000001"})

        persisted_provider = FakeMarketProvider()
        second_app = create_app(
            db_path=self.db_path,
            connector=FakeConnector(),
            market_provider=persisted_provider,
        )
        with TestClient(second_app) as second_client:
            latest = second_client.get("/api/monitor/latest").json()
        self.assertEqual(latest["run"]["id"], run_id)
        self.assertEqual(
            {item["stock_code"] for item in latest["items"]}, {"600519", "000001"}
        )
        self.assertEqual(persisted_provider.calls, [])

    def test_refresh_survives_watchlist_delete_while_provider_is_waiting(self) -> None:
        class BlockingMarketProvider(FakeMarketProvider):
            def __init__(self) -> None:
                super().__init__()
                self.fetch_started = threading.Event()
                self.release_fetch = threading.Event()

            async def fetch_quote(self, stock_code: str) -> MarketQuote:
                self.fetch_started.set()
                await asyncio.to_thread(self.release_fetch.wait)
                return await super().fetch_quote(stock_code)

        provider = BlockingMarketProvider()
        self.client.app.state.market_provider = provider
        watchlist_item = self.client.post(
            "/api/watchlist", json={"stock_code": "600519", "company": "贵州茅台"}
        ).json()["item"]

        with ThreadPoolExecutor(max_workers=1) as pool:
            refresh_future = pool.submit(
                self.client.post, "/api/monitor/refresh", json={}
            )
            try:
                self.assertTrue(provider.fetch_started.wait(timeout=5))
                deleted = self.client.delete(f"/api/watchlist/{watchlist_item['id']}")
                self.assertEqual(deleted.status_code, 204)
            finally:
                provider.release_fetch.set()
            refresh = refresh_future.result(timeout=5)

        self.assertEqual(refresh.status_code, 200)
        payload = refresh.json()
        self.assertEqual(payload["run"]["status"], "completed")
        self.assertEqual(payload["run"]["success_count"], 1)
        self.assertEqual(payload["run"]["failure_count"], 0)
        snapshots = self.client.get(
            "/api/monitor/snapshots", params={"run_id": payload["run"]["id"]}
        ).json()["items"]
        self.assertEqual(len(snapshots), 1)
        self.assertIsNone(snapshots[0]["watchlist_item_id"])
        self.assertEqual(snapshots[0]["stock_code"], "600519")
        self.assertEqual(self.client.get("/api/watchlist").json()["items"], [])

    def test_manual_market_refresh_requires_an_empty_json_object(self) -> None:
        invalid_requests = (
            lambda: self.client.post("/api/monitor/refresh"),
            lambda: self.client.post(
                "/api/monitor/refresh",
                content="{}",
                headers={"Content-Type": "text/plain"},
            ),
            lambda: self.client.post(
                "/api/monitor/refresh", data={"unexpected": "value"}
            ),
            lambda: self.client.post(
                "/api/monitor/refresh", json={"unexpected": "value"}
            ),
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                self.assertEqual(request().status_code, 422)
        self.assertEqual(self.client.get("/api/monitor/runs").json()["items"], [])
        self.assertEqual(
            self.client.post("/api/monitor/refresh", json={}).status_code,
            200,
        )

    def test_unavailable_snapshot_is_audited_without_replacing_last_good(self) -> None:
        self.client.post("/api/watchlist", json={"stock_code": "600519"})
        first = self.client.post("/api/monitor/refresh", json={}).json()
        last_good = first["items"][0]
        self.market_provider.quotes["600519"] = MarketQuote(stock_code="600519")

        unavailable = self.client.post("/api/monitor/refresh", json={}).json()

        self.assertEqual(unavailable["items"], [])
        self.assertEqual(
            unavailable["errors"],
            [
                {
                    "stock_code": "600519",
                    "company": "",
                    "message": "market data unavailable",
                }
            ],
        )
        run = unavailable["run"]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["requested_count"], 1)
        self.assertEqual(run["success_count"], 0)
        self.assertEqual(run["failure_count"], 1)
        self.assertEqual(
            run["requested_count"], run["success_count"] + run["failure_count"]
        )
        audited = self.client.get(
            "/api/monitor/snapshots", params={"run_id": run["id"]}
        ).json()["items"]
        self.assertEqual(len(audited), 1)
        self.assertEqual(audited[0]["data_quality"], "unavailable")
        self.assertIsNone(audited[0]["is_stale"])
        self.assertEqual(
            self.client.get("/api/monitor/latest").json()["items"][0]["id"],
            last_good["id"],
        )

        second_app = create_app(
            db_path=self.db_path,
            connector=FakeConnector(),
            market_provider=FakeMarketProvider(),
        )
        with TestClient(second_app) as second_client:
            restarted = second_client.get("/api/monitor/latest").json()
        self.assertEqual(restarted["run"]["id"], run["id"])
        self.assertEqual(restarted["items"][0]["id"], last_good["id"])

    def test_first_unavailable_snapshot_makes_the_run_all_failed(self) -> None:
        self.market_provider.quotes["000001"] = MarketQuote(stock_code="000001")
        self.client.post("/api/watchlist", json={"stock_code": "000001"})

        payload = self.client.post("/api/monitor/refresh", json={}).json()

        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["run"]["status"], "failed")
        self.assertEqual(payload["run"]["requested_count"], 1)
        self.assertEqual(payload["run"]["success_count"], 0)
        self.assertEqual(payload["run"]["failure_count"], 1)
        self.assertEqual(
            payload["run"]["requested_count"],
            payload["run"]["success_count"] + payload["run"]["failure_count"],
        )
        self.assertEqual(self.client.get("/api/monitor/latest").json()["items"], [])
        audited = self.client.get(
            "/api/monitor/snapshots", params={"run_id": payload["run"]["id"]}
        ).json()["items"]
        self.assertEqual([item["data_quality"] for item in audited], ["unavailable"])

    def test_manual_market_refresh_isolates_single_stock_failure(self) -> None:
        for stock_code in ("600519", "000001", "832982"):
            self.client.post("/api/watchlist", json={"stock_code": stock_code})
        first = self.client.post("/api/monitor/refresh", json={}).json()
        previous_snapshot = next(
            item for item in first["items"] if item["stock_code"] == "000001"
        )
        self.market_provider.calls.clear()
        self.market_provider.failures = {"000001": "upstream unavailable"}

        payload = self.client.post("/api/monitor/refresh", json={}).json()

        self.assertEqual(self.market_provider.calls, ["600519", "000001", "832982"])
        self.assertEqual(payload["run"]["status"], "partial")
        self.assertEqual(payload["run"]["requested_count"], 3)
        self.assertEqual(payload["run"]["success_count"], 2)
        self.assertEqual(payload["run"]["failure_count"], 1)
        self.assertEqual(
            payload["run"]["requested_count"],
            payload["run"]["success_count"] + payload["run"]["failure_count"],
        )
        self.assertEqual(
            {item["stock_code"] for item in payload["items"]}, {"600519", "832982"}
        )
        self.assertEqual(
            payload["errors"],
            [
                {
                    "stock_code": "000001",
                    "company": "",
                    "message": "upstream unavailable",
                }
            ],
        )
        latest = self.client.get("/api/monitor/latest").json()
        self.assertEqual(latest["errors"], payload["errors"])
        self.assertEqual(latest["run"]["errors"], payload["errors"])
        latest_snapshots = {item["stock_code"]: item for item in latest["items"]}
        self.assertEqual(set(latest_snapshots), {"600519", "000001", "832982"})
        self.assertEqual(latest_snapshots["000001"]["id"], previous_snapshot["id"])
        self.assertEqual(latest_snapshots["600519"]["run_id"], payload["run"]["id"])

    def test_database_failure_is_not_reported_as_provider_failure(self) -> None:
        self.client.post("/api/watchlist", json={"stock_code": "600519"})
        database = self.client.app.state.database
        original_add_snapshot = database.add_market_snapshot

        def fail_to_store(*_args, **_kwargs):
            raise sqlite3.OperationalError("database unavailable")

        database.add_market_snapshot = fail_to_store
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.client.post("/api/monitor/refresh", json={})
        finally:
            database.add_market_snapshot = original_add_snapshot

        run = self.client.get("/api/monitor/latest").json()["run"]
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["errors"], [])

    def test_manual_market_refresh_handles_empty_watchlist_without_provider_call(self) -> None:
        payload = self.client.post("/api/monitor/refresh", json={}).json()

        self.assertEqual(self.market_provider.calls, [])
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["run"]["status"], "completed")
        self.assertEqual(payload["run"]["requested_count"], 0)
        self.assertEqual(payload["run"]["success_count"], 0)
        self.assertEqual(payload["run"]["failure_count"], 0)
        self.assertIsNotNone(payload["run"]["completed_at"])

    def test_manual_market_refresh_does_not_create_evidence_or_change_scoring(self) -> None:
        case = self.create_case()
        evidence = self.client.post(
            f"/api/cases/{case['id']}/evidence",
            json={"title": "人工核验材料", "status": "accepted"},
        ).json()["item"]
        before = self.client.post(f"/api/cases/{case['id']}/score", json={}).json()
        self.client.post(
            "/api/watchlist",
            json={"stock_code": "600519", "company": "贵州茅台"},
        )

        refresh = self.client.post("/api/monitor/refresh", json={})
        self.assertEqual(refresh.status_code, 200)

        evidence_after = self.client.get(
            f"/api/cases/{case['id']}/evidence"
        ).json()["items"]
        self.assertEqual([item["id"] for item in evidence_after], [evidence["id"]])
        after = self.client.post(f"/api/cases/{case['id']}/score", json={}).json()
        for field in (
            "accepted_evidence_count",
            "total_evidence_count",
            "catalyst_score",
            "evidence_confidence",
            "coverage_score",
        ):
            self.assertEqual(after[field], before[field])
        self.assertEqual(self.connector.discover_calls, 0)

    def test_watchlist_duplicate_is_idempotent(self) -> None:
        first = self.client.post(
            "/api/watchlist",
            json={"stock_code": " 600519 ", "company": " 贵州茅台 "},
        )
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()["created"])
        self.assertEqual(first.json()["item"]["stock_code"], "600519")
        self.assertEqual(first.json()["item"]["company"], "贵州茅台")

        duplicate = self.client.post(
            "/api/watchlist",
            json={"stock_code": "600519", "company": "不会覆盖"},
        )
        self.assertEqual(duplicate.status_code, 201)
        self.assertFalse(duplicate.json()["created"])
        self.assertEqual(duplicate.json()["item"]["company"], "贵州茅台")
        self.assertEqual(len(self.client.get("/api/watchlist").json()["items"]), 1)
        self.assertEqual(self.connector.discover_calls, 0)

    def test_watchlist_validation_and_missing_items(self) -> None:
        invalid_codes = [
            "60051",
            "6005190",
            "ABCDEF",
            "600519.SH",
            "６００５１９",
            "600 519",
            600519,
            None,
        ]
        for stock_code in invalid_codes:
            with self.subTest(stock_code=stock_code):
                response = self.client.post(
                    "/api/watchlist", json={"stock_code": stock_code}
                )
                self.assertEqual(response.status_code, 422)

        too_long_company = self.client.post(
            "/api/watchlist",
            json={"stock_code": "600519", "company": "公" * 101},
        )
        self.assertEqual(too_long_company.status_code, 422)

        item = self.client.post(
            "/api/watchlist", json={"stock_code": "600519"}
        ).json()["item"]
        self.assertEqual(
            self.client.patch(
                f"/api/watchlist/{item['id']}", json={"sort_order": -1}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.patch(
                f"/api/watchlist/{item['id']}", json={"stock_code": "000001"}
            ).status_code,
            422,
        )
        for field in ("company", "enabled", "sort_order"):
            with self.subTest(null_field=field):
                self.assertEqual(
                    self.client.patch(
                        f"/api/watchlist/{item['id']}", json={field: None}
                    ).status_code,
                    422,
                )
        self.assertEqual(
            self.client.patch("/api/watchlist/missing", json={"enabled": False}).status_code,
            404,
        )
        self.assertEqual(self.client.delete("/api/watchlist/missing").status_code, 404)
        self.assertEqual(self.connector.discover_calls, 0)

    def test_automatic_evidence_requires_review_before_scoring(self) -> None:
        case = self.create_case()
        case_id = case["id"]
        manual = self.client.post(
            f"/api/cases/{case_id}/evidence",
            json={
                "source_type": "market_data",
                "source_name": "手动行情记录",
                "title": "个股与板块同步放量",
                "url": "https://example.com/market",
                "quote": "成交量明显放大",
                "claim": "市场行为支持事件逻辑",
                "direction": "positive",
                "reliability": 3,
                "relevance": 4,
                "freshness": 5,
                "materiality": 3,
                "immediacy": 4,
                "novelty": 3,
                "market_alignment": 4,
                "status": "accepted",
            },
        )
        self.assertEqual(manual.status_code, 201)
        self.assertTrue(manual.json()["created"])

        discovery = self.client.post(
            f"/api/cases/{case_id}/discover",
            json={"query": "重大合同", "limit": 10},
        )
        self.assertEqual(discovery.status_code, 200)
        discovery_payload = discovery.json()
        self.assertEqual(discovery_payload["created_count"], 1)
        automatic = discovery_payload["items"][0]
        self.assertEqual(automatic["status"], "pending")

        pending_score = self.client.post(
            f"/api/cases/{case_id}/score",
            json={},
        )
        self.assertEqual(pending_score.status_code, 200)
        pending_payload = pending_score.json()
        self.assertEqual(pending_payload["accepted_evidence_count"], 1)
        self.assertEqual(pending_payload["total_evidence_count"], 2)
        self.assertFalse(pending_payload["coverage"]["official_source"])

        duplicate = self.client.post(
            f"/api/cases/{case_id}/discover", json={"limit": 10}
        )
        self.assertEqual(duplicate.json()["created_count"], 0)
        self.assertEqual(duplicate.json()["duplicate_count"], 1)
        self.assertEqual(duplicate.json()["items"][0]["status"], "pending")

        accepted = self.client.patch(
            f"/api/evidence/{automatic['id']}", json={"status": "accepted"}
        )
        self.assertEqual(accepted.status_code, 200)

        score = self.client.post(
            f"/api/cases/{case_id}/score",
            json={"metrics": {"priced_in_risk": 1}},
        )
        self.assertEqual(score.status_code, 200)
        payload = score.json()
        self.assertEqual(payload["accepted_evidence_count"], 2)
        self.assertTrue(payload["coverage"]["official_source"])
        self.assertTrue(payload["coverage"]["market_data"])
        self.assertGreaterEqual(payload["coverage_score"], 60)
        self.assertGreater(payload["evidence_confidence"], 0)

    def test_manual_url_rejects_non_http_scheme(self) -> None:
        case = self.create_case()
        response = self.client.post(
            f"/api/cases/{case['id']}/evidence",
            json={"title": "Unsafe", "url": "file:///etc/passwd"},
        )
        self.assertEqual(response.status_code, 422)

    def test_review_history_tracks_decisions_and_notes(self) -> None:
        case = self.create_case()
        discovery = self.client.post(
            f"/api/cases/{case['id']}/discover", json={"limit": 10}
        )
        evidence = discovery.json()["items"][0]
        self.assertEqual(evidence["status"], "pending")
        self.assertIsNone(evidence["reviewed_at"])
        self.assertEqual(
            [entry["action"] for entry in evidence["review_history"]], ["created"]
        )

        accepted = self.client.patch(
            f"/api/evidence/{evidence['id']}",
            json={"status": "accepted", "review_note": "已核对公告原文"},
        )
        self.assertEqual(accepted.status_code, 200)
        accepted_item = accepted.json()
        self.assertEqual(accepted_item["review_note"], "已核对公告原文")
        self.assertIsNotNone(accepted_item["reviewed_at"])
        self.assertEqual(accepted_item["review_history"][-1]["action"], "status_changed")
        self.assertEqual(accepted_item["review_history"][-1]["from_status"], "pending")
        self.assertEqual(accepted_item["review_history"][-1]["to_status"], "accepted")

        noted = self.client.patch(
            f"/api/evidence/{evidence['id']}",
            json={"status": "accepted", "review_note": "二次复核：数字一致"},
        )
        self.assertEqual(noted.status_code, 200)
        self.assertEqual(noted.json()["review_history"][-1]["action"], "note_updated")

        rejected = self.client.patch(
            f"/api/evidence/{evidence['id']}",
            json={"status": "rejected", "review_note": "后续公告已撤回"},
        )
        self.assertEqual(rejected.status_code, 200)
        rejected_item = rejected.json()
        self.assertEqual(rejected_item["review_history"][-1]["action"], "status_changed")
        self.assertEqual(rejected_item["review_history"][-1]["from_status"], "accepted")
        self.assertEqual(rejected_item["review_history"][-1]["to_status"], "rejected")

        listed = self.client.get(f"/api/cases/{case['id']}/evidence").json()["items"][0]
        self.assertEqual(len(listed["review_history"]), 4)
        score = self.client.post(f"/api/cases/{case['id']}/score", json={}).json()
        self.assertEqual(score["accepted_evidence_count"], 0)

    def test_manual_evidence_imports_local_review_history(self) -> None:
        case = self.create_case()
        response = self.client.post(
            f"/api/cases/{case['id']}/evidence",
            json={
                "title": "离线审核后同步的证据",
                "status": "accepted",
                "review_history": [
                    {
                        "action": "created",
                        "from_status": None,
                        "to_status": "pending",
                        "created_at": "2099-07-14T08:00:00+08:00",
                    },
                    {
                        "action": "status_changed",
                        "from_status": "pending",
                        "to_status": "accepted",
                        "review_note": "离线核对完成",
                        "created_at": "2099-07-14T00:00:00+00:00",
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        item = response.json()["item"]
        self.assertEqual(item["review_note"], "离线核对完成")
        self.assertEqual(len(item["review_history"]), 2)
        self.assertEqual(item["review_history"][0]["action"], "created")
        self.assertEqual(item["review_history"][-1]["to_status"], "accepted")
        self.assertEqual(item["reviewed_at"], item["review_history"][-1]["created_at"])

        reviewed = self.client.patch(
            f"/api/evidence/{item['id']}", json={"status": "rejected"}
        ).json()
        self.assertEqual(
            [entry["action"] for entry in reviewed["review_history"]],
            ["created", "status_changed", "status_changed"],
        )
        self.assertEqual(reviewed["review_history"][-1]["from_status"], "accepted")
        self.assertEqual(reviewed["review_history"][-1]["to_status"], "rejected")
        self.assertEqual(reviewed["status"], "rejected")

    def test_manual_evidence_rejects_inconsistent_review_history(self) -> None:
        case = self.create_case()
        histories = [
            [
                {
                    "action": "note_updated",
                    "from_status": "pending",
                    "to_status": "pending",
                    "created_at": "2026-07-14T07:30:00+00:00",
                }
            ],
            [
                {
                    "action": "created",
                    "from_status": None,
                    "to_status": "pending",
                    "created_at": "2026-07-14T07:30:00+00:00",
                },
                {
                    "action": "status_changed",
                    "from_status": "accepted",
                    "to_status": "rejected",
                    "created_at": "2026-07-14T08:00:00+00:00",
                },
            ],
            [
                {
                    "action": "created",
                    "from_status": None,
                    "to_status": "pending",
                    "created_at": "2026-07-14T07:30:00+00:00",
                },
                {
                    "action": "status_changed",
                    "from_status": "pending",
                    "to_status": "pending",
                    "created_at": "2026-07-14T08:00:00+00:00",
                },
            ],
            [
                {
                    "action": "created",
                    "from_status": None,
                    "to_status": "pending",
                    "created_at": "2026-07-14T08:00:00+00:00",
                },
                {
                    "action": "note_updated",
                    "from_status": "pending",
                    "to_status": "accepted",
                    "created_at": "2026-07-14T07:30:00+00:00",
                },
            ],
            [
                {
                    "action": "created",
                    "from_status": None,
                    "to_status": "pending",
                    "created_at": "2026-07-14T07:30:00+00:00",
                }
            ],
            [
                {
                    "action": "created",
                    "from_status": None,
                    "to_status": "pending",
                    "created_at": "2026-07-14T08:00:00+00:00",
                },
                {
                    "action": "status_changed",
                    "from_status": "pending",
                    "to_status": "accepted",
                    "created_at": "2026-07-14T07:30:00+00:00",
                },
            ],
            [
                {
                    "action": "created",
                    "from_status": None,
                    "to_status": "accepted",
                    "created_at": "2026-07-14T07:30:00",
                }
            ],
        ]

        for index, history in enumerate(histories):
            with self.subTest(index=index):
                response = self.client.post(
                    f"/api/cases/{case['id']}/evidence",
                    json={
                        "title": f"不一致审核历史 {index}",
                        "status": "accepted",
                        "review_history": history,
                    },
                )
                self.assertEqual(response.status_code, 422)

    def test_concurrent_reviews_keep_a_contiguous_status_chain(self) -> None:
        case = self.create_case()
        discovery = self.client.post(
            f"/api/cases/{case['id']}/discover", json={"limit": 10}
        )
        evidence_id = discovery.json()["items"][0]["id"]
        database = self.client.app.state.database

        for _ in range(10):
            current = database.get_evidence(evidence_id)
            if current["status"] != "pending":
                database.update_evidence(evidence_id, {"status": "pending"})

            barrier = threading.Barrier(3)

            def review(status: str) -> dict:
                barrier.wait()
                return database.update_evidence(evidence_id, {"status": status})

            with ThreadPoolExecutor(max_workers=2) as pool:
                accepted = pool.submit(review, "accepted")
                rejected = pool.submit(review, "rejected")
                barrier.wait()
                accepted.result(timeout=5)
                rejected.result(timeout=5)

            updated = database.get_evidence(evidence_id)
            history = updated["review_history"]
            previous_status = history[0]["to_status"]
            for entry in history[1:]:
                self.assertEqual(entry["from_status"], previous_status)
                previous_status = entry["to_status"]
            self.assertEqual(updated["status"], previous_status)


class DatabaseMigrationTests(unittest.TestCase):
    @staticmethod
    def replace_with_legacy_snapshot_table(
        database: Database, *, quality_check: bool = True
    ) -> None:
        constraint = (
            "CHECK(data_quality IN ('complete', 'partial'))"
            if quality_check
            else ""
        )
        with database.session() as connection:
            connection.execute("DROP TABLE market_snapshots")
            connection.executescript(
                f"""
                CREATE TABLE market_snapshots (
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
                    is_stale INTEGER NOT NULL CHECK(is_stale IN (0, 1)),
                    stale_seconds INTEGER CHECK(stale_seconds >= 0),
                    fallback_from TEXT,
                    data_quality TEXT NOT NULL {constraint},
                    missing_fields_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX market_snapshots_run_fetched
                    ON market_snapshots(run_id, fetched_at DESC, id);
                CREATE INDEX market_snapshots_stock_fetched
                    ON market_snapshots(stock_code, fetched_at DESC, id);
                """
            )

    def test_legacy_snapshot_schema_and_data_are_migrated_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "legacy.db")
            database.initialize()
            watchlist_item, _created = database.create_watchlist_item(
                {"stock_code": "600519", "company": "贵州茅台", "enabled": True}
            )
            run = database.create_monitor_run(provider="legacy", requested_count=1)
            self.replace_with_legacy_snapshot_table(database)
            legacy_id = "legacy-snapshot-id"
            with database.session() as connection:
                connection.execute(
                    """
                    INSERT INTO market_snapshots (
                        id, run_id, watchlist_item_id, stock_code, company, provider,
                        price, change_percent, volume, turnover, fetched_at,
                        provider_timestamp, is_stale, stale_seconds, fallback_from,
                        data_quality, missing_fields_json
                    ) VALUES (?, ?, ?, '600519', '贵州茅台', 'legacy', 10, 0,
                              100, 1000, '2026-07-15T07:00:00+00:00',
                              '2026-07-15T07:00:00+00:00', 0, 0, NULL,
                              'complete', '[]')
                    """,
                    (legacy_id, run["id"], watchlist_item["id"]),
                )

            database.initialize()

            migrated = database.list_market_snapshots(run_id=run["id"])
            self.assertEqual([item["id"] for item in migrated], [legacy_id])
            self.assertEqual(migrated[0]["data_quality"], "ok")
            self.assertIs(migrated[0]["is_stale"], False)
            with database.session() as connection:
                columns = {
                    row["name"]: row
                    for row in connection.execute("PRAGMA table_info(market_snapshots)")
                }
                indexes = {
                    row["name"]
                    for row in connection.execute("PRAGMA index_list(market_snapshots)")
                }
                foreign_tables = {
                    row["table"]
                    for row in connection.execute(
                        "PRAGMA foreign_key_list(market_snapshots)"
                    )
                }
                table_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'market_snapshots'"
                ).fetchone()["sql"]
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(columns["is_stale"]["notnull"], 0)
            self.assertTrue(
                {"market_snapshots_run_fetched", "market_snapshots_stock_fetched"}
                <= indexes
            )
            self.assertEqual(foreign_tables, {"monitor_runs", "watchlist_items"})
            for quality in ("ok", "partial", "unavailable"):
                self.assertIn(f"'{quality}'", table_sql)

            nullable = database.add_market_snapshot(
                run["id"],
                watchlist_item,
                provider="legacy",
                payload={
                    "price": None,
                    "change_percent": None,
                    "volume": None,
                    "turnover": None,
                    "fetched_at": "2026-07-15T08:00:00+00:00",
                    "provider_timestamp": None,
                    "is_stale": None,
                    "stale_seconds": None,
                    "fallback_from": None,
                    "data_quality": "unavailable",
                    "missing_fields": [
                        "price",
                        "change_percent",
                        "volume",
                        "turnover",
                        "provider_timestamp",
                    ],
                },
            )
            self.assertIsNone(nullable["is_stale"])
            self.assertEqual(nullable["data_quality"], "unavailable")

    def test_failed_snapshot_migration_rolls_back_the_original_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "rollback.db")
            database.initialize()
            watchlist_item, _created = database.create_watchlist_item(
                {"stock_code": "600519", "company": "", "enabled": True}
            )
            run = database.create_monitor_run(provider="legacy", requested_count=1)
            self.replace_with_legacy_snapshot_table(database, quality_check=False)
            with database.session() as connection:
                connection.execute(
                    """
                    INSERT INTO market_snapshots (
                        id, run_id, stock_code, company, provider, fetched_at,
                        is_stale, data_quality, missing_fields_json
                    ) VALUES ('must-survive', ?, '600519', '', 'legacy',
                              '2026-07-15T07:00:00+00:00', 0, 'broken', '[]')
                    """,
                    (run["id"],),
                )

            with self.assertRaises(sqlite3.IntegrityError):
                database.initialize()

            with database.session() as connection:
                original = connection.execute(
                    "SELECT id, data_quality FROM market_snapshots"
                ).fetchone()
                temporary = connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'market_snapshots_v2'"
                ).fetchone()
                table_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'market_snapshots'"
                ).fetchone()["sql"]
            self.assertEqual(dict(original), {"id": "must-survive", "data_quality": "broken"})
            self.assertIsNone(temporary)
            self.assertIn("is_stale INTEGER NOT NULL", table_sql)


class CninfoNormalizationTests(unittest.TestCase):
    def test_market_and_direction_classification(self) -> None:
        self.assertEqual(CninfoConnector.normalize_code("SZSE:000001"), "000001")
        self.assertEqual(CninfoConnector.market_parameters("000001"), ("szse", "sz"))
        self.assertEqual(CninfoConnector.market_parameters("600000"), ("sse", "sh"))
        self.assertEqual(CninfoConnector.direction("关于股份回购的公告"), "positive")
        self.assertEqual(CninfoConnector.direction("关于立案调查的公告"), "negative")
        self.assertEqual(CninfoConnector.direction("关于重大合同解除的公告"), "mixed")
        self.assertEqual(CninfoConnector.direction("关于合同进展的公告"), "neutral")

    def test_announcement_normalization(self) -> None:
        item = {
            "secCode": "000001",
            "secName": "平安银行",
            "announcementTitle": "<em>2025年年度权益分派实施公告</em>",
            "announcementTime": 1780588800000,
            "adjunctUrl": "finalpage/2026-06-05/1225352449.PDF",
            "announcementId": "1225352449",
            "orgId": "gssz0000001",
        }
        result = CninfoConnector.normalize_announcement(
            item,
            query="权益分派",
            company={"code": "000001", "zwjc": "平安银行", "orgId": "gssz0000001"},
        )
        self.assertEqual(result["direction"], "positive")
        self.assertEqual(result["reliability"], 5)
        self.assertNotIn("<em>", result["title"])
        self.assertTrue(result["url"].startswith("https://static.cninfo.com.cn/"))
        self.assertEqual(result["status"], "pending")

    def test_discovery_passes_query_to_cninfo_search(self) -> None:
        class RecordingConnector(CninfoConnector):
            def __init__(self) -> None:
                super().__init__(minimum_interval=0)
                self.calls: list[tuple[str, dict[str, str]]] = []

            async def _post_json(self, _client, url, data):
                self.calls.append((url, data))
                if url == TOP_SEARCH_URL:
                    return [{"code": "000001", "orgId": "gssz0000001", "zwjc": "平安银行"}]
                return {"announcements": []}

        connector = RecordingConnector()
        result = asyncio.run(
            connector.discover(stock_code="000001", query="权益分派", limit=10)
        )
        self.assertEqual(result, [])
        announcement_call = next(call for call in connector.calls if call[0] == ANNOUNCEMENT_URL)
        self.assertEqual(announcement_call[1]["searchkey"], "权益分派")


if __name__ == "__main__":
    unittest.main()
