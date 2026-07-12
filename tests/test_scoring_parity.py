from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import catalyst_score  # noqa: E402


NODE_RUNNER = r"""
const scoring = require('./web/scoring.js');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => {
  process.stdout.write(JSON.stringify(scoring.scorePayload(JSON.parse(input))));
});
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required for cross-language parity tests")
class ScoringParityTests(unittest.TestCase):
    def compare_payload(self, payload: dict) -> None:
        python_output = catalyst_score.score_payload(payload)
        result = subprocess.run(
            ["node", "-e", NODE_RUNNER],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), python_output)

    def test_example_file_matches(self) -> None:
        payload = json.loads((ROOT / "examples" / "events.json").read_text(encoding="utf-8"))
        self.compare_payload(payload)

    def test_invalid_and_missing_metrics_match(self) -> None:
        self.compare_payload(
            {
                "events": [
                    {
                        "title": "Invalid values",
                        "source_reliability": 9,
                        "materiality": "bad",
                    },
                    None,
                ]
            }
        )


if __name__ == "__main__":
    unittest.main()
