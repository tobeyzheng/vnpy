# -*- coding: utf-8 -*-
"""Per-symbol bucketed runtime + futumd DSL namespace for phase-② backtest.

The phase-② futumd strategy
(`phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`) is written
to the Futu quant platform contract: it expects a global namespace where
``bar_close(symbol=..., bar_type=..., select=k, session_type=...)`` etc.
are already defined.  The strategy file ships a *stdlib-only stub block*
that activates only when those names are NOT defined at import time.

This adapter takes the **opposite path**: before exec'ing the strategy
file we pre-populate a namespace with real implementations that read
from a per-symbol bucketed runtime.  Because the names are defined,
the stub block is skipped and the strategy code runs against our
buckets transparently.

Key contract notes (taken verbatim from the futumd strategy):
- ``select=k`` means *the k-th most recent closed bar*; ``select=1`` is
  the latest closed bar, ``select=2`` is the previous one, etc.
- ``bar_close`` / ``bar_high`` / ``bar_low`` / ``bar_volume`` / ``bar_open``
  must accept ``symbol=str`` and route reads to that symbol's bucket.
- ``cash(currency=)`` and ``net_asset(currency=)`` are *portfolio*-scoped:
  one cash pool shared across the whole pool.
- ``position_holding_qty(symbol=)`` returns the current shares held for
  the symbol (>= 0; strategy is long-only).
- ``place_limit(symbol=, price=, qty=, side=, time_in_force=)`` enqueues
  an intent; the engine fills it on the *next bar's open* to avoid
  look-ahead bias.
- ``close_positions(symbol=, qty=)`` enqueues a full-position close;
  same next-bar-open settlement.
- ``alert(title=, content=)`` is captured into a log list (no I/O).
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Tuple


# --------------------------------------------------------------------------- #
# Per-symbol bar buffer
# --------------------------------------------------------------------------- #


@dataclass
class _SymbolBars:
    """Append-only OHLCV buffer for a single symbol.

    Bars are stored in chronological order: index 0 is the *oldest*,
    index -1 is the *most recent closed* bar.  ``select=k`` in DSL maps
    to ``buffer[-k]``.
    """

    closes: List[float] = field(default_factory=list)
    opens: List[float] = field(default_factory=list)
    highs: List[float] = field(default_factory=list)
    lows: List[float] = field(default_factory=list)
    volumes: List[float] = field(default_factory=list)

    def append(self, o: float, h: float, l: float, c: float, v: float) -> None:
        self.opens.append(float(o))
        self.highs.append(float(h))
        self.lows.append(float(l))
        self.closes.append(float(c))
        self.volumes.append(float(v))

    def select(self, field_name: str, k: int) -> float:
        """Return the k-th most recent value of ``field_name``.

        Returns 0.0 if the buffer is too short — matches the futumd
        platform behaviour of returning a falsy value on missing data.
        """

        buf = getattr(self, field_name, None)
        if not buf or k <= 0 or k > len(buf):
            return 0.0
        return float(buf[-k])


# --------------------------------------------------------------------------- #
# Pending order intent
# --------------------------------------------------------------------------- #


@dataclass
class PendingOrder:
    action: str            # "BUY" / "SELL_CLOSE"
    symbol: str
    price_hint: float      # limit price the strategy asked for (logging only)
    qty: int               # signed-positive
    submit_idx: int        # bar index when submitted (for ledger)


# --------------------------------------------------------------------------- #
# Trade ledger entry
# --------------------------------------------------------------------------- #


@dataclass
class TradeRecord:
    symbol: str
    side: str              # "BUY" / "SELL"
    qty: int
    price: float           # actual fill price (next bar open)
    fee: float
    bar_index: int
    bar_datetime: str      # ISO-8601 string for CSV-friendly output


# --------------------------------------------------------------------------- #
# Portfolio runtime — the heart of the per-symbol bucketing
# --------------------------------------------------------------------------- #


@dataclass
class PortfolioRuntime:
    """Per-symbol bucketed runtime shared between adapter and engine.

    The adapter (DSL namespace) reads/writes via the closure created by
    :func:`build_futumd_namespace`; the engine drives the lifecycle by
    appending bars and settling pending orders.
    """

    symbols: List[str]
    cash_value: float
    bars: Dict[str, _SymbolBars] = field(default_factory=dict)
    positions: Dict[str, int] = field(default_factory=dict)
    entry_costs: Dict[str, float] = field(default_factory=dict)
    pending: List[PendingOrder] = field(default_factory=list)
    alerts: List[Tuple[str, str]] = field(default_factory=list)
    last_close: Dict[str, float] = field(default_factory=dict)
    bar_index: int = 0

    def __post_init__(self) -> None:
        for s in self.symbols:
            self.bars.setdefault(s, _SymbolBars())
            self.positions.setdefault(s, 0)
            self.entry_costs.setdefault(s, 0.0)
            self.last_close.setdefault(s, 0.0)

    # -- engine-side helpers ------------------------------------------------ #

    def push_bar(
        self,
        symbol: str,
        o: float,
        h: float,
        l: float,
        c: float,
        v: float,
    ) -> None:
        """Append a *closed* bar for ``symbol`` to its bucket."""

        if symbol not in self.bars:
            # Defensive: tolerate symbols added after init (rare in tests).
            self.bars[symbol] = _SymbolBars()
            self.positions.setdefault(symbol, 0)
            self.entry_costs.setdefault(symbol, 0.0)
            self.last_close.setdefault(symbol, 0.0)
            if symbol not in self.symbols:
                self.symbols.append(symbol)
        self.bars[symbol].append(o, h, l, c, v)
        self.last_close[symbol] = float(c)

    def net_asset_value(self) -> float:
        """Mark-to-market portfolio NAV using the latest closes."""

        nav = float(self.cash_value)
        for s, qty in self.positions.items():
            if qty <= 0:
                continue
            nav += qty * float(self.last_close.get(s, 0.0))
        return nav


# --------------------------------------------------------------------------- #
# DSL namespace builder
# --------------------------------------------------------------------------- #


def build_futumd_namespace(runtime: PortfolioRuntime) -> Dict[str, Any]:
    """Build the futumd-compatible namespace bound to ``runtime``.

    The returned dict is intended to be merged into the strategy module
    globals BEFORE exec'ing the strategy file, so that the strategy's
    top-level ``try: bar_close ... except NameError`` block sees every
    name as already defined and skips its stub block.
    """

    # ------- platform enums (light replicas — only ``.value`` matters) ----- #
    class _Enum:
        SECURITY = "SECURITY"
        BOOL = "BOOL"
        INT = "INT"
        FLOAT = "FLOAT"
        USD = "USD"
        K_DAY = "K_DAY"
        K_1M = "K_1M"
        K_5M = "K_5M"
        K_15M = "K_15M"
        K_30M = "K_30M"
        K_60M = "K_60M"
        RTH = "RTH"
        ETH = "ETH"
        BUY = "BUY"
        SELL = "SELL"
        DAY = "DAY"
        GTC = "GTC"

    class _StrategyBase:  # placeholder; the futumd Strategy subclasses this
        pass

    # ------- DSL functions (closures over runtime) ------------------------- #

    def _bar_field(field_name: str, symbol: str, select: int) -> float:
        sym = (symbol or "").strip().upper()
        bucket = runtime.bars.get(sym)
        if bucket is None:
            return 0.0
        return bucket.select(field_name, int(select) if select else 1)

    def bar_close(symbol: str = "", bar_type: Any = None,
                  select: int = 1, session_type: Any = None) -> float:
        return _bar_field("closes", symbol, select)

    def bar_open(symbol: str = "", bar_type: Any = None,
                 select: int = 1, session_type: Any = None) -> float:
        return _bar_field("opens", symbol, select)

    def bar_high(symbol: str = "", bar_type: Any = None,
                 select: int = 1, session_type: Any = None) -> float:
        return _bar_field("highs", symbol, select)

    def bar_low(symbol: str = "", bar_type: Any = None,
                select: int = 1, session_type: Any = None) -> float:
        return _bar_field("lows", symbol, select)

    def bar_volume(symbol: str = "", bar_type: Any = None,
                   select: int = 1, session_type: Any = None) -> float:
        return _bar_field("volumes", symbol, select)

    def cash(currency: Any = None) -> float:
        # Portfolio-level single cash pool (USD).  ``currency`` is
        # accepted for contract compatibility but ignored — the engine
        # only models USD per the phase-② pool config.
        return float(runtime.cash_value)

    def net_asset(currency: Any = None) -> float:
        return float(runtime.net_asset_value())

    def position_holding_qty(symbol: str = "") -> int:
        sym = (symbol or "").strip().upper()
        return int(runtime.positions.get(sym, 0))

    def place_limit(symbol: str = "", price: float = 0.0, qty: int = 0,
                    side: Any = None, time_in_force: Any = None) -> None:
        sym = (symbol or "").strip().upper()
        if not sym or qty <= 0 or price <= 0:
            return
        side_str = getattr(side, "value", side) or "BUY"
        side_str = str(side_str).upper()
        if side_str == "BUY":
            action = "BUY"
        elif side_str == "SELL":
            action = "SELL_CLOSE"
        else:
            return
        runtime.pending.append(
            PendingOrder(
                action=action,
                symbol=sym,
                price_hint=float(price),
                qty=int(qty),
                submit_idx=int(runtime.bar_index),
            )
        )

    def close_positions(symbol: str = "", qty: int = 0) -> None:
        sym = (symbol or "").strip().upper()
        held = int(runtime.positions.get(sym, 0))
        if held <= 0:
            return
        target_qty = int(qty) if qty and int(qty) > 0 else held
        target_qty = min(target_qty, held)
        if target_qty <= 0:
            return
        runtime.pending.append(
            PendingOrder(
                action="SELL_CLOSE",
                symbol=sym,
                price_hint=0.0,  # market-on-next-open; price determined at fill
                qty=target_qty,
                submit_idx=int(runtime.bar_index),
            )
        )

    def alert(title: str = "", content: str = "") -> None:
        runtime.alerts.append((str(title), str(content)))

    # ------- platform decorators / helpers --------------------------------- #

    def declare_strategy_type(_t: Any) -> None:
        return None

    def declare_trig_symbol() -> str:
        return "_TRIG_SLOT_"

    def show_variable(value: Any, _gtype: Any) -> Any:
        return value

    return {
        # base + decorators
        "StrategyBase": _StrategyBase,
        "declare_strategy_type": declare_strategy_type,
        "declare_trig_symbol": declare_trig_symbol,
        "show_variable": show_variable,
        # enums
        "AlgoStrategyType": _Enum,
        "GlobalType": _Enum,
        "Currency": _Enum,
        "BarType": _Enum,
        "THType": _Enum,
        "OrderSide": _Enum,
        "TimeInForce": _Enum,
        # DSL
        "bar_close": bar_close,
        "bar_open": bar_open,
        "bar_high": bar_high,
        "bar_low": bar_low,
        "bar_volume": bar_volume,
        "cash": cash,
        "net_asset": net_asset,
        "position_holding_qty": position_holding_qty,
        "place_limit": place_limit,
        "close_positions": close_positions,
        "alert": alert,
    }


# --------------------------------------------------------------------------- #
# Strategy loader
# --------------------------------------------------------------------------- #


def load_futumd_strategy(
    strategy_path: str | Path,
    runtime: PortfolioRuntime,
    *,
    module_name: str = "phase2_futumd_strategy_loaded",
) -> Tuple[ModuleType, type]:
    """Load the futumd strategy file with DSL names already injected.

    Parameters
    ----------
    strategy_path:
        Filesystem path to the futumd strategy file (must be the
        unmodified ``us_multi_symbol_phase2_strategy_futumd.py``).
    runtime:
        The portfolio runtime that the DSL closures will read/write.
    module_name:
        Module name used in ``sys.modules``. Different runs should use
        different module names so module-level state from a prior load
        does not leak.

    Returns
    -------
    (module, Strategy_class)
        ``module`` is the loaded module object (kept alive by the caller).
        ``Strategy_class`` is ``module.Strategy`` ready to be instantiated.
    """

    p = Path(strategy_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"futumd strategy file not found: {p}")

    spec = importlib.util.spec_from_file_location(module_name, str(p))
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot build module spec for {p}")
    module = importlib.util.module_from_spec(spec)

    # Inject the DSL namespace into the module globals BEFORE exec so
    # the strategy's top-level `try: bar_close ... except NameError`
    # block sees every name as already defined and skips its stub.
    ns = build_futumd_namespace(runtime)
    module.__dict__.update(ns)
    spec.loader.exec_module(module)

    if not hasattr(module, "Strategy"):
        raise AttributeError(
            f"{p} does not expose a top-level Strategy class — "
            "check the futumd contract."
        )
    return module, getattr(module, "Strategy")


# --------------------------------------------------------------------------- #
# Convenience: settle a pending order against a fill price (used by engine)
# --------------------------------------------------------------------------- #


def settle_pending(
    runtime: PortfolioRuntime,
    *,
    fill_price_for_symbol: Callable[[str], Optional[float]],
    fee_rate: float,
    slippage: float,
    bar_index: int,
    bar_datetime: str,
) -> List[TradeRecord]:
    """Drain ``runtime.pending`` and return the resulting trade ledger.

    Fill rule (next-bar-open settlement is the engine's responsibility;
    this function just looks up the price via the callback so it stays
    easy to unit-test):

    - BUY: fill_price = next_open * (1 + slippage); reject if the
      portfolio cash cannot cover qty * fill_price + fee.
    - SELL_CLOSE: fill_price = next_open * (1 - slippage); qty is
      clamped to the actual holding to avoid going short.
    - Symbol whose next bar is missing (data gap, weekend) is skipped
      and its order is dropped — strategies are expected to re-issue.
    """

    trades: List[TradeRecord] = []
    pending_now = list(runtime.pending)
    runtime.pending.clear()

    for order in pending_now:
        ref_price = fill_price_for_symbol(order.symbol)
        if ref_price is None or ref_price <= 0:
            continue  # data gap — drop intent silently

        if order.action == "BUY":
            fill_price = ref_price * (1.0 + slippage)
            qty = int(order.qty)
            if qty <= 0:
                continue
            notional = fill_price * qty
            fee = notional * fee_rate
            cost = notional + fee
            if cost > runtime.cash_value + 1e-9:
                # Re-scale down to the maximum affordable size.
                max_qty = int((runtime.cash_value - fee) // fill_price)
                if max_qty <= 0:
                    continue
                qty = max_qty
                notional = fill_price * qty
                fee = notional * fee_rate
                cost = notional + fee
            runtime.cash_value -= cost
            held = runtime.positions.get(order.symbol, 0)
            old_cost = runtime.entry_costs.get(order.symbol, 0.0)
            new_held = held + qty
            if new_held > 0:
                runtime.entry_costs[order.symbol] = (
                    (old_cost * held + fill_price * qty) / new_held
                )
            runtime.positions[order.symbol] = new_held
            trades.append(TradeRecord(
                symbol=order.symbol, side="BUY", qty=qty, price=fill_price,
                fee=fee, bar_index=bar_index, bar_datetime=bar_datetime,
            ))
        elif order.action == "SELL_CLOSE":
            held = runtime.positions.get(order.symbol, 0)
            qty = min(int(order.qty), held)
            if qty <= 0:
                continue
            fill_price = ref_price * (1.0 - slippage)
            notional = fill_price * qty
            fee = notional * fee_rate
            runtime.cash_value += notional - fee
            new_held = held - qty
            runtime.positions[order.symbol] = new_held
            if new_held == 0:
                runtime.entry_costs[order.symbol] = 0.0
            trades.append(TradeRecord(
                symbol=order.symbol, side="SELL", qty=qty, price=fill_price,
                fee=fee, bar_index=bar_index, bar_datetime=bar_datetime,
            ))
        # Unknown actions are dropped intentionally.

    return trades
