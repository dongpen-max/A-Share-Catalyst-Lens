#!/usr/bin/env python3
"""Score structured China-stock catalyst evidence.

Input JSON:
{
  "events": [
    {
      "title": "Company wins large order",
      "source_reliability": 5,
      "materiality": 4,
      "immediacy": 3,
      "novelty": 4,
      "confirmation": 4,
      "market_alignment": 3,
      "priced_in_risk": 2,
      "counterevidence": 1,
      "notes": "Official announcement, sector up."
    }
  ]
}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


WEIGHTS = {
    "source_reliability": 20,
    "materiality": 20,
    "immediacy": 10,
    "novelty": 10,
    "confirmation": 10,
    "market_alignment": 10,
}
PENALTIES = {
    "priced_in_risk": 10,
    "counterevidence": 10,
}


def clamp(value: Any, low: float = 0.0, high: float = 5.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return min(max(number, low), high)


def grade(score: float) -> str:
    if score >= 80:
        return "strong_positive"
    if score >= 65:
        return "constructive"
    if score >= 50:
        return "mixed_or_weak_positive"
    if score >= 35:
        return "low_confidence"
    return "not_bullish_or_negative"


def confidence(event: dict[str, Any], score: float) -> str:
    reliability = clamp(event.get("source_reliability"))
    confirmation = clamp(event.get("confirmation"))
    penalty = clamp(event.get("counterevidence")) + clamp(event.get("priced_in_risk"))
    if score >= 75 and reliability >= 4 and confirmation >= 4 and penalty <= 4:
        return "High"
    if score >= 55 and reliability >= 3 and confirmation >= 3 and penalty <= 6:
        return "Medium"
    return "Low"


def score_event(event: dict[str, Any]) -> dict[str, Any]:
    score = 0.0
    components: dict[str, float] = {}

    for key, weight in WEIGHTS.items():
        component = clamp(event.get(key)) / 5.0 * weight
        components[key] = round(component, 2)
        score += component

    for key, weight in PENALTIES.items():
        component = clamp(event.get(key)) / 5.0 * weight
        components[key] = round(-component, 2)
        score -= component

    score = min(max(score, 0.0), 100.0)
    return {
        "title": event.get("title") or event.get("claim") or "Untitled event",
        "score": round(score, 1),
        "grade": grade(score),
        "confidence": confidence(event, score),
        "components": components,
        "notes": event.get("notes", ""),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"event_count": 0, "average_score": 0.0, "overall_grade": "no_events"}
    average = sum(item["score"] for item in results) / len(results)
    return {
        "event_count": len(results),
        "average_score": round(average, 1),
        "overall_grade": grade(average),
        "highest_score": max(item["score"] for item in results),
        "lowest_score": min(item["score"] for item in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score China stock catalyst evidence.")
    parser.add_argument("input_json", help="Path to a JSON file containing an events array.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args()

    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    events = payload.get("events", [])
    if not isinstance(events, list):
        raise SystemExit("input JSON must contain an events array")

    results = [score_event(event) for event in events if isinstance(event, dict)]
    output = {"summary": summarize(results), "events": results}
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
