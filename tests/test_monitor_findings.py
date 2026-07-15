from __future__ import annotations

import unittest

from server.services.findings import (
    CHANGE_PERCENT_RULE,
    VOLUME_RATIO_RULE,
    evaluate_market_snapshot,
    evidence_candidate_from_finding,
)


def snapshot(
    *,
    snapshot_id: str,
    provider_timestamp: str | None,
    change_percent: float | None = 0,
    volume: float | None = 100,
    stock_code: str = "600519",
) -> dict:
    return {
        "id": snapshot_id,
        "run_id": f"run-{snapshot_id}",
        "watchlist_item_id": "watchlist-1",
        "stock_code": stock_code,
        "company": "示例公司",
        "provider": "test-provider",
        "price": 10,
        "change_percent": change_percent,
        "volume": volume,
        "turnover": 1_000,
        "fetched_at": "2026-07-15T02:15:05+00:00",
        "provider_timestamp": provider_timestamp,
        "is_stale": False,
        "stale_seconds": 5,
        "fallback_from": None,
        "data_quality": "ok",
        "missing_fields": [],
    }


class MonitorFindingRuleTests(unittest.TestCase):
    def test_change_threshold_preserves_signed_direction_and_exact_boundary(self) -> None:
        for change_percent, direction in ((5, "positive"), (-6.25, "negative")):
            with self.subTest(change_percent=change_percent):
                current = snapshot(
                    snapshot_id=f"change-{change_percent}",
                    provider_timestamp="2026-07-15T02:15:00+00:00",
                    change_percent=change_percent,
                )
                findings = evaluate_market_snapshot(current, [])

                self.assertEqual(len(findings), 1)
                finding = findings[0]
                self.assertEqual(finding["rule_type"], CHANGE_PERCENT_RULE)
                self.assertEqual(finding["direction"], direction)
                self.assertEqual(finding["observed_value"], change_percent)
                self.assertEqual(finding["threshold_value"], 5)

        self.assertEqual(
            evaluate_market_snapshot(
                snapshot(
                    snapshot_id="below-threshold",
                    provider_timestamp="2026-07-15T02:15:00+00:00",
                    change_percent=4.99,
                ),
                [],
            ),
            [],
        )

    def test_dedupe_key_uses_provider_observation_not_local_snapshot_id(self) -> None:
        first = snapshot(
            snapshot_id="first",
            provider_timestamp="2026-07-15T02:15:00+00:00",
            change_percent=7,
        )
        second = snapshot(
            snapshot_id="second",
            provider_timestamp="2026-07-15T02:15:00+00:00",
            change_percent=7,
        )

        first_finding = evaluate_market_snapshot(first, [second])[0]
        second_finding = evaluate_market_snapshot(second, [first])[0]

        self.assertEqual(first_finding["dedupe_key"], second_finding["dedupe_key"])

    def test_volume_ratio_uses_distinct_prior_dates_in_same_time_bucket(self) -> None:
        current = snapshot(
            snapshot_id="current",
            provider_timestamp="2026-07-15T02:15:00+00:00",
            volume=250,
        )
        history = [
            snapshot(
                snapshot_id="day-1",
                provider_timestamp="2026-07-14T02:10:00+00:00",
                volume=100,
            ),
            snapshot(
                snapshot_id="day-2",
                provider_timestamp="2026-07-11T02:20:00+00:00",
                volume=110,
            ),
            snapshot(
                snapshot_id="day-3",
                provider_timestamp="2026-07-10T02:05:00+00:00",
                volume=90,
            ),
            snapshot(
                snapshot_id="same-day",
                provider_timestamp="2026-07-15T02:00:00+00:00",
                volume=1,
            ),
            snapshot(
                snapshot_id="other-bucket",
                provider_timestamp="2026-07-09T03:05:00+00:00",
                volume=1,
            ),
        ]

        findings = evaluate_market_snapshot(current, history)
        volume_finding = next(
            item for item in findings if item["rule_type"] == VOLUME_RATIO_RULE
        )

        self.assertEqual(volume_finding["observed_value"], 2.5)
        self.assertEqual(volume_finding["baseline_value"], 100)
        self.assertEqual(volume_finding["baseline_count"], 3)
        self.assertEqual(
            set(volume_finding["details"]["baseline_snapshot_ids"]),
            {"day-1", "day-2", "day-3"},
        )

    def test_volume_rule_requires_three_comparable_dates(self) -> None:
        current = snapshot(
            snapshot_id="current",
            provider_timestamp="2026-07-15T02:15:00+00:00",
            volume=1_000,
        )
        history = [
            snapshot(
                snapshot_id="day-1",
                provider_timestamp="2026-07-14T02:10:00+00:00",
                volume=100,
            ),
            snapshot(
                snapshot_id="day-2",
                provider_timestamp="2026-07-11T02:20:00+00:00",
                volume=100,
            ),
        ]

        self.assertEqual(evaluate_market_snapshot(current, history), [])

    def test_evidence_candidate_is_pending_market_data_with_trace_metadata(self) -> None:
        current = snapshot(
            snapshot_id="change",
            provider_timestamp="2026-07-15T02:15:00+00:00",
            change_percent=-8,
        )
        finding = evaluate_market_snapshot(current, [])[0]
        finding["id"] = "finding-1"

        candidate = evidence_candidate_from_finding(finding)

        self.assertEqual(candidate["source_type"], "market_data")
        self.assertEqual(candidate["status"], "pending")
        self.assertEqual(candidate["direction"], "negative")
        self.assertEqual(candidate["metadata"]["monitor_finding_id"], "finding-1")
        self.assertEqual(candidate["metadata"]["snapshot_id"], "change")
        self.assertIn("规则阈值", candidate["claim"])


if __name__ == "__main__":
    unittest.main()
