from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import httpx


TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={symbol}"
STALE_AFTER_SECONDS = 15 * 60
MIN_TENCENT_FIELDS = 45
ASIA_SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


class MarketProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MarketQuote:
    stock_code: str
    company: str = ""
    price: float | None = None
    change_percent: float | None = None
    volume: float | None = None
    turnover: float | None = None
    provider_timestamp: datetime | None = None
    fallback_from: str | None = None


class MarketDataProvider(Protocol):
    name: str

    async def fetch_quote(self, stock_code: str) -> MarketQuote: ...


class TencentMarketProvider:
    """Fetch one A-share quote from Tencent's fixed HTTPS endpoint.

    The normalized contract uses CNY for price/turnover and shares for volume.
    """

    name = "tencent"

    def __init__(
        self,
        *,
        timeout: float = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport

    async def fetch_quote(self, stock_code: str) -> MarketQuote:
        code = self._normalize_code(stock_code)
        symbol = self._symbol(code)
        headers = {
            "Accept": "text/plain",
            "Referer": "https://finance.qq.com/",
            "User-Agent": "A-Share-Catalyst-Lens/0.5",
        }
        try:
            async with httpx.AsyncClient(
                headers=headers,
                timeout=self.timeout,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.get(TENCENT_QUOTE_URL.format(symbol=symbol))
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MarketProviderError(f"Tencent quote request failed: {exc}") from exc

        try:
            content = response.content.decode("gbk").strip()
        except UnicodeDecodeError as exc:
            raise MarketProviderError("Tencent returned invalid GBK quote data") from exc
        fields = self._quote_fields(content, code=code, symbol=symbol)

        return MarketQuote(
            stock_code=code,
            company=fields[1].strip(),
            price=self._optional_number(fields[3]),
            change_percent=self._optional_number(fields[32]),
            volume=self._normalized_volume(fields),
            turnover=self._turnover(fields),
            provider_timestamp=self._provider_timestamp(fields[30]),
        )

    @staticmethod
    def _normalize_code(value: str) -> str:
        code = str(value).strip()
        if re.fullmatch(r"[0-9]{6}", code) is None:
            raise MarketProviderError("stock code must contain exactly 6 ASCII digits")
        return code

    @staticmethod
    def _symbol(code: str) -> str:
        if code.startswith(("4", "8", "92")):
            return f"bj{code}"
        if code.startswith(("5", "6", "9")):
            return f"sh{code}"
        return f"sz{code}"

    @classmethod
    def _quote_fields(cls, content: str, *, code: str, symbol: str) -> list[str]:
        if not content:
            raise MarketProviderError(f"Tencent returned no quote for {code}")
        match = re.fullmatch(r'v_([a-z]{2}[0-9]{6})="(.*)";?', content, re.DOTALL)
        if match is None:
            raise MarketProviderError(f"Tencent returned malformed quote data for {code}")
        if match.group(1) != symbol:
            raise MarketProviderError(
                f"Tencent returned symbol {match.group(1)} for {symbol}"
            )

        payload = match.group(2)
        if not payload:
            raise MarketProviderError(f"Tencent returned no quote for {code}")
        fields = payload.split("~")
        if len(fields) < MIN_TENCENT_FIELDS:
            raise MarketProviderError(
                f"Tencent returned only {len(fields)} quote fields for {code}"
            )
        returned_code = fields[2].strip()
        if returned_code != code:
            raise MarketProviderError(
                f"Tencent returned stock code {returned_code or '(missing)'} for {code}"
            )
        return fields

    @staticmethod
    def _optional_number(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        text = str(value).strip()
        if text in ("", "-"):
            return None
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _normalized_volume(cls, fields: list[str]) -> float | None:
        raw_volume = cls._optional_number(fields[6])
        if raw_volume is None:
            return None

        price = cls._optional_number(fields[3])
        turnover_rate = cls._optional_number(fields[38])
        circulating_market_value_yi = cls._optional_number(fields[44])
        if (
            price is not None
            and price > 0
            and turnover_rate is not None
            and turnover_rate > 0
            and circulating_market_value_yi is not None
            and circulating_market_value_yi > 0
        ):
            expected_volume = (
                circulating_market_value_yi
                * 100_000_000
                / price
                * turnover_rate
                / 100
            )
            volume_as_shares = raw_volume * 100
            if abs(raw_volume - expected_volume) <= abs(
                volume_as_shares - expected_volume
            ):
                return raw_volume
            return volume_as_shares

        return raw_volume * 100

    @classmethod
    def _turnover(cls, fields: list[str]) -> float | None:
        parts = fields[35].split("/")
        if len(parts) >= 3:
            precise_amount = cls._optional_number(parts[2])
            if precise_amount is not None:
                return precise_amount
        amount_wan = cls._optional_number(fields[37])
        return amount_wan * 10_000 if amount_wan is not None else None

    @staticmethod
    def _provider_timestamp(value: Any) -> datetime | None:
        text = str(value).strip() if value is not None else ""
        if not text or text == "-":
            return None
        try:
            local_time = datetime.strptime(text, "%Y%m%d%H%M%S").replace(
                tzinfo=ASIA_SHANGHAI
            )
        except ValueError:
            return None
        return local_time.astimezone(timezone.utc)


def _normalized_market_number(
    value: Any,
    *,
    nonnegative: bool = False,
) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (nonnegative and number < 0):
        return None
    return number


def snapshot_from_quote(
    quote: MarketQuote,
    *,
    fetched_at: datetime | None = None,
    stale_after_seconds: int = STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    fetched = fetched_at or datetime.now(timezone.utc)
    if fetched.tzinfo is None or fetched.utcoffset() is None:
        raise ValueError("fetched_at must include a timezone")
    fetched = fetched.astimezone(timezone.utc)

    provider_timestamp = quote.provider_timestamp
    if provider_timestamp is not None:
        if provider_timestamp.tzinfo is None or provider_timestamp.utcoffset() is None:
            raise ValueError("provider_timestamp must include a timezone")
        provider_timestamp = provider_timestamp.astimezone(timezone.utc)
        age_seconds = (fetched - provider_timestamp).total_seconds()
        stale_seconds: int | None = max(0, math.ceil(age_seconds))
        is_stale: bool | None = age_seconds > stale_after_seconds
    else:
        stale_seconds = None
        is_stale = None

    price = _normalized_market_number(quote.price, nonnegative=True)
    change_percent = _normalized_market_number(quote.change_percent)
    volume = _normalized_market_number(quote.volume, nonnegative=True)
    turnover = _normalized_market_number(quote.turnover, nonnegative=True)
    values = {
        "price": price,
        "change_percent": change_percent,
        "volume": volume,
        "turnover": turnover,
        "provider_timestamp": provider_timestamp,
    }
    missing_fields = [key for key, value in values.items() if value is None]
    market_values = (price, change_percent, volume, turnover)
    if all(value is None for value in market_values):
        data_quality = "unavailable"
    elif missing_fields:
        data_quality = "partial"
    else:
        data_quality = "ok"

    return {
        "stock_code": quote.stock_code,
        "company": quote.company,
        "price": price,
        "change_percent": change_percent,
        "volume": volume,
        "turnover": turnover,
        "fetched_at": fetched.isoformat(),
        "provider_timestamp": (
            provider_timestamp.isoformat() if provider_timestamp is not None else None
        ),
        "is_stale": is_stale,
        "stale_seconds": stale_seconds,
        "fallback_from": quote.fallback_from,
        "data_quality": data_quality,
        "missing_fields": missing_fields,
    }
