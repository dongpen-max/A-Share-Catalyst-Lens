from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


EvidenceOrigin = Literal["automatic", "manual"]
EvidenceStatus = Literal["pending", "accepted", "rejected"]
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
