from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from services.futu_opend.config import OpenDConfig
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData

REPO_ROOT = Path(__file__).resolve().parents[2]

# Futu request_history_kline single-call cap; 1m intervals on long ranges
# easily exceed this and require pagination via ``page_req_key``.
_FUTU_PAGE_MAX = 1000
# Soft sleep between paginated calls to avoid Futu QPS limit (low-cost req).
_FUTU_PAGE_SLEEP = 0.4


def parse_us_symbol(symbol: str) -> tuple[str, str, str]:
    text = symbol.strip().upper()
    if text.startswith("US."):
        code = text.replace("US.", "", 1)
    elif text.endswith(".US"):
        code = text.replace(".US", "")
    elif text.endswith(".SMART"):
        code = text.replace(".SMART", "")
    else:
        code = text
    return f"{code}.US", f"{code}.SMART", f"US.{code}"


def parse_hk_symbol(symbol: str) -> tuple[str, str, str]:
    """Normalise HK ticker into (input_form, vt_symbol, futu_code).

    Accepts forms: ``700``, ``0700``, ``00700``, ``HK.00700``,
    ``0700.HK``, ``00700.HK``, ``00700.SEHK`` (case-insensitive).
    Output is always zero-padded to 5 digits (Futu/SEHK convention),
    e.g. vt_symbol=``00700.SEHK`` and futu_code=``HK.00700``.
    """
    text = symbol.strip().upper()
    if text.startswith("HK."):
        code = text.replace("HK.", "", 1)
    elif text.endswith(".HK"):
        code = text.replace(".HK", "")
    elif text.endswith(".SEHK"):
        code = text.replace(".SEHK", "")
    else:
        code = text
    code = code.lstrip("0") or "0"
    code = code.zfill(5)
    return f"{code}.HK", f"{code}.SEHK", f"HK.{code}"


def parse_symbol(symbol: str) -> tuple[str, str, str, str]:
    """Dispatch by market suffix. Returns (market, input_form, vt_symbol, futu_code).

    market is ``"hk"`` or ``"us"``.
    """
    text = symbol.strip().upper()
    is_hk = (
        text.startswith("HK.")
        or text.endswith(".HK")
        or text.endswith(".SEHK")
    )
    if is_hk:
        input_form, vt_symbol, futu_code = parse_hk_symbol(symbol)
        return "hk", input_form, vt_symbol, futu_code
    input_form, vt_symbol, futu_code = parse_us_symbol(symbol)
    return "us", input_form, vt_symbol, futu_code


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class VnpyBarRepository:
    """Bar repository backed by vn.py database with optional Futu history import."""

    def __init__(self, *, fetch_futu_history: bool = True):
        self.fetch_futu_history = fetch_futu_history
        self.db = get_database()

    def load_us_bars(self, symbol: str, start: datetime, end: datetime, interval: str = "1d") -> tuple[str, str, list[BarData]]:
        _us_symbol, vt_symbol, futu_code = parse_us_symbol(symbol)
        return self._load_bars_impl(vt_symbol, futu_code, start, end, interval)

    def load_us_daily(self, symbol: str, start: datetime, end: datetime) -> tuple[str, str, list[BarData]]:
        return self.load_us_bars(symbol, start, end, "1d")

    def load_hk_bars(self, symbol: str, start: datetime, end: datetime, interval: str = "1d") -> tuple[str, str, list[BarData]]:
        _hk_symbol, vt_symbol, futu_code = parse_hk_symbol(symbol)
        return self._load_bars_impl(vt_symbol, futu_code, start, end, interval)

    def load_bars(self, symbol: str, start: datetime, end: datetime, interval: str = "1d") -> tuple[str, str, list[BarData]]:
        """Market-aware loader. Dispatches by suffix to US/HK pipelines."""
        market, _input_form, vt_symbol, futu_code = parse_symbol(symbol)
        if market == "hk":
            return self.load_hk_bars(symbol, start, end, interval)
        return self.load_us_bars(symbol, start, end, interval)

    def _load_bars_impl(self, vt_symbol: str, futu_code: str, start: datetime, end: datetime, interval: str) -> tuple[str, str, list[BarData]]:
        code, exchange_name = vt_symbol.split(".", 1)
        exchange = Exchange(exchange_name)
        vnpy_interval = self.to_vnpy_interval(interval)
        bars = self.db.load_bar_data(code, exchange, vnpy_interval, start, end)
        bars.sort(key=lambda bar: bar.datetime)

        # No need / not allowed to fetch from Futu: return whatever DB has.
        if not self.fetch_futu_history:
            return vt_symbol, futu_code, bars

        # Compute coverage gaps so partial DB hits still trigger Futu fetch.
        # We only refill the [start, db_min) and (db_max, end] segments.
        gaps: list[tuple[datetime, datetime]] = []
        if not bars:
            gaps.append((start, end))
        else:
            # vnpy DB returns tz-aware datetimes; CLI dates are tz-naive.
            # Compare on date-only to avoid tz arithmetic mismatch.
            db_min_d = bars[0].datetime.date()
            db_max_d = bars[-1].datetime.date()
            start_d = start.date()
            end_d = end.date()
            if (db_min_d - start_d).days >= 1:
                gaps.append((start, datetime.combine(db_min_d - timedelta(days=1), datetime.min.time())))
            if (end_d - db_max_d).days >= 1:
                gaps.append((datetime.combine(db_max_d + timedelta(days=1), datetime.min.time()), end))

        fetched: list[BarData] = []
        for g_start, g_end in gaps:
            chunk = self.fetch_futu_bars(vt_symbol, futu_code, g_start, g_end, interval)
            if chunk:
                fetched.extend(chunk)

        if fetched:
            self.db.save_bar_data(fetched, stream=False)
            # Reload from DB after persisting to get a clean, deduped, sorted view.
            bars = self.db.load_bar_data(code, exchange, vnpy_interval, start, end)
            bars.sort(key=lambda bar: bar.datetime)

        return vt_symbol, futu_code, bars

    def to_vnpy_interval(self, interval: str) -> Interval:
        text = str(interval).lower()
        if text in {"1m", "minute", "min"}:
            return Interval.MINUTE
        if text in {"1d", "d", "daily", "day"}:
            return Interval.DAILY
        raise ValueError(f"unsupported interval: {interval}; supported: 1d, 1m")

    def to_futu_ktype(self, futu: Any, interval: str) -> Any:
        text = str(interval).lower()
        if text in {"1m", "minute", "min"}:
            return futu.KLType.K_1M
        if text in {"1d", "d", "daily", "day"}:
            return futu.KLType.K_DAY
        raise ValueError(f"unsupported futu interval: {interval}; supported: 1d, 1m")

    def fetch_futu_bars(self, vt_symbol: str, futu_code: str, start: datetime, end: datetime, interval: str = "1d") -> list[BarData]:
        """Fetch bars from Futu OpenD with pagination.

        Futu's ``request_history_kline`` caps each call at ~1000 rows (see
        ``_FUTU_PAGE_MAX``). For 1m intervals over a multi-day range the
        result is truncated unless we follow ``page_req_key`` until it
        becomes ``None``. Returns the merged, time-sorted list of bars.
        """
        try:
            import futu  # type: ignore
        except Exception:
            return []
        code, exchange_name = vt_symbol.split(".", 1)
        config = OpenDConfig()
        ctx = futu.OpenQuoteContext(host=config.host, port=config.port)
        ktype = self.to_futu_ktype(futu, interval)
        vnpy_interval = self.to_vnpy_interval(interval)
        bars: list[BarData] = []
        try:
            page_req_key: Any = None
            while True:
                ret, data, page_req_key = ctx.request_history_kline(
                    futu_code,
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    ktype=ktype,
                    max_count=_FUTU_PAGE_MAX,
                    page_req_key=page_req_key,
                )
                if ret != futu.RET_OK:
                    break
                rows = data.to_dict("records") if hasattr(data, "to_dict") else []
                for row in rows:
                    bars.append(
                        BarData(
                            symbol=code,
                            exchange=Exchange(exchange_name),
                            datetime=datetime.fromisoformat(str(row["time_key"])),
                            interval=vnpy_interval,
                            volume=safe_float(row.get("volume")),
                            turnover=safe_float(row.get("turnover")),
                            open_interest=0,
                            open_price=safe_float(row.get("open")),
                            high_price=safe_float(row.get("high")),
                            low_price=safe_float(row.get("low")),
                            close_price=safe_float(row.get("close")),
                            gateway_name="FUTU",
                        )
                    )
                if not page_req_key:
                    break
                time.sleep(_FUTU_PAGE_SLEEP)
            bars.sort(key=lambda bar: bar.datetime)
            return bars
        finally:
            ctx.close()
