from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SessionWindow:
    start: time
    end: time


@dataclass(frozen=True)
class MarketRules:
    market: str
    currency: str
    lot_size_mode: str
    default_stop_loss_pct: float
    default_take_profit_pct: float
    slippage_bps: float
    timezone: str
    regular_sessions: tuple[SessionWindow, ...]
    default_session_start: str
    default_session_end: str
    default_no_new_entry_after: str
    default_daily_rebalance_time: str


_MARKET_ALIASES: dict[str, str] = {
    "hk": "hong_kong",
    "hong_kong": "hong_kong",
    "hongkong": "hong_kong",
    "sehk": "hong_kong",
    "hkfe": "hong_kong",
    "us": "us",
    "smart": "us",
    "nyse": "us",
    "nasdaq": "us",
    "amex": "us",
    "cn": "china",
    "china": "china",
    "sh": "china",
    "sz": "china",
    "sse": "china",
    "szse": "china",
}


def normalize_market_name(market: str) -> str:
    text = str(market or "").strip().lower()
    return _MARKET_ALIASES.get(text, text or "us")


def resolve_market_from_vt_symbol(vt_symbol: str) -> str:
    text = str(vt_symbol or "").strip()
    if "." not in text:
        return normalize_market_name(text)
    _symbol, exchange = text.rsplit(".", 1)
    return normalize_market_name(exchange)


def _hhmm(value: str) -> time:
    hour, minute = str(value).split(":", 1)
    return time(int(hour), int(minute))


def _window(start: str, end: str) -> SessionWindow:
    return SessionWindow(start=_hhmm(start), end=_hhmm(end))


def get_market_rules(market: str) -> MarketRules:
    normalized = normalize_market_name(market)
    if normalized == "hong_kong":
        return MarketRules(
            market="hong_kong",
            currency="HKD",
            lot_size_mode="dynamic",
            default_stop_loss_pct=0.08,
            default_take_profit_pct=0.15,
            slippage_bps=8,
            timezone="Asia/Hong_Kong",
            regular_sessions=(
                _window("09:30", "12:00"),
                _window("13:00", "16:00"),
            ),
            default_session_start="09:30",
            default_session_end="16:00",
            default_no_new_entry_after="15:30",
            default_daily_rebalance_time="15:55",
        )
    if normalized == "china":
        return MarketRules(
            market="china",
            currency="CNY",
            lot_size_mode="board_lot",
            default_stop_loss_pct=0.08,
            default_take_profit_pct=0.15,
            slippage_bps=8,
            timezone="Asia/Shanghai",
            regular_sessions=(
                _window("09:30", "11:30"),
                _window("13:00", "15:00"),
            ),
            default_session_start="09:30",
            default_session_end="15:00",
            default_no_new_entry_after="14:30",
            default_daily_rebalance_time="14:55",
        )
    return MarketRules(
        market="us",
        currency="USD",
        lot_size_mode="unitary",
        default_stop_loss_pct=0.07,
        default_take_profit_pct=0.18,
        slippage_bps=5,
        timezone="America/New_York",
        regular_sessions=(
            _window("09:30", "16:00"),
        ),
        default_session_start="09:30",
        default_session_end="16:00",
        default_no_new_entry_after="15:30",
        default_daily_rebalance_time="15:55",
    )


def market_timezone(market: str) -> str:
    return get_market_rules(market).timezone


def market_session_end(market: str) -> str:
    return get_market_rules(market).default_session_end


def market_no_new_entry_after(market: str) -> str:
    return get_market_rules(market).default_no_new_entry_after


def market_daily_rebalance_time(market: str) -> str:
    return get_market_rules(market).default_daily_rebalance_time


def _to_market_datetime(now: datetime | None, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def _shift_clock(value: time, minutes: int) -> time:
    origin = datetime(2000, 1, 1, value.hour, value.minute, value.second)
    shifted = origin + timedelta(minutes=minutes)
    if shifted.day < origin.day:
        shifted = origin.replace(hour=0, minute=0, second=0)
    elif shifted.day > origin.day:
        shifted = origin.replace(hour=23, minute=59, second=59)
    return shifted.time()


def is_market_open(
    market: str,
    now: datetime | None = None,
    *,
    delay_open_minutes: int = 0,
    early_close_minutes: int = 0,
) -> bool:
    rules = get_market_rules(market)
    current = _to_market_datetime(now, rules.timezone)
    if current.weekday() >= 5:
        return False
    current_time = current.time()
    for session in rules.regular_sessions:
        start = _shift_clock(session.start, delay_open_minutes)
        end = _shift_clock(session.end, -early_close_minutes)
        if start >= end:
            continue
        if start <= current_time < end:
            return True
    return False
