from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from services.futu_opend.config import OpenDConfig
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData

REPO_ROOT = Path(__file__).resolve().parents[2]


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
        if bars or not self.fetch_futu_history:
            return vt_symbol, futu_code, bars
        bars = self.fetch_futu_bars(vt_symbol, futu_code, start, end, interval)
        if bars:
            self.db.save_bar_data(bars, stream=False)
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

        try:
            import futu  # type: ignore
        except Exception:
            return []
        code, exchange_name = vt_symbol.split(".", 1)
        config = OpenDConfig()
        ctx = futu.OpenQuoteContext(host=config.host, port=config.port)
        try:
            ret, data, _ = ctx.request_history_kline(
                futu_code,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                ktype=self.to_futu_ktype(futu, interval),
                max_count=1000,

            )
            if ret != futu.RET_OK:
                return []
            rows = data.to_dict("records") if hasattr(data, "to_dict") else []
            bars: list[BarData] = []
            for row in rows:
                bars.append(
                    BarData(
                        symbol=code,
                        exchange=Exchange(exchange_name),
                        datetime=datetime.fromisoformat(str(row["time_key"])),
                        interval=self.to_vnpy_interval(interval),

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
            bars.sort(key=lambda bar: bar.datetime)
            return bars
        finally:
            ctx.close()
