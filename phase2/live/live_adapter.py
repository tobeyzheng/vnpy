# -*- coding: utf-8 -*-
"""Per-symbol bucketed runtime + futumd DSL namespace for phase-② **live**.

This is the live counterpart of ``phase2/backtest/futumd_strategy_adapter.py``.
The futumd strategy file
(``phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py``) is shared
between backtest and live; it only ever sees DSL names that are already bound
in its module globals at import time.

The live adapter keeps the **read-side** semantics identical to the backtest
adapter (per-symbol bar buckets, single USD cash pool, long-only positions),
and replaces the **write-side** (``place_limit`` / ``close_positions``) with
a callback that emits a fully-populated ``OrderIntent`` for downstream
pre-trade gates and the live broker. The strategy source code therefore needs
zero changes.

Design notes:
- The DSL namespace exposes the *exact* same symbol set as
  ``phase2/backtest/futumd_strategy_adapter.build_futumd_namespace`` so
  `test_live_adapter_interface_alignment` can compare key sets directly.
- ``LivePortfolioRuntime`` is a small extension of ``PortfolioRuntime`` that
  also tracks ``rebalance_date`` (used to seed ``request_id``) and an
  ``intent_seq`` counter so two intents on the same (symbol, side, date) get
  unique request_ids without colliding.
- ``intent_callback`` is the **single** seam that the runner uses to plug in
  the four-stage pre-trade gate + broker.place_order; the adapter itself has
  no knowledge of risk, idempotency, reconciliation, or broker semantics.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Tuple

# IMPORTANT: we deliberately do NOT import from ``phase2.backtest.*`` because
# ``phase2/backtest/__init__.py`` re-exports ``portfolio_backtest_engine`` which
# at import time pulls in ``vnpy.trader.database.get_database`` and triggers
# heavy DB initialisation — incompatible with the "live must import without
# touching the database" requirement. Instead the (small) read-side helpers
# are duplicated below; their semantics MUST stay byte-for-byte equivalent to
# their backtest-adapter siblings (covered by ``test_live_adapter`` parity
# tests against ``build_futumd_namespace``).
from phase2.live.order_state import OrderIntent, make_intent

__all__ = [
    "LivePortfolioRuntime",
    "PortfolioRuntime",
    "_SymbolBars",
    "build_live_futumd_namespace",
    "load_live_futumd_strategy",
    "IntentCallback",
]


IntentCallback = Callable[[OrderIntent], None]


# ---------------------------------------------------------------------------
# Local copy of the backtest adapter's read-side primitives.
# ---------------------------------------------------------------------------


@dataclass
class _SymbolBars:
    """Append-only OHLCV buffer for a single symbol.

    Mirrors ``phase2.backtest.futumd_strategy_adapter._SymbolBars`` exactly.
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
        buf = getattr(self, field_name, None)
        if not buf or k <= 0 or k > len(buf):
            return 0.0
        return float(buf[-k])


@dataclass
class PortfolioRuntime:
    """Per-symbol bucketed runtime — local copy mirroring backtest adapter.

    ``LivePortfolioRuntime`` extends this with rebalance metadata. Live and
    backtest share the same semantics here so the futumd strategy file reads
    bars and positions identically.
    """

    symbols: List[str]
    cash_value: float
    bars: Dict[str, _SymbolBars] = field(default_factory=dict)
    positions: Dict[str, int] = field(default_factory=dict)
    entry_costs: Dict[str, float] = field(default_factory=dict)
    pending: List[Any] = field(default_factory=list)  # unused in live; kept for API parity
    alerts: List[Tuple[str, str]] = field(default_factory=list)
    last_close: Dict[str, float] = field(default_factory=dict)
    bar_index: int = 0

    def __post_init__(self) -> None:
        for s in self.symbols:
            self.bars.setdefault(s, _SymbolBars())
            self.positions.setdefault(s, 0)
            self.entry_costs.setdefault(s, 0.0)
            self.last_close.setdefault(s, 0.0)

    def push_bar(
        self,
        symbol: str,
        o: float,
        h: float,
        l: float,
        c: float,
        v: float,
    ) -> None:
        if symbol not in self.bars:
            self.bars[symbol] = _SymbolBars()
            self.positions.setdefault(symbol, 0)
            self.entry_costs.setdefault(symbol, 0.0)
            self.last_close.setdefault(symbol, 0.0)
            if symbol not in self.symbols:
                self.symbols.append(symbol)
        self.bars[symbol].append(o, h, l, c, v)
        self.last_close[symbol] = float(c)

    def net_asset_value(self) -> float:
        nav = float(self.cash_value)
        for s, qty in self.positions.items():
            if qty <= 0:
                continue
            nav += qty * float(self.last_close.get(s, 0.0))
        return nav


@dataclass
class LivePortfolioRuntime(PortfolioRuntime):
    """Live runtime: extends backtest runtime with rebalance metadata.

    Extra fields:
    - ``strategy_id``    : carries through into ``OrderIntent.strategy_id``.
    - ``rebalance_date`` : ISO-8601 string ``YYYY-MM-DD`` used to seed
      ``request_id`` so the same intent across process restarts collapses to
      the same id.
    - ``execution_env``  : "dry_run" | "futu_sim" | "futu_real"; copied into
      every ``OrderIntent`` so audit logs are unambiguous.
    - ``alerts_emitted`` : phase2 live writes alerts to ``events.jsonl``;
      this list is the in-memory mirror that the runner snapshots at the
      end of the rebalance.
    - ``_intent_seq``    : monotonic counter consumed by ``place_limit`` to
      disambiguate multiple intents that share (symbol, side, date).
    """

    strategy_id: str = "phase2_us_multi"
    rebalance_date: str = ""
    execution_env: str = "dry_run"
    alerts_emitted: List[Tuple[str, str]] = field(default_factory=list)
    _intent_seq: int = 0


def build_live_futumd_namespace(
    runtime: LivePortfolioRuntime,
    *,
    intent_callback: IntentCallback,
) -> Dict[str, Any]:
    """Build the live futumd-compatible namespace bound to ``runtime``.

    The DSL key set MUST match
    ``phase2.backtest.futumd_strategy_adapter.build_futumd_namespace`` so the
    strategy file's stub block stays inactive in both modes. See
    ``test_live_adapter.py::test_namespace_keys_match_backtest`` for the
    enforced invariant.
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

    class _StrategyBase:
        pass

    # ------- read-side DSL (mirror of backtest adapter) -------------------- #

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
        return float(runtime.cash_value)

    def net_asset(currency: Any = None) -> float:
        return float(runtime.net_asset_value())

    def position_holding_qty(symbol: str = "") -> int:
        sym = (symbol or "").strip().upper()
        return int(runtime.positions.get(sym, 0))

    # ------- write-side DSL (live: emit OrderIntent via callback) ---------- #

    def _next_seq() -> int:
        runtime._intent_seq += 1
        return runtime._intent_seq

    def _resolve_side(side: Any) -> str:
        side_str = getattr(side, "value", side) or "BUY"
        return str(side_str).upper()

    def _emit_intent(*, symbol: str, side: str, qty: int, price: float | None,
                     reason: str) -> None:
        market = "US"  # phase2 live milestone is US-only.
        intent = make_intent(
            strategy_id=runtime.strategy_id,
            symbol=symbol,
            market=market,
            side=side,
            qty=qty,
            price=price,
            rebalance_date=runtime.rebalance_date,
            seq=_next_seq(),
            execution_env=runtime.execution_env,
            execution_channel="futu",
            source_phase="live_session",
            reason=reason,
            target_position_pct=0.0,
        )
        intent_callback(intent)

    def place_limit(symbol: str = "", price: float = 0.0, qty: int = 0,
                    side: Any = None, time_in_force: Any = None) -> None:
        sym = (symbol or "").strip().upper()
        side_str = _resolve_side(side)
        if (
            not sym
            or qty is None
            or int(qty) <= 0
            or price is None
            or float(price) <= 0
            or side_str not in {"BUY", "SELL"}
        ):
            # Reject silently here; the runner is responsible for writing the
            # ``order_blocked`` event with reason="invalid_intent" by
            # observing that the callback was never called for this slot.
            runtime.alerts_emitted.append(
                ("invalid_intent", f"symbol={sym!r} side={side_str} qty={qty} price={price}")
            )
            return
        _emit_intent(
            symbol=sym, side=side_str, qty=int(qty), price=float(price),
            reason="place_limit",
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
        last_close = float(runtime.last_close.get(sym, 0.0))
        # Live close is implemented as a SELL limit at the latest close. The
        # broker's order type is LIMIT (futu OrderType.NORMAL); price=0 is
        # NOT acceptable for futu place_order, so we fall back to last_close
        # if the strategy didn't tell us a price. If last_close is also 0
        # (data gap), reject and emit an alert.
        if last_close <= 0:
            runtime.alerts_emitted.append(
                ("close_positions_no_price", f"symbol={sym}")
            )
            return
        _emit_intent(
            symbol=sym, side="SELL", qty=target_qty, price=last_close,
            reason="close_positions",
        )

    def alert(title: str = "", content: str = "") -> None:
        runtime.alerts_emitted.append((str(title), str(content)))
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


def load_live_futumd_strategy(
    strategy_path: str | Path,
    runtime: LivePortfolioRuntime,
    *,
    intent_callback: IntentCallback,
    module_name: str = "phase2_live_futumd_strategy_loaded",
) -> Tuple[ModuleType, type]:
    """Load the futumd strategy file with the **live** DSL injected.

    Mirrors ``phase2.backtest.futumd_strategy_adapter.load_futumd_strategy``
    except the namespace's ``place_limit``/``close_positions`` route to
    ``intent_callback`` instead of populating a backtest pending list.
    """

    p = Path(strategy_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"futumd strategy file not found: {p}")

    spec = importlib.util.spec_from_file_location(module_name, str(p))
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot build module spec for {p}")
    module = importlib.util.module_from_spec(spec)

    ns = build_live_futumd_namespace(runtime, intent_callback=intent_callback)
    module.__dict__.update(ns)
    spec.loader.exec_module(module)

    if not hasattr(module, "Strategy"):
        raise AttributeError(
            f"{p} does not expose a top-level Strategy class — "
            "check the futumd contract."
        )
    return module, getattr(module, "Strategy")
