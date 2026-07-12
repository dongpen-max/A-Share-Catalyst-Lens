from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from server.scoring import coverage, derive_metrics, evidence_confidence


ROOT = Path(__file__).resolve().parents[1]
NODE_RUNNER = r"""
const evidence = require('./web/evidence.js');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => {
  const payload = JSON.parse(input);
  process.stdout.write(JSON.stringify(evidence.analyze(payload.items, payload.overrides)));
});
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required for cross-language parity tests")
class EvidenceParityTests(unittest.TestCase):
    def test_browser_and_server_evidence_analysis_match(self) -> None:
        items = [
            {
                "status": "accepted",
                "source_type": "exchange_announcement",
                "source_name": "巨潮资讯",
                "direction": "positive",
                "url": "https://static.cninfo.com.cn/demo.pdf",
                "quote": "重大合同公告",
                "reliability": 5,
                "relevance": 4,
                "freshness": 5,
                "materiality": 4,
                "immediacy": 5,
                "novelty": 5,
                "market_alignment": 0,
                "priced_in_risk": 0,
                "counterevidence": 0,
            },
            {
                "status": "accepted",
                "source_type": "market_data",
                "source_name": "手动行情记录",
                "direction": "positive",
                "url": "https://example.com/market",
                "quote": "个股与板块同步放量",
                "reliability": 3,
                "relevance": 4,
                "freshness": 5,
                "materiality": 3,
                "immediacy": 4,
                "novelty": 3,
                "market_alignment": 4,
                "priced_in_risk": 1,
                "counterevidence": 0,
            },
            {
                "status": "pending",
                "source_type": "trusted_media",
                "source_name": "待审核来源",
                "direction": "negative",
                "reliability": 1,
                "relevance": 1,
                "freshness": 1,
                "materiality": 5,
            },
        ]
        overrides = {"market_alignment": 5}
        accepted = [item for item in items if item["status"] == "accepted"]
        metrics = derive_metrics(accepted)
        metrics.update(overrides)
        coverage_score, coverage_details = coverage(accepted)
        expected = {
            "metrics": {key: round(value, 2) for key, value in metrics.items()},
            "evidence_confidence": evidence_confidence(accepted),
            "coverage_score": coverage_score,
            "coverage": coverage_details,
            "accepted_evidence_count": 2,
            "total_evidence_count": 3,
        }

        result = subprocess.run(
            ["node", "-e", NODE_RUNNER],
            cwd=ROOT,
            input=json.dumps({"items": items, "overrides": overrides}, ensure_ascii=False),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)


if __name__ == "__main__":
    unittest.main()
