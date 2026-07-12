from __future__ import annotations

from datetime import datetime, timezone
from statistics import fmean
from typing import Any

from scripts.catalyst_score import score_event


METRIC_FIELDS = (
    "source_reliability",
    "materiality",
    "immediacy",
    "novelty",
    "confirmation",
    "market_alignment",
    "priced_in_risk",
    "counterevidence",
)


def clamp(value: float, low: float = 0, high: float = 5) -> float:
    return min(max(float(value), low), high)


def weighted_average(items: list[dict[str, Any]], field: str) -> float:
    if not items:
        return 0
    weights = [max(float(item.get("relevance") or 0), 0.5) for item in items]
    total = sum(float(item.get(field) or 0) * weight for item, weight in zip(items, weights))
    return clamp(total / sum(weights))


def recency_score(value: str | None) -> float:
    if not value:
        return 1
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = max((datetime.now(timezone.utc) - published.astimezone(timezone.utc)).days, 0)
    except ValueError:
        return 1
    if age_days <= 7:
        return 5
    if age_days <= 30:
        return 4
    if age_days <= 90:
        return 3
    if age_days <= 180:
        return 2
    return 1


def derive_metrics(evidence: list[dict[str, Any]]) -> dict[str, float]:
    if not evidence:
        return {field: 0 for field in METRIC_FIELDS}

    source_names = {item.get("source_name") or item.get("source_type") for item in evidence}
    confirmation = min(5, 1.5 + max(len(source_names) - 1, 0) * 1.5 + max(len(evidence) - 1, 0) * 0.5)
    negative_items = [item for item in evidence if item.get("direction") in {"negative", "mixed"}]

    return {
        "source_reliability": weighted_average(evidence, "reliability"),
        "materiality": max(float(item.get("materiality") or 0) for item in evidence),
        "immediacy": weighted_average(evidence, "immediacy"),
        "novelty": weighted_average(evidence, "novelty"),
        "confirmation": clamp(confirmation),
        "market_alignment": weighted_average(evidence, "market_alignment"),
        "priced_in_risk": max(float(item.get("priced_in_risk") or 0) for item in evidence),
        "counterevidence": max(
            [float(item.get("counterevidence") or item.get("materiality") or 0) for item in negative_items]
            or [0]
        ),
    }


def evidence_confidence(evidence: list[dict[str, Any]]) -> float:
    if not evidence:
        return 0
    reliability = fmean(float(item.get("reliability") or 0) for item in evidence) / 5
    relevance = fmean(float(item.get("relevance") or 0) for item in evidence) / 5
    freshness = fmean(float(item.get("freshness") or recency_score(item.get("published_at"))) for item in evidence) / 5
    source_diversity = min(len({item.get("source_name") or item.get("source_type") for item in evidence}) / 3, 1)
    citation_completeness = fmean(
        (1 if item.get("url") else 0) * 0.5 + (1 if item.get("quote") else 0) * 0.5 for item in evidence
    )
    directions = {item.get("direction") for item in evidence}
    contradiction_penalty = 10 if "positive" in directions and "negative" in directions else 0
    score = (
        reliability * 35
        + relevance * 25
        + freshness * 15
        + source_diversity * 15
        + citation_completeness * 10
        - contradiction_penalty
    )
    return round(min(max(score, 0), 100), 1)


def coverage(evidence: list[dict[str, Any]]) -> tuple[float, dict[str, bool]]:
    source_types = {item.get("source_type") for item in evidence}
    details = {
        "official_source": bool(
            source_types & {"exchange_announcement", "regulator_policy", "company_ir", "financial_report"}
        ),
        "financial_impact": any(float(item.get("materiality") or 0) >= 3 for item in evidence),
        "market_data": "market_data" in source_types,
        "peer_context": "peer_context" in source_types,
        "counterevidence": any(
            item.get("direction") in {"negative", "mixed"}
            or item.get("source_type") == "counterevidence"
            or float(item.get("counterevidence") or 0) > 0
            for item in evidence
        ),
    }
    return float(sum(20 for present in details.values() if present)), details


def score_case(
    case: dict[str, Any],
    all_evidence: list[dict[str, Any]],
    overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    accepted = [item for item in all_evidence if item.get("status") == "accepted"]
    metrics = derive_metrics(accepted)
    for key, value in (overrides or {}).items():
        if key in METRIC_FIELDS and value is not None:
            metrics[key] = clamp(value)

    event = {"title": case.get("event_title") or case.get("company") or case.get("stock_code"), **metrics}
    catalyst = score_event(event)
    coverage_score, coverage_details = coverage(accepted)
    confidence_score = evidence_confidence(accepted)
    warnings: list[str] = []
    if not accepted:
        warnings.append("没有已接受证据，当前评分仅能作为空白基线。")
    if coverage_score < 60:
        warnings.append("资料覆盖度低于 60，建议补充行情、同行或反证材料。")
    if confidence_score < 60 and accepted:
        warnings.append("证据可信度低于 60，建议增加一手来源和交叉确认。")

    return {
        "case_id": case["id"],
        "catalyst_score": catalyst["score"],
        "grade": catalyst["grade"],
        "confidence": catalyst["confidence"],
        "evidence_confidence": confidence_score,
        "coverage_score": coverage_score,
        "coverage": coverage_details,
        "metrics": {key: round(value, 2) for key, value in metrics.items()},
        "components": catalyst["components"],
        "accepted_evidence_count": len(accepted),
        "total_evidence_count": len(all_evidence),
        "warnings": warnings,
    }
