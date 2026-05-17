# -*- coding: utf-8 -*-
"""Futu DSL → vnpy CTA bridging adapter.

Goal
----
Run a strategy file written in *Futu Quant DSL* (e.g.
``tmp/strategy/strategy_simple_multifactor.py``) inside vnpy
``BacktestingEngine`` **without modifying the strategy source code**.

How it works
------------
1. Build a sandbox namespace pre-populated with futu DSL globals
   (``StrategyBase``, ``BarType``, ``OrderSide``, ``THType``, ``GlobalType``,
   ``AlgoStrategyType``, ``TimeInForce``, ``bar_close``, ``bar_volume``, ...).
2. ``exec`` the strategy file into that namespace and grab the resulting
   ``Strategy`` class.
3. Probe the strategy by instantiating it once with stub runtime to
   discover the parameter list and default values exposed via
   ``show_variable``.
4. Build a ``CtaTemplate`` subclass on the fly that:
   - declares ``parameters`` / ``variables`` for vnpy optimizer reflection;
   - on every ``on_bar`` updates the ArrayManager-like buffer and
     re-binds DSL globals (``bar_close`` etc.) to read from it;
   - translates ``place_limit`` / ``close_positions`` into ``self.buy`` /
     ``self.sell`` calls on the CTA template.
5. ``BarType.K_1M`` (and any other ``K_xx``) is **aliased to a single
   runtime interval** chosen by the caller (e.g. ``Interval.DAILY``),
   so the strategy source remains untouched while we control the bar
   resolution from outside.

Notes
-----
- We deliberately keep this as a **pure Python file** (no vnpy plug-ins,
  no class registration) so the same factory can build many different
  CTA classes for the same strategy with different intervals.
- ``cash()`` is approximated using the engine ``capital`` minus the cost
  of the current position (entry price × qty), since vnpy CTA does not
  expose a live free-cash figure.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from vnpy.trader.constant import Direction, Interval, Offset
from vnpy.trader.object import BarData, TickData

try:
    from vnpy_ctastrategy import CtaTemplate
    from vnpy_ctastrategy.base import EngineType
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "vnpy_ctastrategy is required for the Futu DSL adapter; "
        "install it via `pip install vnpy_ctastrategy`."
    ) from exc

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DSL constants
# ---------------------------------------------------------------------------
class _NS:
    """Tiny namespace helper used to expose DSL enum-like attributes."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self) -> str:  # pragma: no cover - cosmetics
        return f"_NS({self.__dict__!r})"


# ``BarType`` exposes K_1M / K_5M / ... as plain strings; the adapter does not
# branch on them since we alias every K-line to ``runtime_interval``.
BAR_TYPE = _NS(
    K_1M="K_1M",
    K_3M="K_3M",
    K_5M="K_5M",
    K_15M="K_15M",
    K_30M="K_30M",
    K_60M="K_60M",
    K_DAY="K_DAY",
    K_WEEK="K_WEEK",
    K_MON="K_MON",
)

ORDER_SIDE = _NS(BUY="BUY", SELL="SELL")
TIME_IN_FORCE = _NS(DAY="DAY", GTC="GTC", IOC="IOC", FOK="FOK")
TH_TYPE = _NS(ALL="ALL", REGULAR="REGULAR", AFTER_HOURS="AFTER_HOURS")
GLOBAL_TYPE = _NS(INT="INT", FLOAT="FLOAT", BOOL="BOOL", STRING="STRING")
ALGO_STRATEGY_TYPE = _NS(SECURITY="SECURITY", FUTURES="FUTURES", OPTION="OPTION")


class _StrategyBase:
    """Minimal stand-in for futu's ``StrategyBase``.

    The user-defined strategy inherits from this. We don't need to provide
    behaviour - vnpy CTA wrapper drives everything externally.
    """

    target: str = ""

    def trigger_symbols(self) -> None:  # noqa: D401 - DSL signature
        pass

    def custom_indicator(self) -> None:
        pass

    def global_variables(self) -> None:
        pass

    def initialize(self) -> None:
        pass

    def handle_data(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Sandbox builder
# ---------------------------------------------------------------------------
@dataclass
class _Runtime:
    """Mutable runtime state shared between the strategy code and the
    CtaTemplate wrapper. The DSL functions read/write this object."""

    # Bar buffers (each appended on every on_bar). Latest value at index -1.
    closes: List[float] = field(default_factory=list)
    opens: List[float] = field(default_factory=list)
    highs: List[float] = field(default_factory=list)
    lows: List[float] = field(default_factory=list)
    volumes: List[float] = field(default_factory=list)
    times: List[Any] = field(default_factory=list)

    # Position info maintained by the wrapper.
    pos: int = 0
    cash_value: float = 0.0
    last_close: float = 0.0
    target_symbol: str = ""

    # Pending orders queued by the strategy on the current bar.
    pending: List[Tuple[str, str, float, int]] = field(default_factory=list)
    # Each tuple: (action, symbol, price, qty). action in {"buy", "sell_close"}

    # Captured ``show_variable`` declarations during probe.
    declared_params: Dict[str, Tuple[Any, str]] = field(default_factory=dict)
    probe_mode: bool = False

    def reset_buffers(self) -> None:
        self.closes.clear()
        self.opens.clear()
        self.highs.clear()
        self.lows.clear()
        self.volumes.clear()
        self.times.clear()


def _build_dsl_namespace(rt: _Runtime) -> Dict[str, Any]:
    """Return a globals dict to ``exec`` the strategy source into."""

    # ---- futu DSL function shims (closures over rt) ----
    def show_variable(default: Any, var_type: str = "") -> Any:
        # During probe, we don't know the variable name here (it's the LHS of
        # an assignment). The caller side handles name capture by inspecting
        # the strategy class attributes after ``initialize`` returns. Here we
        # just return the default as-is.
        return default

    def declare_strategy_type(*args: Any, **kwargs: Any) -> None:
        return None

    def declare_trig_symbol(*args: Any, **kwargs: Any) -> str:
        # Strategy stores the result on ``self.target``; we feed in the
        # symbol externally on every bar via ``rt.target_symbol``.
        return rt.target_symbol or "TARGET"

    def _select_index(select: int) -> int:
        """Translate futu ``select`` (1-based, "the i-th most recent bar")
        into a Python negative index. ``select=1`` → ``[-1]`` (current /
        latest). The strategy's loop uses ``select=i+1`` with i in 0..n-1,
        so it walks backwards from the latest bar."""
        if select is None or select < 1:
            select = 1
        return -select

    def bar_close(symbol: str = "", bar_type: str = "", select: int = 1,
                  session_type: str = "ALL", **kwargs: Any) -> float:
        idx = _select_index(select)
        if not rt.closes or abs(idx) > len(rt.closes):
            return 0.0
        return float(rt.closes[idx])

    def bar_open(symbol: str = "", bar_type: str = "", select: int = 1,
                 session_type: str = "ALL", **kwargs: Any) -> float:
        idx = _select_index(select)
        if not rt.opens or abs(idx) > len(rt.opens):
            return 0.0
        return float(rt.opens[idx])

    def bar_high(symbol: str = "", bar_type: str = "", select: int = 1,
                 session_type: str = "ALL", **kwargs: Any) -> float:
        idx = _select_index(select)
        if not rt.highs or abs(idx) > len(rt.highs):
            return 0.0
        return float(rt.highs[idx])

    def bar_low(symbol: str = "", bar_type: str = "", select: int = 1,
                session_type: str = "ALL", **kwargs: Any) -> float:
        idx = _select_index(select)
        if not rt.lows or abs(idx) > len(rt.lows):
            return 0.0
        return float(rt.lows[idx])

    def bar_volume(symbol: str = "", bar_type: str = "", select: int = 1,
                   session_type: str = "ALL", **kwargs: Any) -> float:
        idx = _select_index(select)
        if not rt.volumes or abs(idx) > len(rt.volumes):
            return 0.0
        return float(rt.volumes[idx])

    def position_holding_qty(symbol: str = "", **kwargs: Any) -> int:
        return int(rt.pos)

    def cash(*args: Any, **kwargs: Any) -> float:
        return float(rt.cash_value)

    def place_limit(symbol: str = "", price: float = 0.0, qty: int = 0,
                    side: str = "BUY", time_in_force: str = "DAY",
                    **kwargs: Any) -> None:
        if qty <= 0 or price <= 0:
            return
        if side == ORDER_SIDE.BUY:
            rt.pending.append(("buy", symbol, float(price), int(qty)))
        else:
            rt.pending.append(("sell_close", symbol, float(price), int(qty)))

    def close_positions(symbol: str = "", qty: int = 0, **kwargs: Any) -> None:
        if qty <= 0:
            qty = rt.pos
        if qty <= 0:
            return
        # Use last close as a fallback price; the wrapper will convert this
        # into ``self.sell(price, qty)``.
        price = rt.last_close or 0.0
        rt.pending.append(("sell_close", symbol, float(price), int(qty)))

    def net_asset(*args: Any, **kwargs: Any) -> float:
        """Approximate net asset as cash + position market value."""
        pos_value = rt.pos * rt.last_close
        return float(rt.cash_value + pos_value)

    def alert(title: str = "", content: str = "", **kwargs: Any) -> None:
        logger.debug("[strategy alert] %s | %s", title, content)

    ns: Dict[str, Any] = {
        # --- base / decorators ---
        "StrategyBase": _StrategyBase,
        # --- enum-likes ---
        "BarType": BAR_TYPE,
        "OrderSide": ORDER_SIDE,
        "TimeInForce": TIME_IN_FORCE,
        "THType": TH_TYPE,
        "GlobalType": GLOBAL_TYPE,
        "AlgoStrategyType": ALGO_STRATEGY_TYPE,
        "Currency": _NS(USD="USD", HKD="HKD", CNY="CNY"),
        # --- DSL functions ---
        "show_variable": show_variable,
        "declare_strategy_type": declare_strategy_type,
        "declare_trig_symbol": declare_trig_symbol,
        "bar_close": bar_close,
        "bar_open": bar_open,
        "bar_high": bar_high,
        "bar_low": bar_low,
        "bar_volume": bar_volume,
        "position_holding_qty": position_holding_qty,
        "cash": cash,
        "net_asset": net_asset,
        "place_limit": place_limit,
        "close_positions": close_positions,
        "alert": alert,
        # Some Futu DSL strategies use these too; provide harmless stubs.
        "trigger_symbols": lambda *a, **kw: None,
        "custom_indicator": lambda *a, **kw: None,
    }
    return ns


# ---------------------------------------------------------------------------
# Strategy loading
# ---------------------------------------------------------------------------
def _load_strategy_class(strategy_path: Path, rt: _Runtime) -> Type[Any]:
    """Exec the strategy source into a sandbox and return the ``Strategy`` class."""
    code = Path(strategy_path).read_text(encoding="utf-8")
    ns = _build_dsl_namespace(rt)
    ns["__name__"] = f"futu_dsl_{Path(strategy_path).stem}"
    exec(compile(code, str(strategy_path), "exec"), ns, ns)

    cls = ns.get("Strategy")
    if cls is None:
        # Fallback: pick the first class subclassing _StrategyBase.
        for v in ns.values():
            if isinstance(v, type) and issubclass(v, _StrategyBase) and v is not _StrategyBase:
                cls = v
                break
    if cls is None:
        raise RuntimeError(
            f"No `Strategy` class found in {strategy_path}; futu DSL strategies "
            f"must define a class named `Strategy` inheriting from StrategyBase."
        )
    return cls


def _probe_parameters(strategy_cls: Type[Any], rt: _Runtime) -> Dict[str, Any]:
    """Instantiate the strategy once to capture the parameter set.

    The futu DSL pattern is:

    .. code-block:: python

        def global_variables(self):
            self.fast_window = show_variable(5, GlobalType.INT)
            self.slow_window = show_variable(20, GlobalType.INT)
            ...

    After ``initialize()`` has run, all such attributes live on the instance.
    We compare the instance's ``__dict__`` against a baseline ``StrategyBase``
    instance and pick up the new attributes whose values are scalar (int /
    float / bool / str). Those become tunable parameters.
    """
    rt.probe_mode = True
    inst = strategy_cls()
    if hasattr(inst, "initialize"):
        try:
            inst.initialize()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Probe initialize() raised %s; continuing", exc)
    rt.probe_mode = False

    params: Dict[str, Any] = {}
    skip_keys = {"target", "entry_price", "last_signal"}
    for k, v in vars(inst).items():
        if k.startswith("_") or k in skip_keys:
            continue
        if isinstance(v, (bool, int, float, str)):
            params[k] = v
    return params


# ---------------------------------------------------------------------------
# CtaTemplate factory
# ---------------------------------------------------------------------------
def make_cta_class(
    strategy_path: str | Path,
    runtime_interval: Interval = Interval.DAILY,
    class_name: Optional[str] = None,
    initial_capital: float = 100_000.0,
) -> Type[CtaTemplate]:
    """Build a ``CtaTemplate`` subclass that executes the futu DSL strategy.

    Parameters
    ----------
    strategy_path:
        Path to the futu DSL strategy file.
    runtime_interval:
        The vnpy ``Interval`` that ``BarType.K_1M`` (and any other ``K_xx``
        reference inside the strategy) is aliased to during execution. The
        strategy source is **not** modified.
    class_name:
        Override the generated class name (defaults to
        ``"FutuDsl_<filestem>_<interval>"``).
    initial_capital:
        Used only as a starting estimate of ``cash()`` until the wrapper
        sees its first fill.
    """
    strategy_path = Path(strategy_path)
    if not strategy_path.exists():
        raise FileNotFoundError(strategy_path)

    # ---- Probe strategy once to discover params ----
    probe_rt = _Runtime()
    probe_cls = _load_strategy_class(strategy_path, probe_rt)
    discovered = _probe_parameters(probe_cls, probe_rt)
    logger.info(
        "[adapter] discovered %d parameters from %s: %s",
        len(discovered), strategy_path.name, list(discovered.keys()),
    )

    if class_name is None:
        class_name = f"FutuDsl_{strategy_path.stem}_{runtime_interval.value}"

    parameters_list = sorted(discovered.keys())
    variables_list = ["last_signal", "entry_price"]
    default_params: Dict[str, Any] = dict(discovered)

    # ---- The CtaTemplate subclass ----
    class _FutuDslCta(CtaTemplate):
        author = "futu_dsl_adapter"
        parameters = parameters_list
        variables = variables_list

        # Inject discovered defaults as class attributes so vnpy optimizer
        # can both read and override them.
        for _k, _v in default_params.items():
            locals()[_k] = _v
        # Also expose the runtime variables.
        last_signal: str = ""
        entry_price: float = 0.0

        def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
            super().__init__(cta_engine, strategy_name, vt_symbol, setting)
            # Per-instance runtime; also re-build a private DSL namespace so
            # bar_close/place_limit close over *this* runtime.
            self._rt = _Runtime()
            self._rt.cash_value = float(initial_capital)
            self._dsl_ns = _build_dsl_namespace(self._rt)
            self._inner_cls = _load_strategy_class(strategy_path, self._rt)
            self._inner = self._inner_cls()
            # Sync param overrides from setting onto the inner instance so
            # show_variable() returns the optimizer's value next time.
            for k in self.parameters:
                if hasattr(self, k):
                    setattr(self._inner, k, getattr(self, k))
            # Run initialize() so the strategy can compute derived state.
            try:
                self._inner.initialize()
            except Exception as exc:  # noqa: BLE001
                self.write_log(f"inner initialize() failed: {exc}")
            # After initialize ran, *override again* with optimizer values
            # because show_variable() inside global_variables would have
            # reset them to defaults.
            for k in self.parameters:
                if hasattr(self, k):
                    setattr(self._inner, k, getattr(self, k))
            # Wire target symbol so declare_trig_symbol / handle_data align.
            self._rt.target_symbol = vt_symbol.split(".")[0]
            self._inner.target = self._rt.target_symbol

        # ---- CTA template lifecycle ----
        def on_init(self):
            self.write_log(f"[adapter] on_init {self.vt_symbol}")
            self.load_bar(30)

        def on_start(self):
            self.write_log("[adapter] on_start")

        def on_stop(self):
            self.write_log("[adapter] on_stop")

        def on_tick(self, tick: TickData):
            # We don't drive intraday from ticks in backtests.
            pass

        def on_bar(self, bar: BarData):
            rt = self._rt
            rt.closes.append(float(bar.close_price))
            rt.opens.append(float(bar.open_price))
            rt.highs.append(float(bar.high_price))
            rt.lows.append(float(bar.low_price))
            rt.volumes.append(float(bar.volume))
            rt.times.append(bar.datetime)
            rt.last_close = float(bar.close_price)

            # Bound buffers to keep memory in check (300 bars is way more
            # than any sensible warmup window in this adapter's strategies).
            CAP = 600
            if len(rt.closes) > CAP:
                trim = len(rt.closes) - CAP
                rt.closes = rt.closes[trim:]
                rt.opens = rt.opens[trim:]
                rt.highs = rt.highs[trim:]
                rt.lows = rt.lows[trim:]
                rt.volumes = rt.volumes[trim:]
                rt.times = rt.times[trim:]

            # Sync engine-side state into runtime.
            rt.pos = int(self.pos)
            # cash() is approximated as: capital + realized pnl - cost basis
            # We cannot read realized pnl directly from CtaTemplate, so we
            # approximate cash as `initial_capital - position_cost`.
            inner_entry = float(getattr(self._inner, "entry_price", 0.0) or 0.0)
            cost_basis = inner_entry * abs(rt.pos)
            rt.cash_value = max(0.0, initial_capital - cost_basis)

            # Re-sync param overrides every bar so optimizer values stick
            # even if the strategy reassigns inside global_variables.
            for k in self.parameters:
                if hasattr(self, k):
                    setattr(self._inner, k, getattr(self, k))

            # Drive the strategy.
            rt.pending.clear()
            try:
                self._inner.handle_data()
            except Exception as exc:  # noqa: BLE001
                self.write_log(f"handle_data error: {exc}")
                return

            # Mirror inner state for vnpy variables tab.
            self.last_signal = str(getattr(self._inner, "last_signal", ""))[:128]
            self.entry_price = float(getattr(self._inner, "entry_price", 0.0) or 0.0)

            # Translate queued DSL orders into CTA orders.
            for action, _sym, price, qty in rt.pending:
                if qty <= 0 or price <= 0:
                    continue
                if action == "buy":
                    # Allow both opening new positions AND adding to
                    # existing positions (pyramiding / scale-in).
                    if self.pos == 0:
                        self.buy(price, qty)
                    else:
                        # vnpy CTA uses buy() for both open-long and
                        # add-long; the engine tracks net position.
                        self.buy(price, qty)
                elif action == "sell_close":
                    if self.pos > 0:
                        self.sell(price, min(qty, self.pos))

            self.put_event()

        def on_order(self, order):
            pass

        def on_trade(self, trade):
            # Reset entry_price tracking on flat - only meaningful for the
            # single-symbol/single-side strategies we adapt.
            if self.pos == 0:
                self._inner.entry_price = 0.0

        def on_stop_order(self, stop_order):
            pass

    _FutuDslCta.__name__ = class_name
    _FutuDslCta.__qualname__ = class_name
    return _FutuDslCta


# ---------------------------------------------------------------------------
# Convenience: list available strategies under tmp/strategy
# ---------------------------------------------------------------------------
def list_available_strategies(strategy_dir: str | Path = "tmp/strategy") -> List[str]:
    p = Path(strategy_dir)
    if not p.exists():
        return []
    return sorted(
        f.name for f in p.glob("strategy_*.py") if f.name != "__init__.py"
    )