from __future__ import annotations

import asyncio
import hashlib
import html
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx


TOP_SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
ANNOUNCEMENT_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_ROOT = "https://static.cninfo.com.cn/"

CATALYST_KEYWORDS = {
    "中标",
    "合同",
    "订单",
    "回购",
    "增持",
    "减持",
    "合同解除",
    "解除合同",
    "违约",
    "诉讼",
    "冻结",
    "逾期",
    "分红",
    "权益分派",
    "预增",
    "预减",
    "扭亏",
    "亏损",
    "并购",
    "重组",
    "获批",
    "立案",
    "处罚",
    "终止",
    "退市",
    "风险警示",
}
POSITIVE_KEYWORDS = {
    "中标",
    "重大合同",
    "订单",
    "签订合同",
    "回购",
    "增持",
    "分红",
    "权益分派",
    "预增",
    "扭亏",
    "获批",
}
NEGATIVE_KEYWORDS = {
    "减持",
    "预减",
    "亏损",
    "立案",
    "处罚",
    "终止",
    "合同解除",
    "解除合同",
    "违约",
    "诉讼",
    "冻结",
    "逾期",
    "退市",
    "风险警示",
}
HIGH_MATERIALITY = {
    "重大合同",
    "中标",
    "业绩预告",
    "回购",
    "增持",
    "减持",
    "并购",
    "重组",
    "立案",
    "处罚",
    "诉讼",
    "冻结",
    "退市",
}


class CninfoError(RuntimeError):
    pass


class CninfoConnector:
    name = "cninfo"

    def __init__(self, *, timeout: float = 15, minimum_interval: float = 0.8) -> None:
        self.timeout = timeout
        self.minimum_interval = minimum_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def discover(
        self,
        *,
        stock_code: str,
        start_date: date | None = None,
        end_date: date | None = None,
        query: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        code = self.normalize_code(stock_code)
        column, plate = self.market_parameters(code)
        end = end_date or date.today()
        start = start_date or end - timedelta(days=90)
        if start > end:
            raise CninfoError("start_date must not be after end_date")
        if (end - start).days > 365:
            start = end - timedelta(days=365)

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; A-Share-Catalyst-Lens/0.3)",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://www.cninfo.com.cn",
            "Referer": (
                "https://www.cninfo.com.cn/new/commonUrl/"
                "pageOfSearch?url=disclosure/list/search&lastPage=index"
            ),
            "X-Requested-With": "XMLHttpRequest",
        }
        async with httpx.AsyncClient(headers=headers, timeout=self.timeout, follow_redirects=True) as client:
            companies = await self._post_json(client, TOP_SEARCH_URL, {"keyWord": code})
            company = next((item for item in companies if str(item.get("code")) == code), None)
            if not company:
                raise CninfoError(f"CNINFO did not resolve stock code {code}")

            form = {
                "pageNum": "1",
                "pageSize": str(min(max(limit, 1), 30)),
                "column": column,
                "tabName": "fulltext",
                "plate": plate,
                "stock": f"{code},{company.get('orgId', '')}",
                "searchkey": query.strip(),
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": f"{start.isoformat()}~{end.isoformat()}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            result = await self._post_json(client, ANNOUNCEMENT_URL, form)

        announcements = result.get("announcements") or []
        return [
            self.normalize_announcement(item, query=query, company=company)
            for item in announcements[:limit]
        ]

    async def _post_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        data: dict[str, str],
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                await self._respect_rate_limit()
                response = await client.post(url, data=data)
                if response.status_code >= 500:
                    raise CninfoError(f"CNINFO returned HTTP {response.status_code}")
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError, CninfoError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.6 * (2**attempt))
        raise CninfoError(f"CNINFO request failed: {last_error}")

    async def _respect_rate_limit(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self.minimum_interval:
                await asyncio.sleep(self.minimum_interval - elapsed)
            self._last_request = time.monotonic()

    @staticmethod
    def normalize_code(value: str) -> str:
        match = re.search(r"\d{6}", value)
        if not match:
            raise CninfoError("A-share stock code must contain six digits")
        return match.group(0)

    @staticmethod
    def market_parameters(code: str) -> tuple[str, str]:
        if code.startswith(("000", "001", "002", "003", "200", "300", "301")):
            return "szse", "sz"
        if code.startswith(("600", "601", "603", "605", "688", "689", "900")):
            return "sse", "sh"
        raise CninfoError(f"stock code {code} is not currently supported by the CNINFO connector")

    @classmethod
    def normalize_announcement(
        cls,
        item: dict[str, Any],
        *,
        query: str,
        company: dict[str, Any],
    ) -> dict[str, Any]:
        title = cls.clean_title(str(item.get("announcementTitle") or "未命名公告"))
        published_at = cls.timestamp_to_iso(item.get("announcementTime"))
        freshness = cls.freshness_score(published_at)
        direction = cls.direction(title)
        materiality = cls.materiality(title)
        adjunct_url = str(item.get("adjunctUrl") or "").lstrip("/")
        url = f"{PDF_ROOT}{adjunct_url}" if adjunct_url else ""
        content_hash = hashlib.sha256(
            f"cninfo|{item.get('announcementId', '')}|{title}|{url}".encode("utf-8")
        ).hexdigest()
        source_type = "financial_report" if re.search(r"年度报告|季度报告|半年度报告", title) else "exchange_announcement"
        return {
            "source_type": source_type,
            "source_name": "巨潮资讯",
            "title": title,
            "url": url,
            "published_at": published_at,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "quote": title,
            "claim": f"{company.get('zwjc') or item.get('secName') or ''}发布公告：{title}",
            "direction": direction,
            "reliability": 5,
            "relevance": cls.relevance(title, query),
            "freshness": freshness,
            "materiality": materiality,
            "immediacy": freshness,
            "novelty": freshness,
            "market_alignment": 0,
            "priced_in_risk": 0,
            "counterevidence": materiality if direction == "negative" else 0,
            "status": "pending",
            "content_hash": content_hash,
            "metadata": {
                "connector": "cninfo",
                "announcement_id": str(item.get("announcementId") or ""),
                "org_id": str(item.get("orgId") or company.get("orgId") or ""),
                "stock_code": str(item.get("secCode") or company.get("code") or ""),
                "company": str(item.get("secName") or company.get("zwjc") or ""),
            },
        }

    @staticmethod
    def clean_title(value: str) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()

    @staticmethod
    def timestamp_to_iso(value: Any) -> str | None:
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            return None

    @staticmethod
    def freshness_score(published_at: str | None) -> float:
        if not published_at:
            return 1
        published = datetime.fromisoformat(published_at)
        days = max((datetime.now(timezone.utc) - published).days, 0)
        if days <= 7:
            return 5
        if days <= 30:
            return 4
        if days <= 90:
            return 3
        if days <= 180:
            return 2
        return 1

    @staticmethod
    def direction(title: str) -> str:
        positive = any(keyword in title for keyword in POSITIVE_KEYWORDS)
        negative = any(keyword in title for keyword in NEGATIVE_KEYWORDS)
        if positive and negative:
            return "mixed"
        if positive:
            return "positive"
        if negative:
            return "negative"
        return "neutral"

    @staticmethod
    def materiality(title: str) -> float:
        if any(keyword in title for keyword in HIGH_MATERIALITY):
            return 4
        if any(keyword in title for keyword in CATALYST_KEYWORDS):
            return 3
        if re.search(r"董事会|股东大会|任职|制度", title):
            return 2
        return 2.5

    @staticmethod
    def relevance(title: str, query: str) -> float:
        query = query.strip()
        if not query:
            return 3
        if query.lower() in title.lower():
            return 5
        shared = [keyword for keyword in CATALYST_KEYWORDS if keyword in query and keyword in title]
        return 4 if shared else 3
