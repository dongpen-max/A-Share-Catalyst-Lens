from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.services.cninfo import ANNOUNCEMENT_URL, TOP_SEARCH_URL, CninfoConnector


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


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.connector = FakeConnector()
        app = create_app(
            db_path=self.db_path,
            connector=self.connector,
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
        self.assertEqual(health.json()["connectors"], ["fake-cninfo"])

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
