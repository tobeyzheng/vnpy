"""Symbol and interval mapping between Futu OpenD and vnpy."""

from __future__ import annotations

from typing import Tuple

from vnpy.trader.constant import Exchange, Interval


# ---------------------------------------------------------------------------
# Exchange / market mapping
# ---------------------------------------------------------------------------
# Futu uses prefixes: HK / US / SH / SZ
# vnpy uses Exchange enum: SEHK / NASDAQ (we collapse all US into SMART
# because backtesting does not differentiate venues), SSE / SZSE.
_FUTU_PREFIX_TO_VN_EXCHANGE = {
    "HK": Exchange.SEHK,
    "US": Exchange.SMART,  # US equities collapsed to SMART for backtest
    "SH": Exchange.SSE,
    "SZ": Exchange.SZSE,
}

_VN_EXCHANGE_TO_FUTU_PREFIX = {
    Exchange.SEHK: "HK",
    Exchange.SMART: "US",
    Exchange.NASDAQ: "US",
    Exchange.NYSE: "US",
    Exchange.AMEX: "US",
    Exchange.ARCA: "US",
    Exchange.BATS: "US",
    Exchange.SSE: "SH",
    Exchange.SZSE: "SZ",
}


def futu_to_vnpy(futu_code: str) -> Tuple[str, Exchange]:
    """Convert a Futu code like ``HK.00700`` to ``("00700", Exchange.SEHK)``.

    Raises ``ValueError`` if the prefix is not supported.
    """
    if "." not in futu_code:
        raise ValueError(f"Invalid Futu code (missing prefix): {futu_code!r}")

    prefix, symbol = futu_code.split(".", 1)
    prefix = prefix.upper()
    if prefix not in _FUTU_PREFIX_TO_VN_EXCHANGE:
        raise ValueError(
            f"Unsupported Futu market prefix {prefix!r} in code {futu_code!r}; "
            f"supported: {sorted(_FUTU_PREFIX_TO_VN_EXCHANGE)}"
        )

    return symbol, _FUTU_PREFIX_TO_VN_EXCHANGE[prefix]


def vnpy_to_futu(symbol: str, exchange: Exchange) -> str:
    """Convert a vnpy ``(symbol, exchange)`` pair back to a Futu code."""
    if exchange not in _VN_EXCHANGE_TO_FUTU_PREFIX:
        raise ValueError(
            f"Unsupported vnpy exchange {exchange!r}; "
            f"supported: {sorted(e.value for e in _VN_EXCHANGE_TO_FUTU_PREFIX)}"
        )
    return f"{_VN_EXCHANGE_TO_FUTU_PREFIX[exchange]}.{symbol}"


# ---------------------------------------------------------------------------
# Interval mapping
# ---------------------------------------------------------------------------
# Lazy import of futu KLType to avoid hard dependency at import time of this
# module (so unit tests / linting can run without futu_api installed).
_VN_INTERVAL_TO_FUTU_NAME = {
    Interval.MINUTE: "K_1M",
    Interval.HOUR: "K_60M",
    Interval.DAILY: "K_DAY",
    Interval.WEEKLY: "K_WEEK",
}

# Plain string aliases for CLI users (e.g. "1m", "5m", "15m", "30m", "60m", "1d")
_ALIAS_TO_VN_INTERVAL = {
    "1m": Interval.MINUTE,
    "1min": Interval.MINUTE,
    "5m": "K_5M",  # not a vnpy native enum; we keep raw mapping below
    "5min": "K_5M",
    "15m": "K_15M",
    "15min": "K_15M",
    "30m": "K_30M",
    "30min": "K_30M",
    "60m": Interval.HOUR,
    "60min": Interval.HOUR,
    "1h": Interval.HOUR,
    "d": Interval.DAILY,
    "1d": Interval.DAILY,
    "day": Interval.DAILY,
    "daily": Interval.DAILY,
    "w": Interval.WEEKLY,
    "1w": Interval.WEEKLY,
    "week": Interval.WEEKLY,
}

# vnpy Interval enum is limited; for sub-hour bars we still want to fetch and
# persist them. We persist to vnpy DB using a synthetic Interval mapping where
# 5m/15m/30m are mapped to MINUTE with a multiplier note in the symbol meta.
# To keep things simple and accurate, we expose a "raw" interval string
# (matching futu KLType names) for cache_index keys, but always pass a proper
# vnpy Interval to BarData (5m/15m/30m collapse to MINUTE; the resampling is
# the user's responsibility for now).
_VN_INTERVAL_FALLBACK = {
    "K_5M": Interval.MINUTE,
    "K_15M": Interval.MINUTE,
    "K_30M": Interval.MINUTE,
    "K_1M": Interval.MINUTE,
    "K_60M": Interval.HOUR,
    "K_DAY": Interval.DAILY,
    "K_WEEK": Interval.WEEKLY,
}


def vnpy_to_futu_interval(interval: Interval) -> str:
    """Return the futu KLType name (e.g. ``"K_DAY"``) for a vnpy Interval."""
    name = _VN_INTERVAL_TO_FUTU_NAME.get(interval)
    if name is None:
        raise ValueError(f"Unsupported vnpy interval {interval!r} for Futu")
    return name


def futu_to_vnpy_interval(futu_name: str) -> Interval:
    """Return the vnpy Interval for a futu KLType name (best-effort fallback)."""
    if futu_name not in _VN_INTERVAL_FALLBACK:
        raise ValueError(f"Unsupported futu KLType name {futu_name!r}")
    return _VN_INTERVAL_FALLBACK[futu_name]


def parse_interval_alias(alias: str) -> str:
    """Translate a user-friendly alias like ``"5m"`` to a futu KLType name.

    Returns the futu KLType name (e.g. ``"K_5M"``). Raises ``ValueError`` if the
    alias is unknown.
    """
    key = alias.strip().lower()
    if key not in _ALIAS_TO_VN_INTERVAL:
        raise ValueError(
            f"Unknown interval alias {alias!r}; "
            f"supported: {sorted(_ALIAS_TO_VN_INTERVAL)}"
        )
    val = _ALIAS_TO_VN_INTERVAL[key]
    if isinstance(val, Interval):
        return _VN_INTERVAL_TO_FUTU_NAME[val]
    return val  # already a "K_xxx" string
