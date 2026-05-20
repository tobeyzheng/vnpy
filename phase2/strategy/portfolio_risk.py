"""US Multi-Symbol Quant Phase 2 — risk rules + portfolio circuit breaker.

This module is the canonical home for **all** phase-② risk logic so the
strategy file can stay focused on signal generation. It exposes:

Single-symbol gates (return ``(triggered: bool, reason: str)``)
    * :func:`single_hard_stop`         — 单标硬止损
    * :func:`single_trailing_take_profit` — 单标移动止盈
    * :func:`single_trend_reverse`     — 单标趋势反转
    * :func:`single_atr_breakout`      — 单标波动率失控

Portfolio gates (operate on a :class:`PortfolioState` snapshot)
    * :func:`portfolio_drawdown`       — 组合最大回撤
    * :func:`portfolio_sector_cap`     — 单行业敞口上限
    * :func:`portfolio_concurrent_holdings` — 持仓只数上限
    * :func:`portfolio_daily_loss`     — 单日组合亏损熔断
    * :func:`portfolio_consecutive_loss` — 连续亏损日熔断

Circuit-breaker state lifecycle
    * :func:`load_portfolio_state`     — read JSON, fall back to defaults
    * :func:`save_portfolio_state`     — atomic-ish write to
      ``state/runs/<plan>/<run_id>/portfolio_state.json``
    * :func:`mark_breaker_triggered`   — flip & timestamp the breaker
    * :func:`should_block_new_orders`  — restart-safe gate readers consult

The breaker file is the **single source of truth** across restarts: when a
fresh process boots and the file already records ``triggered=True``, the
strategy must refuse to issue new orders until an operator clears the file
(see ``05_sim_gate_checklist.md``).

All functions are pure-Python and dependency-free; they're imported by the
strategy and by the unit tests in ``tests/test_risk_rules.py``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Dataclasses — small, JSON-serialisable.
# --------------------------------------------------------------------------- #


@dataclass
class PortfolioState:
    """Persistent state for the portfolio circuit breaker.

    Stored at ``state/runs/<plan>/<run_id>/portfolio_state.json``.
    """

    plan: str = "us_multi_symbol_quant_phase2"
    run_id: str = ""
    nav_peak: float = 0.0
    nav_last: float = 0.0
    consecutive_loss_days: int = 0
    last_pnl_date: str = ""
    breaker_triggered: bool = False
    breaker_reason: str = ""
    breaker_triggered_at: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class HoldingSnapshot:
    """A single-symbol view used by portfolio gates."""

    symbol: str
    sector: str
    market_value: float


@dataclass
class PortfolioSnapshot:
    nav: float
    cash: float
    holdings: List[HoldingSnapshot] = field(default_factory=list)
    today_pnl: float = 0.0  # signed; negative means loss


# --------------------------------------------------------------------------- #
# Single-symbol risk gates.
# --------------------------------------------------------------------------- #


def single_hard_stop(
    entry_price: float, current_price: float, stop_loss_pct: float
) -> Tuple[bool, str]:
    """单标硬止损：跌幅 >= stop_loss_pct 立即出场。"""

    if entry_price <= 0 or current_price <= 0 or stop_loss_pct <= 0:
        return False, ""
    loss_pct = (entry_price - current_price) / entry_price
    if loss_pct >= stop_loss_pct:
        return True, "hard_stop"
    return False, ""


def single_trailing_take_profit(
    entry_price: float,
    highest_price: float,
    current_price: float,
    take_profit_pct: float,
    trailing_drawdown_pct: float,
) -> Tuple[bool, str]:
    """单标移动止盈：盈利 >= take_profit_pct 后回撤 >= trailing_drawdown_pct 出场。"""

    if entry_price <= 0 or highest_price <= 0 or current_price <= 0:
        return False, ""
    if take_profit_pct <= 0 or trailing_drawdown_pct <= 0:
        return False, ""
    if (highest_price / entry_price - 1.0) < take_profit_pct:
        return False, ""
    drawdown = (highest_price - current_price) / highest_price
    if drawdown >= trailing_drawdown_pct:
        return True, "trailing_tp"
    return False, ""


def single_trend_reverse(ema_fast: float, ema_slow: float) -> Tuple[bool, str]:
    """单标趋势反转：EMA12 跌破 EMA26 时出场。"""

    if ema_fast <= 0 or ema_slow <= 0:
        return False, ""
    if ema_fast < ema_slow:
        return True, "trend_reverse"
    return False, ""


def single_atr_breakout(
    atr_pct_now: float, atr_pct_max: float, multiplier: float = 1.5
) -> Tuple[bool, str]:
    """单标波动率失控：当 ATR% 超过门控阈值的 multiplier 倍时出场。"""

    if atr_pct_now < 0 or atr_pct_max <= 0:
        return False, ""
    if atr_pct_now > atr_pct_max * multiplier:
        return True, "vol_breakout"
    return False, ""


# --------------------------------------------------------------------------- #
# Portfolio risk gates.
# --------------------------------------------------------------------------- #


def portfolio_drawdown(
    state: PortfolioState, snapshot: PortfolioSnapshot, dd_limit: float
) -> Tuple[bool, str]:
    """组合最大回撤：(nav_peak - nav) / nav_peak >= dd_limit 触发。"""

    if dd_limit <= 0 or snapshot.nav <= 0:
        return False, ""
    peak = max(state.nav_peak, snapshot.nav)
    if peak <= 0:
        return False, ""
    dd = (peak - snapshot.nav) / peak
    if dd >= dd_limit:
        return True, "portfolio_drawdown"
    return False, ""


def portfolio_sector_cap(
    snapshot: PortfolioSnapshot, sector_cap: float
) -> Tuple[bool, str]:
    """单行业敞口上限：任一行业市值占 NAV 比重 > sector_cap 触发。"""

    if sector_cap <= 0 or snapshot.nav <= 0:
        return False, ""
    by_sector: Dict[str, float] = {}
    for h in snapshot.holdings:
        by_sector[h.sector] = by_sector.get(h.sector, 0.0) + max(0.0, h.market_value)
    for sector, mv in by_sector.items():
        if mv / snapshot.nav > sector_cap:
            return True, f"sector_cap:{sector}"
    return False, ""


def portfolio_concurrent_holdings(
    snapshot: PortfolioSnapshot, max_concurrent_holdings: int
) -> Tuple[bool, str]:
    """持仓只数上限：当前持仓 symbol 数 > max_concurrent_holdings 触发。"""

    if max_concurrent_holdings <= 0:
        return False, ""
    held = [h for h in snapshot.holdings if h.market_value > 0]
    if len(held) > max_concurrent_holdings:
        return True, "too_many_holdings"
    return False, ""


def portfolio_daily_loss(
    snapshot: PortfolioSnapshot, daily_loss_limit_pct: float
) -> Tuple[bool, str]:
    """单日组合亏损熔断：当日 PnL/NAV <= -daily_loss_limit_pct 触发。"""

    if daily_loss_limit_pct <= 0 or snapshot.nav <= 0:
        return False, ""
    pnl_pct = snapshot.today_pnl / snapshot.nav
    if pnl_pct <= -abs(daily_loss_limit_pct):
        return True, "daily_loss_limit"
    return False, ""


def portfolio_consecutive_loss(
    state: PortfolioState, max_consecutive_loss_days: int
) -> Tuple[bool, str]:
    """连续亏损日熔断：state.consecutive_loss_days >= max_consecutive_loss_days 触发。"""

    if max_consecutive_loss_days <= 0:
        return False, ""
    if state.consecutive_loss_days >= max_consecutive_loss_days:
        return True, "consecutive_loss"
    return False, ""


# --------------------------------------------------------------------------- #
# Circuit-breaker state persistence.
# --------------------------------------------------------------------------- #


def state_file_path(
    plan: str, run_id: str, *, base_dir: str | Path = "state/runs"
) -> Path:
    """Return the canonical ``portfolio_state.json`` path for a run."""

    return Path(base_dir) / plan / run_id / "portfolio_state.json"


def load_portfolio_state(
    path: str | Path,
    *,
    plan: str = "us_multi_symbol_quant_phase2",
    run_id: str = "",
) -> PortfolioState:
    """Load state from JSON; if file missing, return a fresh default state."""

    p = Path(path)
    if not p.is_file():
        return PortfolioState(plan=plan, run_id=run_id)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("portfolio_state.json corrupted (%s); using defaults.", exc)
        return PortfolioState(plan=plan, run_id=run_id)

    if not isinstance(raw, dict):
        return PortfolioState(plan=plan, run_id=run_id)

    return PortfolioState(
        plan=str(raw.get("plan", plan)),
        run_id=str(raw.get("run_id", run_id)),
        nav_peak=float(raw.get("nav_peak", 0.0)),
        nav_last=float(raw.get("nav_last", 0.0)),
        consecutive_loss_days=int(raw.get("consecutive_loss_days", 0)),
        last_pnl_date=str(raw.get("last_pnl_date", "")),
        breaker_triggered=bool(raw.get("breaker_triggered", False)),
        breaker_reason=str(raw.get("breaker_reason", "")),
        breaker_triggered_at=str(raw.get("breaker_triggered_at", "")),
        notes=str(raw.get("notes", "")),
    )


def save_portfolio_state(state: PortfolioState, path: str | Path) -> None:
    """Atomic-ish JSON write — temp file + rename."""

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)

    fd, tmp_name = tempfile.mkstemp(prefix=".portfolio_state.", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, p)
    except Exception:
        # Best-effort cleanup of the tmp file when rename fails.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def mark_breaker_triggered(
    state: PortfolioState, *, reason: str, when_iso: Optional[str] = None
) -> PortfolioState:
    """Idempotently flip the breaker flag and stamp the reason."""

    from datetime import datetime, timezone

    if state.breaker_triggered:
        return state  # already tripped — keep first reason for audit trail
    state.breaker_triggered = True
    state.breaker_reason = reason
    state.breaker_triggered_at = (
        when_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    return state


def should_block_new_orders(state: PortfolioState) -> bool:
    """Restart-safe gate read by the strategy & runners before any order."""

    return bool(state.breaker_triggered)


# --------------------------------------------------------------------------- #
# Convenience aggregator — used by the strategy at the start of each bar.
# --------------------------------------------------------------------------- #


def evaluate_all_portfolio_gates(
    state: PortfolioState,
    snapshot: PortfolioSnapshot,
    *,
    dd_limit: float,
    sector_cap: float,
    max_concurrent_holdings: int,
    daily_loss_limit_pct: float,
    max_consecutive_loss_days: int,
) -> List[str]:
    """Run all 5 portfolio gates; return list of triggered reasons (may be empty)."""

    reasons: List[str] = []
    for triggered, reason in (
        portfolio_drawdown(state, snapshot, dd_limit),
        portfolio_sector_cap(snapshot, sector_cap),
        portfolio_concurrent_holdings(snapshot, max_concurrent_holdings),
        portfolio_daily_loss(snapshot, daily_loss_limit_pct),
        portfolio_consecutive_loss(state, max_consecutive_loss_days),
    ):
        if triggered:
            reasons.append(reason)
    return reasons
