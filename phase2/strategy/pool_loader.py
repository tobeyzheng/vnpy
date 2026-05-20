"""US Multi-Symbol Quant Phase 2 — pool loader (schema-validation only).

This module is responsible **solely** for loading and schema-validating
``pool_config.yaml``. Runtime filters (liquidity / price / ATR% / earnings
freeze) live in the same module but are added in a separate task and only
read snapshots — they never mutate the YAML file.

Design notes:
- Hard cap: pool size <= 20 (project rule, see requirements.md §1.4).
- Currency must be USD; OTC and IPO < 1 year symbols are rejected.
- Schema failure ⇒ raise ``PoolConfigError`` with a clear message; callers
  decide whether to abort. The CLI wrapper aborts (non-zero exit code).
- No I/O side effect besides reading the YAML file. Logging only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover - environment guard
    raise ImportError(
        "PyYAML is required for pool_loader; install via `pip install pyyaml`."
    ) from exc


logger = logging.getLogger(__name__)

POOL_HARD_CAP = 20
ALLOWED_CURRENCY = "USD"
REQUIRED_SYMBOL_FIELDS = ("symbol", "market", "market_cap_bucket", "sector")
ALLOWED_MARKET_CAP_BUCKETS = {"mega", "large", "mid", "small"}
# Heuristic OTC suffix list — Futu market codes treat OTC venues as
# ``US.OTC*``. We reject any explicit OTC marker as a defence-in-depth.
_OTC_MARKERS = ("OTC", "PINK", "OTCBB")


class PoolConfigError(ValueError):
    """Raised when ``pool_config.yaml`` fails schema validation."""


@dataclass(frozen=True)
class PoolSymbol:
    symbol: str
    market: str
    market_cap_bucket: str
    sector: str

    def fq_symbol(self) -> str:
        """Return the fully-qualified Futu-style code, e.g. ``US.NVDA``."""

        if "." in self.symbol:
            return self.symbol
        return f"{self.market}.{self.symbol}"


@dataclass(frozen=True)
class PoolDefaults:
    liquidity_min_adv60_usd: float = 50_000_000.0
    price_min: float = 5.0
    price_max: float = 800.0
    atr_pct_max: float = 0.08
    earnings_freeze_days: int = 2
    market: str = "US"


@dataclass(frozen=True)
class PoolConfig:
    version: int
    currency: str
    max_pool_size: int
    defaults: PoolDefaults
    symbols: List[PoolSymbol] = field(default_factory=list)

    def symbol_list(self) -> List[str]:
        return [s.symbol for s in self.symbols]

    def fq_symbol_list(self) -> List[str]:
        return [s.fq_symbol() for s in self.symbols]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def load_pool_config(
    path: str | Path,
    *,
    ipo_blacklist: Optional[set[str]] = None,
) -> PoolConfig:
    """Load and validate ``pool_config.yaml``.

    Parameters
    ----------
    path:
        Path to the YAML file.
    ipo_blacklist:
        Optional set of symbols flagged as IPO < 1 year. When provided,
        any intersection with the configured pool raises ``PoolConfigError``.
        Production callers wire this from an external static file or data
        feed; tests inject the set directly.

    Returns
    -------
    PoolConfig
        Validated, immutable configuration object.

    Raises
    ------
    PoolConfigError
        On any schema violation (size, currency, missing fields, OTC, IPO).
    FileNotFoundError
        When ``path`` does not exist.
    """

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"pool_config.yaml not found: {p}")

    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise PoolConfigError(
            f"pool_config.yaml root must be a mapping, got {type(raw).__name__}."
        )

    return _validate(raw, ipo_blacklist or set())


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _validate(raw: Dict[str, Any], ipo_blacklist: set[str]) -> PoolConfig:
    currency = str(raw.get("currency", "")).strip().upper()
    if currency != ALLOWED_CURRENCY:
        raise PoolConfigError(
            f"currency must be {ALLOWED_CURRENCY}, got {currency!r}."
        )

    declared_cap = int(raw.get("max_pool_size", POOL_HARD_CAP))
    if declared_cap > POOL_HARD_CAP:
        raise PoolConfigError(
            f"max_pool_size {declared_cap} exceeds hard cap {POOL_HARD_CAP}."
        )

    raw_symbols = raw.get("symbols")
    if not isinstance(raw_symbols, list) or not raw_symbols:
        raise PoolConfigError("symbols must be a non-empty list.")

    if len(raw_symbols) > POOL_HARD_CAP:
        raise PoolConfigError(
            f"pool size {len(raw_symbols)} exceeds hard cap {POOL_HARD_CAP}."
        )
    if len(raw_symbols) > declared_cap:
        raise PoolConfigError(
            f"pool size {len(raw_symbols)} exceeds declared max_pool_size {declared_cap}."
        )

    symbols: List[PoolSymbol] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_symbols):
        if not isinstance(item, dict):
            raise PoolConfigError(
                f"symbols[{idx}] must be a mapping, got {type(item).__name__}."
            )
        missing = [f for f in REQUIRED_SYMBOL_FIELDS if f not in item]
        if missing:
            raise PoolConfigError(
                f"symbols[{idx}] missing required fields: {missing}."
            )
        sym = str(item["symbol"]).strip().upper()
        if not sym:
            raise PoolConfigError(f"symbols[{idx}].symbol is empty.")
        if any(marker in sym for marker in _OTC_MARKERS):
            raise PoolConfigError(
                f"symbols[{idx}]={sym} is OTC-like and not allowed in phase 2 pool."
            )
        if sym in seen:
            raise PoolConfigError(f"duplicate symbol in pool: {sym}.")
        if sym in ipo_blacklist:
            raise PoolConfigError(
                f"symbol {sym} is flagged as IPO<1y; cannot enter phase 2 pool."
            )
        bucket = str(item["market_cap_bucket"]).strip().lower()
        if bucket not in ALLOWED_MARKET_CAP_BUCKETS:
            raise PoolConfigError(
                f"symbols[{idx}]={sym}: market_cap_bucket {bucket!r} not in "
                f"{sorted(ALLOWED_MARKET_CAP_BUCKETS)}."
            )
        seen.add(sym)
        symbols.append(
            PoolSymbol(
                symbol=sym,
                market=str(item["market"]).strip().upper(),
                market_cap_bucket=bucket,
                sector=str(item["sector"]).strip(),
            )
        )

    raw_defaults = raw.get("defaults") or {}
    defaults = PoolDefaults(
        liquidity_min_adv60_usd=float(
            raw_defaults.get("liquidity_min_adv60_usd", 50_000_000.0)
        ),
        price_min=float(raw_defaults.get("price_min", 5.0)),
        price_max=float(raw_defaults.get("price_max", 800.0)),
        atr_pct_max=float(raw_defaults.get("atr_pct_max", 0.08)),
        earnings_freeze_days=int(raw_defaults.get("earnings_freeze_days", 2)),
        market=str(raw_defaults.get("market", "US")).strip().upper(),
    )

    cfg = PoolConfig(
        version=int(raw.get("version", 1)),
        currency=currency,
        max_pool_size=declared_cap,
        defaults=defaults,
        symbols=symbols,
    )
    logger.info(
        "loaded phase2 pool_config: %d symbols (cap=%d).",
        len(symbols),
        declared_cap,
    )
    return cfg


# --------------------------------------------------------------------------- #
# Runtime filters (read-only — never mutate pool_config.yaml).
#
# Each filter returns a tuple ``(passed: bool, reason: str)``. Reason ``""``
# indicates the symbol is admitted; otherwise a short tag such as
# ``"low_adv60"`` is returned for downstream logging / metrics.
# --------------------------------------------------------------------------- #


def filter_liquidity(adv60_usd: float, min_adv60_usd: float) -> tuple[bool, str]:
    """Liquidity gate based on 60-day average dollar volume."""

    if adv60_usd < 0:
        return False, "invalid_adv60"
    if adv60_usd < min_adv60_usd:
        return False, "low_adv60"
    return True, ""


def filter_price_band(
    price: float, price_min: float, price_max: float
) -> tuple[bool, str]:
    """Reject penny stocks and runaway names."""

    if price <= 0:
        return False, "invalid_price"
    if price < price_min:
        return False, "below_price_min"
    if price > price_max:
        return False, "above_price_max"
    return True, ""


def filter_atr_pct(atr_pct: float, atr_pct_max: float) -> tuple[bool, str]:
    """Volatility gate (ATR / close)."""

    if atr_pct < 0:
        return False, "invalid_atr"
    if atr_pct > atr_pct_max:
        return False, "atr_too_high"
    return True, ""


def filter_earnings_freeze(
    symbol: str,
    today_iso: str,
    earnings_calendar: Dict[str, List[str]],
    freeze_days: int = 2,
) -> tuple[bool, str]:
    """Reject the ±N trading days around the next earnings date.

    ``earnings_calendar`` is a mapping of symbol → ISO date strings.
    Calendar uses *calendar* days as a conservative upper bound (we do not
    have exchange calendar here); strategy layer can refine to trading days.
    """

    from datetime import date

    def _parse(s: str) -> Optional[date]:
        try:
            y, m, d = s.split("-")
            return date(int(y), int(m), int(d))
        except (ValueError, AttributeError):
            return None

    today = _parse(today_iso)
    if today is None:
        return False, "invalid_today"
    if freeze_days < 0:
        return False, "invalid_freeze_days"

    for ev in earnings_calendar.get(symbol.upper(), []):
        ev_d = _parse(ev)
        if ev_d is None:
            continue
        if abs((ev_d - today).days) <= freeze_days:
            return False, "earnings_freeze"
    return True, ""


def apply_runtime_filters(
    cfg: PoolConfig,
    *,
    market_snapshot: Dict[str, Dict[str, float]],
    earnings_calendar: Optional[Dict[str, List[str]]] = None,
    today_iso: Optional[str] = None,
) -> tuple[List[str], Dict[str, str]]:
    """Apply the four runtime filters to a pool snapshot.

    Parameters
    ----------
    cfg:
        Validated pool config (from :func:`load_pool_config`).
    market_snapshot:
        ``{symbol: {"price": float, "adv60_usd": float, "atr_pct": float}}``.
        Symbols missing keys are conservatively rejected.
    earnings_calendar:
        Optional ``{symbol: [iso_date, ...]}`` mapping. When None the
        earnings filter is skipped (e.g. unit tests for other filters).
    today_iso:
        ISO date string for earnings-freeze evaluation. Required only when
        ``earnings_calendar`` is provided.

    Returns
    -------
    (admitted, rejected)
        ``admitted`` — list of symbols passing every active filter.
        ``rejected`` — ``{symbol: reason_tag}`` for diagnostics. Filters do
        **not** mutate ``cfg``.
    """

    admitted: List[str] = []
    rejected: Dict[str, str] = {}
    d = cfg.defaults

    for s in cfg.symbols:
        snap = market_snapshot.get(s.symbol)
        if not snap:
            rejected[s.symbol] = "no_snapshot"
            continue

        ok, why = filter_liquidity(
            float(snap.get("adv60_usd", -1.0)), d.liquidity_min_adv60_usd
        )
        if not ok:
            rejected[s.symbol] = why
            continue

        ok, why = filter_price_band(
            float(snap.get("price", -1.0)), d.price_min, d.price_max
        )
        if not ok:
            rejected[s.symbol] = why
            continue

        ok, why = filter_atr_pct(float(snap.get("atr_pct", -1.0)), d.atr_pct_max)
        if not ok:
            rejected[s.symbol] = why
            continue

        if earnings_calendar is not None and today_iso is not None:
            ok, why = filter_earnings_freeze(
                s.symbol, today_iso, earnings_calendar, d.earnings_freeze_days
            )
            if not ok:
                rejected[s.symbol] = why
                continue

        admitted.append(s.symbol)

    return admitted, rejected


# --------------------------------------------------------------------------- #
# Pool change log helper — writes to docs/project_operation_log.md.
# Filters call this **only** when the persistent pool membership changes
# (add/remove), never for transient daily admit/reject events.
# --------------------------------------------------------------------------- #


def append_pool_change_log(
    log_path: str | Path,
    *,
    added: Optional[List[str]] = None,
    removed: Optional[List[str]] = None,
    reason: str = "",
    date_iso: Optional[str] = None,
) -> None:
    """Append a single bullet entry to ``docs/project_operation_log.md``.

    The log line format is intentionally simple Markdown so reviewers can
    grep historic pool moves quickly:

    ``- 2026-05-20 phase2-pool 池变更：+AAPL,+NVDA / -META（理由：流动性下降）``
    """

    from datetime import date

    if not added and not removed:
        return  # no-op for empty calls

    iso = date_iso or date.today().isoformat()
    plus = ",".join(f"+{s}" for s in (added or []))
    minus = ",".join(f"-{s}" for s in (removed or []))
    delta = " / ".join(part for part in (plus, minus) if part)
    reason_tail = f"（理由：{reason}）" if reason else ""
    line = f"- {iso} phase2-pool 池变更：{delta}{reason_tail}\n"

    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(line)


# --------------------------------------------------------------------------- #
# CLI entry — keeps the loader runnable for quick smoke checks.
# --------------------------------------------------------------------------- #

def _cli() -> int:  # pragma: no cover - thin CLI shim
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Validate phase2 pool_config.yaml.")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config" / "pool_config.yaml"),
        help="path to pool_config.yaml",
    )
    args = parser.parse_args()

    try:
        cfg = load_pool_config(args.config)
    except (PoolConfigError, FileNotFoundError) as exc:
        print(f"[pool_loader] FAIL: {exc}", file=sys.stderr)
        return 2
    print(
        f"[pool_loader] OK: {len(cfg.symbols)} symbols, "
        f"currency={cfg.currency}, cap={cfg.max_pool_size}."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
