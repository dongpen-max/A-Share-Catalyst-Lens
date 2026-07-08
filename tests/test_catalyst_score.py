from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import catalyst_score  # noqa: E402


class CatalystScoreTests(unittest.TestCase):
    def test_sample_event_score_is_stable(self) -> None:
        event = {
            "title": "Company announces a verified large order",
            "source_reliability": 5,
            "materiality": 4,
            "immediacy": 4,
            "novelty": 3,
            "confirmation": 4,
            "market_alignment": 3,
            "priced_in_risk": 2,
            "counterevidence": 1,
        }

        result = catalyst_score.score_event(event)

        self.assertEqual(result["score"], 58.0)
        self.assertEqual(result["grade"], "mixed_or_weak_positive")
        self.assertEqual(result["confidence"], "Medium")
        self.assertEqual(result["components"]["priced_in_risk"], -4.0)

    def test_invalid_metrics_emit_warnings(self) -> None:
        output = catalyst_score.score_payload(
            {
                "events": [
                    {
                        "title": "Invalid event",
                        "source_reliability": 9,
                        "materiality": "bad",
                    }
                ]
            }
        )

        warnings = output.get("warnings", [])
        self.assertTrue(any("source_reliability=9" in warning for warning in warnings))
        self.assertTrue(any("materiality is not numeric" in warning for warning in warnings))
        self.assertTrue(any("immediacy is missing" in warning for warning in warnings))

    def test_cli_strict_rejects_invalid_metrics(self) -> None:
        payload = json.dumps({"events": [{"source_reliability": 6}]})
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "catalyst_score.py"), "-", "--strict"],
            input=payload,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("source_reliability=6", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
