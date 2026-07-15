from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from server.services.market import ASIA_SHANGHAI


MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)


@dataclass(frozen=True, slots=True)
class TradingSessionDecision:
    is_open: bool
    code: str
    local_time: datetime
    calendar_year: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "is_open": self.is_open,
            "code": self.code,
            "local_time": self.local_time.isoformat(),
            "calendar_year": self.calendar_year,
        }


class AshareTradingSessionPolicy:
    def __init__(
        self,
        *,
        calendar_year: int | None,
        holidays: frozenset[date] = frozenset(),
        calendar_complete: bool = False,
    ) -> None:
        self.calendar_year = calendar_year
        self.holidays = holidays
        self.calendar_complete = calendar_complete

    @property
    def is_authoritative(self) -> bool:
        return (
            self.calendar_complete
            and self.calendar_year is not None
            and bool(self.holidays)
        )

    def decision(self, value: datetime) -> TradingSessionDecision:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trading session timestamps must include a timezone")
        local_time = value.astimezone(ASIA_SHANGHAI)
        local_date = local_time.date()
        if not self.is_authoritative or local_date.year != self.calendar_year:
            return self._result(False, "calendar_unavailable", local_time)
        if local_date.weekday() >= 5:
            return self._result(False, "weekend", local_time)
        if local_date in self.holidays:
            return self._result(False, "holiday", local_time)

        local_clock = local_time.timetz().replace(tzinfo=None)
        in_morning = MORNING_OPEN <= local_clock <= MORNING_CLOSE
        in_afternoon = AFTERNOON_OPEN <= local_clock <= AFTERNOON_CLOSE
        return self._result(
            in_morning or in_afternoon,
            "open" if in_morning or in_afternoon else "outside_session",
            local_time,
        )

    def _result(
        self,
        is_open: bool,
        code: str,
        local_time: datetime,
    ) -> TradingSessionDecision:
        return TradingSessionDecision(
            is_open=is_open,
            code=code,
            local_time=local_time,
            calendar_year=self.calendar_year,
        )
