from __future__ import annotations

import hashlib
import math
from datetime import datetime
from statistics import median
from typing import Any

from server.services.market import ASIA_SHANGHAI


RULE_VERSION = "1"
CHANGE_PERCENT_THRESHOLD = 5.0
VOLUME_RATIO_THRESHOLD = 2.0
VOLUME_BASELINE_MIN_DATES = 3
VOLUME_BASELINE_MAX_DATES = 20
VOLUME_TIME_BUCKET_MINUTES = 30

CHANGE_PERCENT_RULE = "change_percent_threshold"
VOLUME_RATIO_RULE = "volume_ratio"


def evaluate_market_snapshot(
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    change_percent_threshold: float = CHANGE_PERCENT_THRESHOLD,
    volume_ratio_threshold: float = VOLUME_RATIO_THRESHOLD,
) -> list[dict[str, Any]]:
    if change_percent_threshold <= 0 or volume_ratio_threshold <= 0:
        raise ValueError("monitor thresholds must be positive")
    if snapshot.get("data_quality") == "unavailable":
        return []

    findings: list[dict[str, Any]] = []
    change_percent = _finite_number(snapshot.get("change_percent"))
    if (
        change_percent is not None
        and abs(change_percent) >= change_percent_threshold
    ):
        findings.append(
            _finding_payload(
                snapshot,
                rule_type=CHANGE_PERCENT_RULE,
                direction=(
                    "positive"
                    if change_percent > 0
                    else ("negative" if change_percent < 0 else "neutral")
                ),
                observed_value=change_percent,
                threshold_value=change_percent_threshold,
                baseline_value=None,
                baseline_count=0,
                details={
                    "price": _finite_number(snapshot.get("price")),
                    "change_percent": change_percent,
                    "absolute_change_percent": abs(change_percent),
                },
            )
        )

    volume_finding = _volume_finding(
        snapshot,
        history,
        volume_ratio_threshold=volume_ratio_threshold,
    )
    if volume_finding is not None:
        findings.append(volume_finding)
    return findings


def evidence_candidate_from_finding(finding: dict[str, Any]) -> dict[str, Any]:
    rule_type = finding["rule_type"]
    details = finding.get("details") or {}
    stock_code = finding["stock_code"]
    provider = finding["provider"]
    provider_timestamp = finding.get("provider_timestamp")
    fetched_at = finding["fetched_at"]
    threshold = float(finding["threshold_value"])

    if rule_type == CHANGE_PERCENT_RULE:
        change_percent = float(finding["observed_value"])
        title = f"{stock_code} 涨跌幅触发 {threshold:.2f}% 阈值"
        claim = (
            f"服务商记录涨跌幅 {change_percent:+.2f}%，"
            f"绝对值达到 {threshold:.2f}% 规则阈值。"
        )
        quote = claim
        market_alignment = 4
    elif rule_type == VOLUME_RATIO_RULE:
        ratio = float(finding["observed_value"])
        baseline_count = int(finding.get("baseline_count") or 0)
        title = f"{stock_code} 成交量触发 {threshold:.2f} 倍阈值"
        claim = (
            f"当前成交量为同时间段历史中位数的 {ratio:.2f} 倍，"
            f"达到 {threshold:.2f} 倍规则阈值，基线含 {baseline_count} 个日期。"
        )
        quote = claim
        market_alignment = 3
    else:
        raise ValueError(f"unsupported monitor rule: {rule_type}")

    freshness = 2 if details.get("is_stale") is True else 4
    return {
        "source_type": "market_data",
        "source_name": f"Catalyst Watch · {provider}",
        "title": title,
        "url": "",
        "published_at": provider_timestamp or fetched_at,
        "fetched_at": fetched_at,
        "quote": quote,
        "claim": claim,
        "direction": finding["direction"],
        "reliability": 3,
        "relevance": 4,
        "freshness": freshness,
        "materiality": 0,
        "immediacy": 2,
        "novelty": 3,
        "market_alignment": market_alignment,
        "priced_in_risk": 0,
        "counterevidence": 0,
        "status": "pending",
        "content_hash": hashlib.sha256(
            f"monitor-finding:{finding['id']}".encode("utf-8")
        ).hexdigest(),
        "metadata": {
            "monitor_finding_id": finding["id"],
            "snapshot_id": finding["snapshot_id"],
            "run_id": finding["run_id"],
            "stock_code": stock_code,
            "provider": provider,
            "provider_timestamp": provider_timestamp,
            "rule_type": rule_type,
            "rule_version": finding["rule_version"],
            "observed_value": finding["observed_value"],
            "threshold_value": finding["threshold_value"],
            "baseline_value": finding.get("baseline_value"),
            "baseline_count": finding.get("baseline_count") or 0,
            "dedupe_key": finding["dedupe_key"],
            "details": details,
        },
    }


def _volume_finding(
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    volume_ratio_threshold: float,
) -> dict[str, Any] | None:
    current_volume = _finite_number(snapshot.get("volume"))
    current_time = _aware_datetime(snapshot.get("provider_timestamp"))
    if current_volume is None or current_volume <= 0 or current_time is None:
        return None

    current_local = current_time.astimezone(ASIA_SHANGHAI)
    current_bucket = _time_bucket(current_local)
    candidates_by_date: dict[str, tuple[float, datetime, dict[str, Any]]] = {}
    for candidate in history:
        if candidate.get("id") == snapshot.get("id"):
            continue
        if candidate.get("provider") != snapshot.get("provider"):
            continue
        if candidate.get("data_quality") == "unavailable":
            continue
        candidate_volume = _finite_number(candidate.get("volume"))
        candidate_time = _aware_datetime(candidate.get("provider_timestamp"))
        if candidate_volume is None or candidate_volume <= 0 or candidate_time is None:
            continue
        candidate_local = candidate_time.astimezone(ASIA_SHANGHAI)
        if candidate_local.date() >= current_local.date():
            continue
        if _time_bucket(candidate_local) != current_bucket:
            continue

        date_key = candidate_local.date().isoformat()
        minute_distance = abs(
            _minute_of_day(candidate_local) - _minute_of_day(current_local)
        )
        existing = candidates_by_date.get(date_key)
        if existing is None or minute_distance < existing[0]:
            candidates_by_date[date_key] = (
                minute_distance,
                candidate_time,
                candidate,
            )

    selected = sorted(
        candidates_by_date.values(),
        key=lambda item: item[1],
        reverse=True,
    )[:VOLUME_BASELINE_MAX_DATES]
    if len(selected) < VOLUME_BASELINE_MIN_DATES:
        return None

    baseline_snapshots = [item[2] for item in selected]
    baseline_volumes = [float(item["volume"]) for item in baseline_snapshots]
    baseline_value = float(median(baseline_volumes))
    if baseline_value <= 0:
        return None
    ratio = current_volume / baseline_value
    if ratio < volume_ratio_threshold:
        return None

    return _finding_payload(
        snapshot,
        rule_type=VOLUME_RATIO_RULE,
        direction="neutral",
        observed_value=ratio,
        threshold_value=volume_ratio_threshold,
        baseline_value=baseline_value,
        baseline_count=len(baseline_snapshots),
        details={
            "volume": current_volume,
            "volume_ratio": ratio,
            "baseline_median_volume": baseline_value,
            "baseline_snapshot_ids": [item["id"] for item in baseline_snapshots],
            "time_bucket_minutes": VOLUME_TIME_BUCKET_MINUTES,
        },
    )


def _finding_payload(
    snapshot: dict[str, Any],
    *,
    rule_type: str,
    direction: str,
    observed_value: float,
    threshold_value: float,
    baseline_value: float | None,
    baseline_count: int,
    details: dict[str, Any],
) -> dict[str, Any]:
    observation_key = snapshot.get("provider_timestamp") or snapshot["id"]
    dedupe_material = "|".join(
        (
            snapshot["stock_code"],
            snapshot["provider"],
            str(observation_key),
            rule_type,
            RULE_VERSION,
        )
    )
    return {
        "snapshot_id": snapshot["id"],
        "run_id": snapshot["run_id"],
        "watchlist_item_id": snapshot.get("watchlist_item_id"),
        "stock_code": snapshot["stock_code"],
        "company": snapshot.get("company") or "",
        "provider": snapshot["provider"],
        "provider_timestamp": snapshot.get("provider_timestamp"),
        "fetched_at": snapshot["fetched_at"],
        "rule_type": rule_type,
        "rule_version": RULE_VERSION,
        "direction": direction,
        "observed_value": observed_value,
        "threshold_value": threshold_value,
        "baseline_value": baseline_value,
        "baseline_count": baseline_count,
        "details": {
            **details,
            "is_stale": snapshot.get("is_stale"),
            "stale_seconds": snapshot.get("stale_seconds"),
            "data_quality": snapshot.get("data_quality"),
        },
        "dedupe_key": hashlib.sha256(dedupe_material.encode("utf-8")).hexdigest(),
    }


def _aware_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _time_bucket(value: datetime) -> int:
    return _minute_of_day(value) // VOLUME_TIME_BUCKET_MINUTES
