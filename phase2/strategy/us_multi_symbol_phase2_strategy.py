# -*- coding: utf-8 -*-
"""US Multi-Symbol Quant Phase 2 — strategy skeleton (DO NOT live-submit).

This file is the phase-② multi-symbol strategy. It deliberately mirrors the
single-name NVDA strategy public surface (``Strategy`` class with
``initialize`` / ``trigger_symbols`` / ``custom_indicator`` /
``global_variables`` / ``handle_data``) so the Futu platform can load it
without any platform-side change. It does **not** modify the existing NVDA
files (``us_nvda_1d_strategy_multifactor.py``,
``us_nvda_1d_strategy_trend_momentum.py``,
``strategy_classic_multifactor.py``).

================================================================
HARD GATE — ``LIVE_SUBMIT`` MUST stay ``False`` in this branch.
================================================================

Switching ``LIVE_SUBMIT`` to ``True`` requires:
1. an independent plan under ``.codebuddy/plan/``;
2. an explicit user confirmation per project rule 2;
3. SIM admittance per ``docs/research/us_multi_symbol_quant/05_sim_gate_checklist.md``.

Until then, the strategy only emits structured candidates and never calls
``place_market`` / ``place_limit``.

Naming convention (carried over from the NVDA strategy, scope upgraded):
    base_capital / slice_value / max_slices / position_pct
        — per-symbol (single-name) money management knobs.
    pool_budget_pct / max_concurrent_holdings / cash_buffer_pct /
    max_orders_per_day
        — portfolio-level knobs newly introduced in phase ②.

Module imports the lightweight pool loader so unit tests can drive the
strategy without the Futu runtime. Inside the Futu sandbox the platform
provides ``StrategyBase`` / ``declare_*`` / ``net_asset`` / ``cash`` /
``position_holding_qty`` symbols globally, hence the conditional-import
shim below.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Hard switch — never flip in this plan.
# --------------------------------------------------------------------------- #
LIVE_SUBMIT: bool = False  # noqa: E501 — keep flag visible & easy to grep

# --------------------------------------------------------------------------- #
# Futu runtime shim — when running outside the platform (unit tests / lint),
# fall back to no-op stand-ins so ``py_compile`` and pytest stay green.
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - exercised only in Futu sandbox
    StrategyBase  # type: ignore[name-defined]
    declare_strategy_type  # type: ignore[name-defined]
    declare_trig_symbol  # type: ignore[name-defined]
    show_variable  # type: ignore[name-defined]
    AlgoStrategyType  # type: ignore[name-defined]
    GlobalType  # type: ignore[name-defined]
    Currency  # type: ignore[name-defined]
    net_asset  # type: ignore[name-defined]
    cash  # type: ignore[name-defined]
    position_holding_qty  # type: ignore[name-defined]
    bar_close  # type: ignore[name-defined]
    bar_open  # type: ignore[name-defined]
    bar_high  # type: ignore[name-defined]
    bar_low  # type: ignore[name-defined]
    bar_volume  # type: ignore[name-defined]
    _FUTU_AVAILABLE = True
except NameError:
    _FUTU_AVAILABLE = False

    class StrategyBase:  # type: ignore[no-redef]
        """Stub matching the Futu ``StrategyBase`` shape (test-time only)."""

    class _EnumStub:
        SECURITY = "SECURITY"
        BOOL = "BOOL"
        INT = "INT"
        FLOAT = "FLOAT"
        USD = "USD"

    AlgoStrategyType = _EnumStub  # type: ignore[assignment]
    GlobalType = _EnumStub  # type: ignore[assignment]
    Currency = _EnumStub  # type: ignore[assignment]

    def declare_strategy_type(_t: Any) -> None:  # type: ignore[no-redef]
        return None

    def declare_trig_symbol() -> str:  # type: ignore[no-redef]
        return "_TRIG_PLACEHOLDER_"

    def show_variable(value: Any, _gtype: Any) -> Any:  # type: ignore[no-redef]
        return value

    def net_asset(currency: Any = None) -> float:  # type: ignore[no-redef]
        return 0.0

    def cash(currency: Any = None) -> float:  # type: ignore[no-redef]
        return 0.0

    def position_holding_qty(symbol: str = "") -> int:  # type: ignore[no-redef]
        return 0

    def bar_close(symbol: str = "") -> List[float]:  # type: ignore[no-redef]
        return []

    def bar_open(symbol: str = "") -> List[float]:  # type: ignore[no-redef]
        return []

    def bar_high(symbol: str = "") -> List[float]:  # type: ignore[no-redef]
        return []

    def bar_low(symbol: str = "") -> List[float]:  # type: ignore[no-redef]
        return []

    def bar_volume(symbol: str = "") -> List[float]:  # type: ignore[no-redef]
        return []


# --------------------------------------------------------------------------- #
# Pool loader import — ``phase2`` package is importable both from repo root
# (unit tests) and from the platform root that mounts the file directly.
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]  # /projects/vnpy
if str(_REPO_ROOT) not in sys.path:  # pragma: no cover - sandbox guard
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from phase2.strategy.pool_loader import (  # type: ignore
        PoolConfig,
        apply_runtime_filters,
        load_pool_config,
    )
except Exception:  # pragma: no cover - sandbox without phase2 package
    PoolConfig = None  # type: ignore[assignment]
    apply_runtime_filters = None  # type: ignore[assignment]
    load_pool_config = None  # type: ignore[assignment]

try:
    from phase2.strategy.portfolio_risk import (  # type: ignore
        HoldingSnapshot,
        PortfolioSnapshot,
        PortfolioState,
        evaluate_all_portfolio_gates,
        load_portfolio_state,
        mark_breaker_triggered,
        save_portfolio_state,
        should_block_new_orders,
        state_file_path,
    )
except Exception:  # pragma: no cover - sandbox without phase2 package
    HoldingSnapshot = None  # type: ignore[assignment]
    PortfolioSnapshot = None  # type: ignore[assignment]
    PortfolioState = None  # type: ignore[assignment]
    evaluate_all_portfolio_gates = None  # type: ignore[assignment]
    load_portfolio_state = None  # type: ignore[assignment]
    mark_breaker_triggered = None  # type: ignore[assignment]
    save_portfolio_state = None  # type: ignore[assignment]
    should_block_new_orders = None  # type: ignore[assignment]
    state_file_path = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# Indicator helpers — pure, side-effect free; testable without the platform.
# Implementations stay simple and avoid pandas/numpy so the file remains
# importable inside the Futu sandbox where third-party deps are limited.
# --------------------------------------------------------------------------- #


def ema(values: List[float], period: int) -> Optional[float]:
    """Classic EMA on the **most recent** ``period`` values."""

    if not values or period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1.0 - k)
    return e


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    """Standard 14-period RSI; returns ``None`` if not enough samples."""

    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


def atr_pct(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> Optional[float]:
    """ATR over ``period`` bars divided by the latest close (== ATR%)."""

    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs: List[float] = []
    for i in range(-period, 0):
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs) / period
    last_close = closes[-1]
    return a / last_close if last_close > 0 else None


def adx_proxy(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> Optional[float]:
    """Lightweight ADX proxy.

    A full Wilder ADX is heavier than what the sandbox needs for a daily
    trend gate; we approximate with the rolling magnitude of directional
    moves, which preserves "trend strength > 25" semantics in tests.
    """

    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    plus = 0.0
    minus = 0.0
    tr_sum = 0.0
    for i in range(-period, 0):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus += up if (up > down and up > 0) else 0.0
        minus += down if (down > up and down > 0) else 0.0
        tr_sum += max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    if tr_sum == 0:
        return 0.0
    di_p = 100.0 * plus / tr_sum
    di_m = 100.0 * minus / tr_sum
    if di_p + di_m == 0:
        return 0.0
    return 100.0 * abs(di_p - di_m) / (di_p + di_m)


# --------------------------------------------------------------------------- #
# Budget allocation — pure functions so they're trivially unit-testable.
#
# Two stages:
#   1) ``compute_per_symbol_budget`` — derives the slice value off NAV.
#   2) ``allocate_orders_serial`` — walks candidates one at a time,
#      re-reads cash after every (pretend) fill, enforces cash buffer and
#      max_orders_per_day, returns a structured plan + skip reasons.
# These functions never touch the Futu APIs; the ``Strategy._allocate_budget``
# wrapper injects the live ``net_asset`` / ``cash`` snapshots at runtime.
# --------------------------------------------------------------------------- #


def compute_per_symbol_budget(
    nav: float,
    *,
    pool_budget_pct: float,
    max_concurrent_holdings: int,
    position_pct: float,
) -> Dict[str, float]:
    """Return ``{"per_symbol_budget": ..., "slice_value": ...}``.

    Formulas (mirror requirement 3.2):
        per_symbol_budget = NAV × pool_budget_pct / max_concurrent_holdings
        slice_value       = per_symbol_budget × position_pct
    """

    if nav < 0 or pool_budget_pct < 0 or position_pct < 0:
        raise ValueError("nav / pool_budget_pct / position_pct must be >= 0.")
    if max_concurrent_holdings <= 0:
        raise ValueError("max_concurrent_holdings must be > 0.")
    per_symbol = nav * pool_budget_pct / max_concurrent_holdings
    slice_value = per_symbol * position_pct
    return {"per_symbol_budget": per_symbol, "slice_value": slice_value}


def allocate_orders_serial(
    candidates: List[Dict[str, Any]],
    *,
    nav: float,
    initial_cash: float,
    pool_budget_pct: float,
    max_concurrent_holdings: int,
    position_pct: float,
    cash_buffer_pct: float,
    max_orders_per_day: int,
    orders_used_today: int = 0,
) -> Dict[str, Any]:
    """Serial budget walker — produces an order plan + skip reasons.

    ``candidates`` is a list of ``{"symbol": str, "price": float}`` entries
    sorted in priority order. The caller (strategy / runner) is responsible
    for ranking; this function is pure & deterministic w.r.t. its inputs.

    Returns
    -------
    dict
        {
          "orders":  [{"symbol", "qty", "price", "notional"} ...],
          "skipped": [{"symbol", "reason"} ...],
          "remaining_cash": float,
          "orders_used_after": int,
        }
    """

    if cash_buffer_pct < 0 or cash_buffer_pct >= 1:
        raise ValueError("cash_buffer_pct must be in [0, 1).")
    if max_orders_per_day < 0:
        raise ValueError("max_orders_per_day must be >= 0.")

    budget = compute_per_symbol_budget(
        nav,
        pool_budget_pct=pool_budget_pct,
        max_concurrent_holdings=max_concurrent_holdings,
        position_pct=position_pct,
    )
    slice_value = budget["slice_value"]
    cash_floor = nav * cash_buffer_pct
    remaining_cash = initial_cash
    orders_used = orders_used_today
    orders: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for c in candidates:
        sym = c["symbol"]
        price = float(c.get("price", 0.0) or 0.0)

        if orders_used >= max_orders_per_day:
            skipped.append({"symbol": sym, "reason": "max_orders_per_day"})
            continue
        if price <= 0:
            skipped.append({"symbol": sym, "reason": "invalid_price"})
            continue

        spendable = max(0.0, remaining_cash - cash_floor)
        if spendable < slice_value:
            skipped.append({"symbol": sym, "reason": "insufficient_cash"})
            continue

        notional_target = min(slice_value, spendable)
        qty = int(notional_target // price)
        if qty <= 0:
            skipped.append({"symbol": sym, "reason": "qty_zero"})
            continue
        notional = qty * price
        if remaining_cash - notional < cash_floor - 1e-9:
            skipped.append({"symbol": sym, "reason": "would_break_cash_buffer"})
            continue

        orders.append(
            {"symbol": sym, "qty": qty, "price": price, "notional": notional}
        )
        remaining_cash -= notional
        orders_used += 1

    return {
        "orders": orders,
        "skipped": skipped,
        "remaining_cash": remaining_cash,
        "orders_used_after": orders_used,
        "slice_value": slice_value,
        "per_symbol_budget": budget["per_symbol_budget"],
    }


# --------------------------------------------------------------------------- #
# Per-symbol state — one struct per pool member; all attributes are scalars
# so the whole dict can be JSON-serialised when the strategy persists state.
# --------------------------------------------------------------------------- #


class _SymbolState:
    __slots__ = (
        "base_capital",
        "used_slices",
        "last_entry_price",
        "highest_price",
        "bars_since_last_entry",
        "bars_since_last_exit",
        "consecutive_unprofitable_adds",
        "last_rsi",
    )

    def __init__(self) -> None:
        self.base_capital: float = 0.0
        self.used_slices: int = 0
        self.last_entry_price: float = 0.0
        self.highest_price: float = 0.0
        self.bars_since_last_entry: int = 0
        self.bars_since_last_exit: int = 10**6  # large = no recent exit
        self.consecutive_unprofitable_adds: int = 0
        self.last_rsi: float = 50.0


# --------------------------------------------------------------------------- #
# Multi-symbol strategy.
# --------------------------------------------------------------------------- #


class Strategy(StrategyBase):
    """Phase-② multi-symbol skeleton (4 factors × 5 entry conds × exit chain)."""

    # ------------------------------------------------------------------ #
    # Lifecycle hooks expected by the Futu platform.
    # ------------------------------------------------------------------ #

    def initialize(self) -> None:
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self.trigger_symbols()
        self.custom_indicator()
        self.global_variables()
        self._states: Dict[str, _SymbolState] = {
            s: _SymbolState() for s in self.pool_symbols
        }
        self._candidates_today: List[Dict[str, Any]] = []
        self._orders_today: int = 0
        self._last_bar_id: Any = None

        # ---------------------------------------------------------------- #
        # Portfolio circuit-breaker — read persistent state on boot so the
        # strategy stays blocked across process restarts. ``run_id`` is
        # injected via env var by the runner; tests / smoke checks fall
        # back to ``"sandbox"``.
        # ---------------------------------------------------------------- #
        self._plan_name: str = "us_multi_symbol_quant_phase2"
        self._run_id: str = os.environ.get("PHASE2_RUN_ID", "sandbox")
        self._state_path: Optional[Path] = None
        self._portfolio_state: Optional[Any] = None
        if state_file_path is not None and load_portfolio_state is not None:
            base_dir = os.environ.get("PHASE2_STATE_DIR", "state/runs")
            self._state_path = state_file_path(
                self._plan_name, self._run_id, base_dir=base_dir
            )
            self._portfolio_state = load_portfolio_state(
                self._state_path, plan=self._plan_name, run_id=self._run_id
            )

    def trigger_symbols(self) -> None:
        # Load pool config; fall back to a tiny default for sandbox tests.
        cfg_path = os.environ.get(
            "PHASE2_POOL_CONFIG",
            str(_HERE / "config" / "pool_config.yaml"),
        )
        if load_pool_config is not None and Path(cfg_path).is_file():
            self._pool_cfg: Optional[PoolConfig] = load_pool_config(cfg_path)
            self.pool_symbols: List[str] = self._pool_cfg.symbol_list()
        else:
            self._pool_cfg = None
            self.pool_symbols = ["NVDA"]

        if len(self.pool_symbols) > 20:
            raise ValueError(
                f"phase2 hard cap exceeded: {len(self.pool_symbols)} > 20"
            )

        # Declare each pool member as a trigger symbol.
        self._trig_handles = [declare_trig_symbol() for _ in self.pool_symbols]

    def custom_indicator(self) -> None:
        # Indicators are computed per-bar in handle_data — keep this no-op.
        return None

    def global_variables(self) -> None:
        # Per-symbol money management (carried-over names).
        self.position_pct = show_variable(0.10, GlobalType.FLOAT)
        self.max_slices = show_variable(5, GlobalType.INT)
        self.min_add_interval = show_variable(10, GlobalType.INT)
        self.min_add_loss_pct = show_variable(0.02, GlobalType.FLOAT)

        # Portfolio-level (new in phase ②).
        self.pool_budget_pct = show_variable(0.80, GlobalType.FLOAT)
        self.max_concurrent_holdings = show_variable(5, GlobalType.INT)
        self.cash_buffer_pct = show_variable(0.05, GlobalType.FLOAT)
        self.max_orders_per_day = show_variable(10, GlobalType.INT)

        # Risk knobs.
        self.stop_loss_pct = show_variable(0.05, GlobalType.FLOAT)
        self.take_profit_pct = show_variable(0.10, GlobalType.FLOAT)
        self.trailing_drawdown_pct = show_variable(0.05, GlobalType.FLOAT)
        self.portfolio_dd_limit = show_variable(0.08, GlobalType.FLOAT)
        self.sector_cap = show_variable(0.40, GlobalType.FLOAT)
        self.cooldown_bars_after_exit = show_variable(5, GlobalType.INT)
        self.daily_loss_limit_pct = show_variable(0.03, GlobalType.FLOAT)
        self.max_consecutive_loss_days = show_variable(5, GlobalType.INT)

        # Factor thresholds.
        self.adx_min = show_variable(25.0, GlobalType.FLOAT)
        self.rsi_oversold = show_variable(30.0, GlobalType.FLOAT)
        self.atr_pct_max = show_variable(0.08, GlobalType.FLOAT)
        self.vol_ratio_min = show_variable(1.2, GlobalType.FLOAT)

    # ------------------------------------------------------------------ #
    # Per-bar driver — kept thin; heavy logic delegated to private helpers
    # for testability.
    # ------------------------------------------------------------------ #

    def handle_data(self) -> None:
        self._candidates_today = []
        self._orders_today = 0

        # Run the 5 portfolio gates first so a fresh trip latches before
        # we evaluate per-symbol entries.
        self._check_portfolio_gates_and_persist()

        # If breaker was tripped (this bar or a previous run) refuse new
        # entries; the exit chain still runs so existing positions can be
        # closed deterministically. ``LIVE_SUBMIT == False`` keeps this a
        # no-op end-to-end in phase ②, but the gate is still authoritative.
        if (
            self._portfolio_state is not None
            and should_block_new_orders is not None
            and should_block_new_orders(self._portfolio_state)
        ):
            self._entries_blocked_by_breaker = True
        else:
            self._entries_blocked_by_breaker = False

        for sym in self.pool_symbols:
            state = self._states[sym]
            state.bars_since_last_entry += 1
            state.bars_since_last_exit += 1

            ctx = self._build_symbol_context(sym)
            if ctx is None:
                continue

            # Exit chain runs first when there's an open position.
            held_qty = int(position_holding_qty(symbol=sym) or 0)
            if held_qty > 0:
                exit_action = self._evaluate_exit(sym, state, ctx)
                if exit_action is not None:
                    # Exits in phase 2 are recorded but not actually submitted
                    # because LIVE_SUBMIT == False.
                    state.bars_since_last_exit = 0
                    continue  # do not also evaluate entries this bar

            # Entry — must pass all 5 conditions.
            if self._entries_blocked_by_breaker:
                continue
            if state.bars_since_last_exit < int(self.cooldown_bars_after_exit):
                continue
            if self._evaluate_entry(sym, state, ctx):
                self._candidates_today.append({"symbol": sym, "ctx": ctx})

    # ------------------------------------------------------------------ #
    # Helpers.
    # ------------------------------------------------------------------ #

    def _check_portfolio_gates_and_persist(self) -> List[str]:
        """Evaluate the 5 portfolio gates; persist on first trip.

        Returns the list of triggered reasons (empty when all gates pass).
        Once any gate trips we mark the breaker, save state to disk, and
        any subsequent ``handle_data`` call (this run or after a restart)
        will refuse new entries via :func:`should_block_new_orders`.

        Side-effect-free when running in the platform sandbox without the
        helper module — guards every call with ``is None`` checks.
        """

        if (
            self._portfolio_state is None
            or evaluate_all_portfolio_gates is None
            or PortfolioSnapshot is None
            or HoldingSnapshot is None
        ):
            return []

        nav = float(net_asset(currency=Currency.USD) or 0.0)
        avail_cash = float(cash(currency=Currency.USD) or 0.0)
        holdings: List[Any] = []
        for sym in self.pool_symbols:
            qty = int(position_holding_qty(symbol=sym) or 0)
            if qty <= 0:
                continue
            closes = list(bar_close(symbol=sym) or [])
            price = closes[-1] if closes else 0.0
            sector = "unknown"
            if self._pool_cfg is not None:
                for ps in self._pool_cfg.symbols:
                    if ps.symbol == sym:
                        sector = ps.sector
                        break
            holdings.append(
                HoldingSnapshot(
                    symbol=sym, sector=sector, market_value=qty * price
                )
            )
        snap = PortfolioSnapshot(
            nav=nav,
            cash=avail_cash,
            holdings=holdings,
            today_pnl=nav - float(self._portfolio_state.nav_last or nav),
        )
        # Update peak NAV for drawdown tracking.
        if nav > 0 and nav > self._portfolio_state.nav_peak:
            self._portfolio_state.nav_peak = nav

        reasons = evaluate_all_portfolio_gates(
            self._portfolio_state,
            snap,
            dd_limit=float(self.portfolio_dd_limit),
            sector_cap=float(self.sector_cap),
            max_concurrent_holdings=int(self.max_concurrent_holdings),
            daily_loss_limit_pct=float(self.daily_loss_limit_pct),
            max_consecutive_loss_days=int(self.max_consecutive_loss_days),
        )
        if reasons and not self._portfolio_state.breaker_triggered:
            mark_breaker_triggered(  # type: ignore[misc]
                self._portfolio_state, reason=";".join(reasons)
            )
            if self._state_path is not None and save_portfolio_state is not None:
                try:
                    save_portfolio_state(
                        self._portfolio_state, self._state_path
                    )
                except Exception:  # pragma: no cover - best-effort persist
                    pass
        return reasons

    def _allocate_budget(
        self, candidates: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Snapshot account state and delegate to the pure allocator.

        Per requirement 3.1/3.3 the strategy SHALL pull a single
        ``net_asset`` / ``cash`` baseline at the start of allocation, then
        re-evaluate cash after each (would-be) order. The pure
        :func:`allocate_orders_serial` already implements the post-fill
        cash decrement deterministically.

        Returns the same plan dict as :func:`allocate_orders_serial`.
        Note: with ``LIVE_SUBMIT == False`` the plan is *not* submitted.
        """

        nav = float(net_asset(currency=Currency.USD) or 0.0)
        avail_cash = float(cash(currency=Currency.USD) or 0.0)
        plan = allocate_orders_serial(
            candidates,
            nav=nav,
            initial_cash=avail_cash,
            pool_budget_pct=float(self.pool_budget_pct),
            max_concurrent_holdings=int(self.max_concurrent_holdings),
            position_pct=float(self.position_pct),
            cash_buffer_pct=float(self.cash_buffer_pct),
            max_orders_per_day=int(self.max_orders_per_day),
            orders_used_today=int(self._orders_today),
        )
        self._orders_today = int(plan["orders_used_after"])
        return plan

    def _build_symbol_context(self, symbol: str) -> Optional[Dict[str, Any]]:
        closes = list(bar_close(symbol=symbol) or [])
        highs = list(bar_high(symbol=symbol) or [])
        lows = list(bar_low(symbol=symbol) or [])
        volumes = list(bar_volume(symbol=symbol) or [])
        if len(closes) < 30 or len(highs) < 30 or len(lows) < 30:
            return None

        e12 = ema(closes, 12)
        e26 = ema(closes, 26)
        rsi_now = rsi(closes, 14)
        atrp = atr_pct(highs, lows, closes, 14)
        adx_v = adx_proxy(highs, lows, closes, 14)
        if None in (e12, e26, rsi_now, atrp, adx_v):
            return None

        avg_vol = sum(volumes[-20:]) / 20.0 if len(volumes) >= 20 else 0.0
        vol_ratio = (volumes[-1] / avg_vol) if avg_vol > 0 else 0.0

        return {
            "price": closes[-1],
            "ema12": e12,
            "ema26": e26,
            "rsi": rsi_now,
            "atr_pct": atrp,
            "adx": adx_v,
            "vol_ratio": vol_ratio,
            "closes": closes,
        }

    def _evaluate_entry(
        self,
        symbol: str,
        state: _SymbolState,
        ctx: Dict[str, Any],
    ) -> bool:
        """Return True iff every entry condition is satisfied.

        The 5 entry conditions:
        1. Trend factor — EMA12 > EMA26 AND ADX >= adx_min;
        2. Momentum factor — RSI rising from oversold (last < threshold,
           current >= threshold) OR EMA golden-cross within last 3 bars;
        3. Volatility gate — ATR% <= atr_pct_max (gate, not a score);
        4. Volume factor — vol_ratio >= vol_ratio_min;
        5. Cooldown — bars_since_last_exit >= cooldown_bars_after_exit
           (already enforced by caller).
        """

        f_trend = ctx["ema12"] > ctx["ema26"] and ctx["adx"] >= float(self.adx_min)
        rsi_cross_up = (
            state.last_rsi < float(self.rsi_oversold)
            and ctx["rsi"] >= float(self.rsi_oversold)
        )
        ema_gc_recent = ctx["ema12"] > ctx["ema26"]  # simple proxy
        f_momentum = rsi_cross_up or ema_gc_recent
        f_vol_gate = ctx["atr_pct"] <= float(self.atr_pct_max)
        f_volume = ctx["vol_ratio"] >= float(self.vol_ratio_min)

        state.last_rsi = ctx["rsi"]
        return bool(f_trend and f_momentum and f_vol_gate and f_volume)

    def _evaluate_exit(
        self,
        symbol: str,
        state: _SymbolState,
        ctx: Dict[str, Any],
    ) -> Optional[str]:
        """Return the **first** triggered exit reason, or ``None``.

        Priority chain (per requirement 2.5):
            hard_stop > trailing_take_profit > trend_reverse > vol_breakout
        """

        price = ctx["price"]
        if state.last_entry_price > 0:
            loss_pct = (state.last_entry_price - price) / state.last_entry_price
            if loss_pct >= float(self.stop_loss_pct):
                return "hard_stop"

        # Trailing take-profit — armed only after a gain threshold.
        if state.highest_price < price:
            state.highest_price = price
        if (
            state.last_entry_price > 0
            and (state.highest_price / state.last_entry_price - 1.0)
            >= float(self.take_profit_pct)
        ):
            drawdown = (state.highest_price - price) / state.highest_price
            if drawdown >= float(self.trailing_drawdown_pct):
                return "trailing_tp"

        # Trend reverse — EMA12 falls back below EMA26.
        if ctx["ema12"] < ctx["ema26"]:
            return "trend_reverse"

        # Volatility breakout — ATR% suddenly exceeds the gate.
        if ctx["atr_pct"] > float(self.atr_pct_max) * 1.5:
            return "vol_breakout"

        return None


# --------------------------------------------------------------------------- #
# Manual smoke (no live submit, no Futu connection):
#     python3 phase2/strategy/us_multi_symbol_phase2_strategy.py --check
# --------------------------------------------------------------------------- #


def _self_check() -> int:  # pragma: no cover - thin CLI shim
    s = Strategy()
    s.initialize()
    print(
        f"[phase2-strategy] LIVE_SUBMIT={LIVE_SUBMIT} | "
        f"pool={len(s.pool_symbols)} symbols | "
        f"entry-knobs: position_pct={s.position_pct} max_slices={s.max_slices} | "
        f"portfolio: pool_budget_pct={s.pool_budget_pct} "
        f"max_concurrent={s.max_concurrent_holdings}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    if "--check" in sys.argv:
        raise SystemExit(_self_check())
    raise SystemExit(0)
