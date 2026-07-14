from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.services.cninfo import ANNOUNCEMENT_URL, TOP_SEARCH_URL, CninfoConnector


class FakeConnector:
    name = "fake-cninfo"

    async def discover(self, **_kwargs):
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
        app = create_app(
            db_path=Path(self.temp_dir.name) / "test.db",
            connector=FakeConnector(),
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
