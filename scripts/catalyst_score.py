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
import sys
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
METRIC_FIELDS = tuple(WEIGHTS) + tuple(PENALTIES)


def clamp(value: Any, low: float = 0.0, high: float = 5.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return min(max(number, low), high)


def metric_value(event: dict[str, Any], key: str, index: int, warnings: list[str]) -> float:
    raw = event.get(key)
    if raw is None:
        warnings.append(f"events[{index}].{key} is missing; using 0")
        return 0.0
    try:
        number = float(raw)
    except (TypeError, ValueError):
        warnings.append(f"events[{index}].{key} is not numeric; using 0")
        return 0.0
    if number < 0 or number > 5:
        clamped = clamp(number)
        warnings.append(f"events[{index}].{key}={number:g} is outside 0-5; clamped to {clamped:g}")
        return clamped
    return number


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


def confidence(metrics: dict[str, float], score: float) -> str:
    reliability = metrics["source_reliability"]
    confirmation = metrics["confirmation"]
    penalty = metrics["counterevidence"] + metrics["priced_in_risk"]
    if score >= 75 and reliability >= 4 and confirmation >= 4 and penalty <= 4:
        return "High"
    if score >= 55 and reliability >= 3 and confirmation >= 3 and penalty <= 6:
        return "Medium"
    return "Low"


def score_event(event: dict[str, Any], index: int = 0, warnings: list[str] | None = None) -> dict[str, Any]:
    warnings = warnings if warnings is not None else []
    metrics = {key: metric_value(event, key, index, warnings) for key in METRIC_FIELDS}
    score = 0.0
    components: dict[str, float] = {}

    for key, weight in WEIGHTS.items():
        component = metrics[key] / 5.0 * weight
        components[key] = round(component, 2)
        score += component

    for key, weight in PENALTIES.items():
        component = metrics[key] / 5.0 * weight
        components[key] = round(-component, 2)
        score -= component

    score = min(max(score, 0.0), 100.0)
    return {
        "title": event.get("title") or event.get("claim") or "Untitled event",
        "score": round(score, 1),
        "grade": grade(score),
        "confidence": confidence(metrics, score),
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


def read_payload(input_path: str) -> dict[str, Any]:
    if input_path == "-":
        text = sys.stdin.read()
    else:
        text = Path(input_path).read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object")
    return payload


def score_payload(payload: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    if "events" not in payload:
        warnings.append("events array is missing; using []")
        events = []
    else:
        events = payload["events"]
    if not isinstance(events, list):
        raise ValueError("input JSON must contain an events array")

    results: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            warnings.append(f"events[{index}] is not an object; skipped")
            continue
        results.append(score_event(event, index=index, warnings=warnings))

    output = {"summary": summarize(results), "events": results}
    if warnings:
        output["warnings"] = warnings
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Score China stock catalyst evidence.")
    parser.add_argument("input_json", help="Path to a JSON file containing an events array, or '-' for stdin.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--strict", action="store_true", help="Exit with code 2 when validation warnings are found.")
    args = parser.parse_args()

    try:
        output = score_payload(read_payload(args.input_json))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    warnings = output.get("warnings", [])
    if args.strict and warnings:
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        return 2

    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
