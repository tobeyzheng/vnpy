"""Futu OpenD historical bar data manager.

Pulls K-line bars from a local Futu OpenD gateway and persists them in the
vnpy SQLite database. Maintains a cache index JSON to avoid duplicate
downloads and to support incremental refresh.

Design notes
------------
- Storage of truth: vnpy SQLite database (``~/.vntrader/database.db``).
  ``BacktestingEngine.load_data`` consumes from it natively.
- Cache index: ``tmp/data/cache_index.json`` records the covered date range
  per ``(futu_code, kl_type, autype)`` triple. The DB unique index acts as a
  second-line guard against duplicates.
- Connection: respects env ``FUTU_OPEND_HOST`` / ``FUTU_OPEND_PORT`` /
  ``FUTU_OPEND_PASSWORD``; defaults to ``127.0.0.1:11111`` no password.
- Throttle: sleeps ``0.5s`` between paged calls; retries 3 times with
  exponential backoff on transient errors.
- Scope of v1: HK / US / A-share spot equities; daily and 1m/5m/15m/30m/60m
  bars.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData

from .symbol_mapping import (
    futu_to_vnpy,
    futu_to_vnpy_interval,
    parse_interval_alias,
)


logger = logging.getLogger(__name__)


GATEWAY_NAME = "FUTU_OPEND"
DEFAULT_CACHE_INDEX = Path(__file__).resolve().parent.parent / "data" / "cache_index.json"

# Per-call paging size; futu hard cap is 1000.
PAGE_SIZE = 1000
# Sleep between paged requests to stay friendly to OpenD rate limits.
PAGE_SLEEP_SEC = 0.5
# Retries for transient OpenD errors.
MAX_RETRIES = 3


class OpenDPullError(RuntimeError):
    """Raised when OpenD permanently fails to return data after retries."""


@dataclass(frozen=True)
class PullResult:
    futu_code: str
    kl_type: str
    requested_start: str
    requested_end: str
    fetched_bars: int
    cache_hit: bool
    final_start: Optional[str]
    final_end: Optional[str]


class FutuDataManager:
    """High-level manager for pulling and caching Futu OpenD K-lines."""

    def __init__(
        self,
        cache_index_path: Path = DEFAULT_CACHE_INDEX,
        host: Optional[str] = None,
        port: Optional[int] = None,
        password: Optional[str] = None,
    ) -> None:
        self.cache_index_path = Path(cache_index_path)
        self.cache_index_path.parent.mkdir(parents=True, exist_ok=True)

        self.host = host or os.environ.get("FUTU_OPEND_HOST", "127.0.0.1")
        self.port = int(port if port is not None else os.environ.get("FUTU_OPEND_PORT", 11111))
        self.password = password if password is not None else os.environ.get("FUTU_OPEND_PASSWORD", "")

        self._db = get_database()
        self._cache: dict = self._load_cache_index()
        self._quote_ctx = None  # lazy

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def pull(
        self,
        futu_codes: Iterable[str],
        start: str,
        end: str,
        interval_alias: str = "1d",
        autype: str = "qfq",
        force: bool = False,
    ) -> List[PullResult]:
        """Pull K-line bars for ``futu_codes`` over ``[start, end]``.

        Parameters
        ----------
        futu_codes:
            List of futu-format codes such as ``["HK.00700", "US.AAPL"]``.
        start, end:
            ``YYYY-MM-DD`` inclusive boundaries.
        interval_alias:
            User-friendly interval alias understood by ``parse_interval_alias``
            (``"1d"`` / ``"1m"`` / ``"5m"`` / ``"15m"`` / ``"30m"`` / ``"60m"``).
        autype:
            Adjust type, one of ``"qfq"`` (前复权) / ``"hfq"`` (后复权) / ``"none"``.
        force:
            If ``True``, ignore cache and re-pull the full range.
        """
        kl_type = parse_interval_alias(interval_alias)
        results: List[PullResult] = []
        for code in futu_codes:
            res = self._pull_one(code, kl_type, autype, start, end, force=force)
            results.append(res)
        self._save_cache_index()
        return results

    def status(self) -> dict:
        """Return a copy of the in-memory cache index for inspection."""
        return json.loads(json.dumps(self._cache))

    def close(self) -> None:
        if self._quote_ctx is not None:
            try:
                self._quote_ctx.close()
            except Exception:  # noqa: BLE001 - best-effort
                pass
            self._quote_ctx = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _cache_key(self, futu_code: str, kl_type: str, autype: str) -> str:
        return f"{futu_code}|{kl_type}|{autype}"

    def _load_cache_index(self) -> dict:
        if not self.cache_index_path.exists():
            return {"version": 1, "entries": {}}
        try:
            data = json.loads(self.cache_index_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "entries" not in data:
                logger.warning("cache_index.json corrupted, recreating")
                return {"version": 1, "entries": {}}
            return data
        except json.JSONDecodeError:
            logger.warning("cache_index.json malformed JSON, recreating")
            return {"version": 1, "entries": {}}

    def _save_cache_index(self) -> None:
        self.cache_index_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _ensure_quote_ctx(self):
        if self._quote_ctx is not None:
            return self._quote_ctx
        # Local import so that import of this module does not require futu_api.
        from futu import OpenQuoteContext  # type: ignore

        ctx = OpenQuoteContext(host=self.host, port=self.port)
        if self.password:
            try:
                ctx.unlock_trade(self.password)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                # unlock_trade is for trade ctx; quote ctx does not need it.
                pass
        self._quote_ctx = ctx
        return ctx

    # ------------------------------------------------------------------
    # Core per-symbol pull with cache & gap detection
    # ------------------------------------------------------------------
    def _pull_one(
        self,
        futu_code: str,
        kl_type: str,
        autype: str,
        start: str,
        end: str,
        force: bool,
    ) -> PullResult:
        key = self._cache_key(futu_code, kl_type, autype)
        entry = self._cache["entries"].get(key)

        gaps: List[Tuple[str, str]]
        if force or entry is None:
            gaps = [(start, end)]
        else:
            gaps = _compute_gaps(entry["start"], entry["end"], start, end)

        if not gaps:
            logger.info("[cache hit] %s %s [%s, %s] -> 0 fetched", futu_code, kl_type, start, end)
            return PullResult(
                futu_code=futu_code,
                kl_type=kl_type,
                requested_start=start,
                requested_end=end,
                fetched_bars=0,
                cache_hit=True,
                final_start=entry["start"],
                final_end=entry["end"],
            )

        symbol, exchange = futu_to_vnpy(futu_code)
        vn_interval = futu_to_vnpy_interval(kl_type)

        total_fetched = 0
        for gap_start, gap_end in gaps:
            logger.info("[fetch] %s %s gap [%s, %s]", futu_code, kl_type, gap_start, gap_end)
            bars = self._fetch_with_retry(futu_code, kl_type, autype, gap_start, gap_end)
            if not bars:
                logger.info("  -> 0 rows returned")
                continue
            bar_objs = [
                _make_bar(symbol, exchange, vn_interval, b) for b in bars
            ]
            self._db.save_bar_data(bar_objs)
            total_fetched += len(bar_objs)
            logger.info("  -> saved %d bars", len(bar_objs))

        # Update cache entry to the union of [old, new].
        new_start = start if entry is None else min(entry["start"], start)
        new_end = end if entry is None else max(entry["end"], end)
        if force:
            new_start, new_end = start, end

        self._cache["entries"][key] = {
            "futu_code": futu_code,
            "kl_type": kl_type,
            "autype": autype,
            "start": new_start,
            "end": new_end,
            "last_updated_at": datetime.now().isoformat(timespec="seconds"),
            "source": "futu_opend",
        }

        return PullResult(
            futu_code=futu_code,
            kl_type=kl_type,
            requested_start=start,
            requested_end=end,
            fetched_bars=total_fetched,
            cache_hit=False,
            final_start=new_start,
            final_end=new_end,
        )

    # ------------------------------------------------------------------
    # OpenD network plumbing
    # ------------------------------------------------------------------
    def _fetch_with_retry(
        self,
        futu_code: str,
        kl_type: str,
        autype: str,
        start: str,
        end: str,
    ) -> List[dict]:
        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self._fetch_paged(futu_code, kl_type, autype, start, end)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                wait = 2 ** (attempt - 1)
                logger.warning(
                    "OpenD fetch failed (attempt %d/%d) for %s [%s, %s]: %s; retry in %ds",
                    attempt, MAX_RETRIES, futu_code, start, end, exc, wait,
                )
                time.sleep(wait)
        raise OpenDPullError(
            f"OpenD pull failed after {MAX_RETRIES} attempts for "
            f"{futu_code} {kl_type} [{start}, {end}]: {last_err}"
        )

    def _fetch_paged(
        self,
        futu_code: str,
        kl_type: str,
        autype: str,
        start: str,
        end: str,
    ) -> List[dict]:
        from futu import KLType, AuType, RET_OK  # type: ignore

        kl_enum = getattr(KLType, kl_type)
        autype_enum = {
            "qfq": AuType.QFQ,
            "hfq": AuType.HFQ,
            "none": AuType.NONE,
        }[autype.lower()]

        ctx = self._ensure_quote_ctx()
        all_rows: List[dict] = []
        page_req_key: Optional[str] = None
        while True:
            ret, data, page_req_key = ctx.request_history_kline(
                code=futu_code,
                start=start,
                end=end,
                ktype=kl_enum,
                autype=autype_enum,
                max_count=PAGE_SIZE,
                page_req_key=page_req_key,
            )
            if ret != RET_OK:
                raise RuntimeError(f"request_history_kline returned {ret}: {data}")
            if data is None or len(data) == 0:
                break
            for _, row in data.iterrows():
                all_rows.append(row.to_dict())
            if not page_req_key:
                break
            time.sleep(PAGE_SLEEP_SEC)
        return all_rows


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def _compute_gaps(
    cache_start: str,
    cache_end: str,
    req_start: str,
    req_end: str,
) -> List[Tuple[str, str]]:
    """Return the date-range gaps in ``[req_start, req_end]`` not already in
    ``[cache_start, cache_end]``. Both bounds are inclusive ``YYYY-MM-DD``.
    """
    fmt = "%Y-%m-%d"
    cs = datetime.strptime(cache_start, fmt)
    ce = datetime.strptime(cache_end, fmt)
    rs = datetime.strptime(req_start, fmt)
    re_ = datetime.strptime(req_end, fmt)

    gaps: List[Tuple[str, str]] = []
    # Left gap
    if rs < cs:
        left_end = min(cs - timedelta(days=1), re_)
        if rs <= left_end:
            gaps.append((rs.strftime(fmt), left_end.strftime(fmt)))
    # Right gap
    if re_ > ce:
        right_start = max(ce + timedelta(days=1), rs)
        if right_start <= re_:
            gaps.append((right_start.strftime(fmt), re_.strftime(fmt)))
    return gaps


def _make_bar(
    symbol: str,
    exchange: Exchange,
    interval: Interval,
    row: dict,
) -> BarData:
    """Convert a futu kline row dict to a vnpy ``BarData``."""
    ts = row.get("time_key") or row.get("time")
    if isinstance(ts, str):
        # futu returns "YYYY-MM-DD HH:MM:SS" for intraday and "YYYY-MM-DD" for daily.
        if len(ts) <= 10:
            dt = datetime.strptime(ts, "%Y-%m-%d")
        else:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    else:
        dt = ts  # already a datetime

    return BarData(
        symbol=symbol,
        exchange=exchange,
        datetime=dt,
        interval=interval,
        volume=float(row.get("volume") or 0),
        turnover=float(row.get("turnover") or 0),
        open_price=float(row["open"]),
        high_price=float(row["high"]),
        low_price=float(row["low"]),
        close_price=float(row["close"]),
        gateway_name=GATEWAY_NAME,
    )
