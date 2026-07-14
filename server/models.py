from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EvidenceOrigin = Literal["automatic", "manual"]
EvidenceStatus = Literal["pending", "accepted", "rejected"]
EvidenceReviewAction = Literal["created", "status_changed", "note_updated"]
EvidenceDirection = Literal["positive", "negative", "mixed", "neutral"]
SourceType = Literal[
    "exchange_announcement",
    "regulator_policy",
    "company_ir",
    "financial_report",
    "market_data",
    "peer_context",
    "trusted_media",
    "social",
    "counterevidence",
    "other",
]


class EvidenceReviewHistoryEntry(BaseModel):
    action: EvidenceReviewAction
    from_status: EvidenceStatus | None = None
    to_status: EvidenceStatus
    review_note: str = Field(default="", max_length=500)
    created_at: datetime


class WatchlistCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str
    company: str = Field(default="", max_length=100)
    enabled: bool = True

    @field_validator("stock_code", mode="before")
    @classmethod
    def validate_stock_code(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("stock code must be text")
        normalized = value.strip()
        if re.fullmatch(r"[0-9]{6}", normalized) is None:
            raise ValueError("stock code must contain exactly 6 ASCII digits")
        return normalized

    @field_validator("company", mode="before")
    @classmethod
    def normalize_company(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("company must be text")
        return value.strip()


class WatchlistPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)

    @field_validator("company", mode="before")
    @classmethod
    def normalize_company(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("company must be text")
        return value.strip()

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> "WatchlistPatch":
        for field in ("company", "enabled", "sort_order"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class CaseCreate(BaseModel):
    stock_code: str = Field(min_length=1, max_length=20)
    company: str = Field(default="", max_length=100)
    event_title: str = Field(default="", max_length=240)
    event_type: str = Field(default="policy", max_length=40)
    event_date: date | None = None
    query: str = Field(default="", max_length=240)

    @field_validator("stock_code")
    @classmethod
    def normalize_stock_code(cls, value: str) -> str:
        return value.strip().upper()


class CasePatch(BaseModel):
    stock_code: str | None = Field(default=None, min_length=1, max_length=20)
    company: str | None = Field(default=None, max_length=100)
    event_title: str | None = Field(default=None, max_length=240)
    event_type: str | None = Field(default=None, max_length=40)
    event_date: date | None = None
    query: str | None = Field(default=None, max_length=240)


class EvidenceCreate(BaseModel):
    source_type: SourceType = "other"
    source_name: str = Field(default="手动输入", max_length=120)
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(default="", max_length=2048)
    published_at: datetime | None = None
    quote: str = Field(default="", max_length=4000)
    claim: str = Field(default="", max_length=1000)
    direction: EvidenceDirection = "neutral"
    reliability: float = Field(default=3, ge=0, le=5)
    relevance: float = Field(default=3, ge=0, le=5)
    freshness: float = Field(default=3, ge=0, le=5)
    materiality: float = Field(default=3, ge=0, le=5)
    immediacy: float = Field(default=3, ge=0, le=5)
    novelty: float = Field(default=3, ge=0, le=5)
    market_alignment: float = Field(default=0, ge=0, le=5)
    priced_in_risk: float = Field(default=0, ge=0, le=5)
    counterevidence: float = Field(default=0, ge=0, le=5)
    status: EvidenceStatus = "accepted"
    reviewed_at: datetime | None = None
    review_note: str = Field(default="", max_length=500)
    review_history: list[EvidenceReviewHistoryEntry] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def validate_review_history(self) -> "EvidenceCreate":
        if not self.review_history:
            # Without an imported audit chain, the persistence layer owns the
            # initial review timestamp.
            self.reviewed_at = None
            return self

        for entry in self.review_history:
            if entry.created_at.tzinfo is None or entry.created_at.utcoffset() is None:
                raise ValueError("review history timestamps must include a timezone")

        first = self.review_history[0]
        if first.action != "created" or first.from_status is not None:
            raise ValueError("review history must start with created and no from_status")

        current_status = first.to_status
        previous_time = first.created_at
        for entry in self.review_history[1:]:
            if entry.action == "created":
                raise ValueError("review history can contain only one created entry")
            if entry.from_status != current_status:
                raise ValueError("review history status chain is not contiguous")
            if entry.created_at < previous_time:
                raise ValueError("review history timestamps must be nondecreasing")
            if entry.action == "status_changed" and entry.to_status == current_status:
                raise ValueError("status_changed must change the evidence status")
            if entry.action == "note_updated" and entry.to_status != current_status:
                raise ValueError("note_updated cannot change the evidence status")
            current_status = entry.to_status
            previous_time = entry.created_at

        if current_status != self.status:
            raise ValueError("review history final status must match evidence status")

        latest_review = next(
            (
                entry
                for entry in reversed(self.review_history)
                if entry.action != "created"
            ),
            None,
        )
        self.reviewed_at = (
            latest_review.created_at
            if latest_review
            else (first.created_at if self.status != "pending" else None)
        )
        self.review_note = self.review_history[-1].review_note
        return self


class EvidencePatch(BaseModel):
    source_type: SourceType | None = None
    source_name: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    url: str | None = Field(default=None, max_length=2048)
    published_at: datetime | None = None
    quote: str | None = Field(default=None, max_length=4000)
    claim: str | None = Field(default=None, max_length=1000)
    direction: EvidenceDirection | None = None
    reliability: float | None = Field(default=None, ge=0, le=5)
    relevance: float | None = Field(default=None, ge=0, le=5)
    freshness: float | None = Field(default=None, ge=0, le=5)
    materiality: float | None = Field(default=None, ge=0, le=5)
    immediacy: float | None = Field(default=None, ge=0, le=5)
    novelty: float | None = Field(default=None, ge=0, le=5)
    market_alignment: float | None = Field(default=None, ge=0, le=5)
    priced_in_risk: float | None = Field(default=None, ge=0, le=5)
    counterevidence: float | None = Field(default=None, ge=0, le=5)
    status: EvidenceStatus | None = None
    review_note: str | None = Field(default=None, max_length=500)


class DiscoveryRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    query: str = Field(default="", max_length=120)
    limit: int = Field(default=20, ge=1, le=30)


class MetricOverrides(BaseModel):
    source_reliability: float | None = Field(default=None, ge=0, le=5)
    materiality: float | None = Field(default=None, ge=0, le=5)
    immediacy: float | None = Field(default=None, ge=0, le=5)
    novelty: float | None = Field(default=None, ge=0, le=5)
    confirmation: float | None = Field(default=None, ge=0, le=5)
    market_alignment: float | None = Field(default=None, ge=0, le=5)
    priced_in_risk: float | None = Field(default=None, ge=0, le=5)
    counterevidence: float | None = Field(default=None, ge=0, le=5)


class ScoreRequest(BaseModel):
    metrics: MetricOverrides | None = None
