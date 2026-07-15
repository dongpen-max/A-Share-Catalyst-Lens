from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import httpx

from server.services.market import (
    TENCENT_QUOTE_URL,
    MarketProviderError,
    MarketQuote,
    TencentMarketProvider,
    snapshot_from_quote,
)


def tencent_payload(
    *,
    symbol: str = "sh600519",
    code: str = "600519",
    company: str = "贵州茅台",
    price: str = "10.00",
    change_percent: str = "1.25",
    volume: str = "10000",
    provider_timestamp: str = "20260715150000",
    amount_triplet: str = "10.00/10000/1234567.89",
    amount_wan: str = "999.99",
    turnover_rate: str = "1.00",
    circulating_market_value_yi: str = "10.00",
    field_count: int = 50,
) -> bytes:
    fields = [""] * field_count
    values = {
        1: company,
        2: code,
        3: price,
        6: volume,
        30: provider_timestamp,
        32: change_percent,
        35: amount_triplet,
        37: amount_wan,
        38: turnover_rate,
        44: circulating_market_value_yi,
    }
    for index, value in values.items():
        if index < field_count:
            fields[index] = value
    return f'v_{symbol}="{"~".join(fields)}";'.encode("gbk")


class TencentMarketProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_shanghai_shenzhen_and_beijing_to_fixed_https_urls(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            symbol = request.url.path.removeprefix("/q=")
            return httpx.Response(
                200,
                content=tencent_payload(symbol=symbol, code=symbol[2:]),
            )

        provider = TencentMarketProvider(transport=httpx.MockTransport(handler))
        for code, symbol in (
            ("600519", "sh600519"),
            ("000001", "sz000001"),
            ("832982", "bj832982"),
        ):
            with self.subTest(code=code):
                quote = await provider.fetch_quote(code)
                self.assertEqual(quote.stock_code, code)
                self.assertEqual(
                    str(requests[-1].url),
                    TENCENT_QUOTE_URL.format(symbol=symbol),
                )
        self.assertEqual(TencentMarketProvider._symbol("430047"), "bj430047")
        self.assertEqual(TencentMarketProvider._symbol("920002"), "bj920002")

    async def test_decodes_gbk_and_normalizes_volume_amount_and_timestamp(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=tencent_payload())

        provider = TencentMarketProvider(transport=httpx.MockTransport(handler))
        quote = await provider.fetch_quote("600519")

        self.assertEqual(quote.company, "贵州茅台")
        self.assertEqual(quote.price, 10)
        self.assertEqual(quote.change_percent, 1.25)
        self.assertEqual(quote.volume, 1_000_000)
        self.assertEqual(quote.turnover, 1_234_567.89)
        self.assertEqual(
            quote.provider_timestamp,
            datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc),
        )

    async def test_volume_cross_check_can_select_raw_shares_and_amount_fallback(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=tencent_payload(
                    volume="1000000",
                    amount_triplet="10.00/1000000/not-a-number",
                    amount_wan="123.45",
                ),
            )

        quote = await TencentMarketProvider(
            transport=httpx.MockTransport(handler)
        ).fetch_quote("600519")

        self.assertEqual(quote.volume, 1_000_000)
        self.assertEqual(quote.turnover, 1_234_500)

    async def test_volume_uses_lots_fallback_and_zero_values_are_not_missing(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=tencent_payload(
                    price="0",
                    change_percent="0",
                    volume="0",
                    amount_triplet="0/0/0",
                    turnover_rate="",
                    circulating_market_value_yi="",
                ),
            )

        quote = await TencentMarketProvider(
            transport=httpx.MockTransport(handler)
        ).fetch_quote("600519")
        snapshot = snapshot_from_quote(
            quote,
            fetched_at=datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(quote.price, 0)
        self.assertEqual(quote.change_percent, 0)
        self.assertEqual(quote.volume, 0)
        self.assertEqual(quote.turnover, 0)
        self.assertEqual(snapshot["data_quality"], "ok")
        self.assertEqual(snapshot["missing_fields"], [])

    async def test_rejects_empty_short_and_inconsistent_responses(self) -> None:
        responses = (
            b'v_sh600519="";',
            tencent_payload(field_count=10),
            tencent_payload(code="000001"),
        )
        for content in responses:
            with self.subTest(content=content[:30]):
                provider = TencentMarketProvider(
                    transport=httpx.MockTransport(
                        lambda _request: httpx.Response(200, content=content)
                    )
                )
                with self.assertRaises(MarketProviderError):
                    await provider.fetch_quote("600519")

    async def test_http_failure_is_wrapped_without_retry(self) -> None:
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503, text="unavailable")

        provider = TencentMarketProvider(transport=httpx.MockTransport(handler))
        with self.assertRaises(MarketProviderError):
            await provider.fetch_quote("832982")
        self.assertEqual(calls, 1)

    async def test_rejects_non_code_input_before_request(self) -> None:
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=tencent_payload())

        provider = TencentMarketProvider(transport=httpx.MockTransport(handler))
        with self.assertRaises(MarketProviderError):
            await provider.fetch_quote("https://example.com/600519")
        self.assertEqual(calls, 0)


class MarketSnapshotNormalizationTests(unittest.TestCase):
    def test_staleness_uses_strict_threshold_and_clamps_future_age(self) -> None:
        fetched_at = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
        for age, expected_stale, expected_seconds in (
            (900, False, 900),
            (900.5, True, 901),
            (901, True, 901),
            (-60, False, 0),
        ):
            with self.subTest(age=age):
                snapshot = snapshot_from_quote(
                    MarketQuote(
                        stock_code="600519",
                        price=10,
                        change_percent=0,
                        volume=0,
                        turnover=0,
                        provider_timestamp=fetched_at - timedelta(seconds=age),
                    ),
                    fetched_at=fetched_at,
                )
                self.assertIs(snapshot["is_stale"], expected_stale)
                self.assertEqual(snapshot["stale_seconds"], expected_seconds)
                self.assertEqual(snapshot["data_quality"], "ok")

    def test_missing_timestamp_has_unknown_staleness_and_partial_quality(self) -> None:
        snapshot = snapshot_from_quote(
            MarketQuote(
                stock_code="000001",
                price=12.34,
                change_percent=-0.8,
                volume=123_400,
                turnover=2_500_000,
            ),
            fetched_at=datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc),
        )

        self.assertIsNone(snapshot["provider_timestamp"])
        self.assertIsNone(snapshot["is_stale"])
        self.assertIsNone(snapshot["stale_seconds"])
        self.assertEqual(snapshot["data_quality"], "partial")
        self.assertEqual(snapshot["missing_fields"], ["provider_timestamp"])

    def test_no_market_values_are_unavailable(self) -> None:
        snapshot = snapshot_from_quote(
            MarketQuote(stock_code="000001"),
            fetched_at=datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(snapshot["data_quality"], "unavailable")


if __name__ == "__main__":
    unittest.main()
