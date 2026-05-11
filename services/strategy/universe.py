from __future__ import annotations

from typing import Any, List, Mapping

from services.futu_account.quote_client import FutuQuoteClient
from services.futu_opend import OpenDConfig

from .symbols import normalize_symbol

DEFAULT_UNIVERSE_LIMIT = 200
# OpenD's get_stock_filter caps a single page at 200 rows.
FILTER_PAGE_SIZE = 200

MARKET_FUTU_CODES = {
    "hong_kong": "HK",
    "us": "US",
}

SUPPORTED_UNIVERSE_PRESETS = ("large_cap", "momentum_cta", "none")
DEFAULT_UNIVERSE_PRESET = "large_cap"

# Per-market thresholds for the ``large_cap`` preset.
# Numbers are expressed in the local currency of each market.
_LARGE_CAP_THRESHOLDS: dict[str, dict[str, float]] = {
    "hong_kong": {
        "min_market_cap": 5_000_000_000.0,   # 50 亿 HKD
        "min_price": 1.0,
        "max_price": 1_000.0,
        "min_turnover": 50_000_000.0,        # 5000 万 HKD
    },
    "us": {
        "min_market_cap": 500_000_000.0,     # 5 亿 USD
        "min_price": 5.0,
        "max_price": 10_000.0,
        "min_turnover": 10_000_000.0,        # 1000 万 USD
    },
}


class UniverseUnavailableError(RuntimeError):
    """Raised when the upstream universe provider cannot be used."""


class FutuMarketUniverseProvider:
    """Lists tradable equity symbols for a target market via the Futu quote SDK.

    Two backends are supported:

    1. ``get_stock_filter`` based screening (default): applies a preset of
       liquidity / market-cap / price filters and sorts by market cap to
       pull a high-quality short list (Top-N by paged requests).
    2. ``get_stock_basicinfo`` legacy listing: dictionary-ordered full
       universe; used when ``preset='none'`` or when the screener call
       fails irrecoverably.
    """

    def __init__(
        self,
        config: OpenDConfig | None = None,
        *,
        quote_client: FutuQuoteClient | None = None,
        max_symbols: int = DEFAULT_UNIVERSE_LIMIT,
    ) -> None:
        self.config = config or OpenDConfig()
        self.quote_client = quote_client or FutuQuoteClient(self.config)
        self.max_symbols = max(1, int(max_symbols))

    def list_symbols(
        self,
        market: str,
        *,
        preset: str | None = None,
        extra_limit: int | None = None,
    ) -> List[dict[str, Any]]:
        market_key = (market or "").strip().lower()
        futu_market = MARKET_FUTU_CODES.get(market_key)
        if not futu_market:
            raise ValueError(f"Unsupported market for universe listing: {market}")

        ok, message = self.quote_client.availability()
        if not ok:
            raise UniverseUnavailableError(message or "Futu SDK is unavailable")

        futu = self.quote_client._futu  # type: ignore[attr-defined]
        if futu is None:
            raise UniverseUnavailableError("Futu SDK is not loaded")

        preset_key = (preset or DEFAULT_UNIVERSE_PRESET).strip().lower()
        if preset_key not in SUPPORTED_UNIVERSE_PRESETS:
            preset_key = DEFAULT_UNIVERSE_PRESET

        target_limit = max(1, int(extra_limit or self.max_symbols))

        ctx = futu.OpenQuoteContext(host=self.config.host, port=self.config.port)
        try:
            if preset_key == "none":
                return self._list_via_basicinfo(
                    futu=futu,
                    ctx=ctx,
                    market_key=market_key,
                    futu_market=futu_market,
                    limit=target_limit,
                )
            try:
                rows = self._list_via_filter(
                    futu=futu,
                    ctx=ctx,
                    market_key=market_key,
                    futu_market=futu_market,
                    preset=preset_key,
                    limit=target_limit,
                )
            except Exception:
                # Soft failure (SDK shape mismatch, preset fields missing,
                # mid-page transport hiccup, etc.): degrade to basicinfo so
                # the downstream pipeline still has something to score on.
                rows = self._list_via_basicinfo(
                    futu=futu,
                    ctx=ctx,
                    market_key=market_key,
                    futu_market=futu_market,
                    limit=target_limit,
                )
            return rows
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Backend: get_stock_filter (preset-based screening)
    # ------------------------------------------------------------------
    def _list_via_filter(
        self,
        *,
        futu: Any,
        ctx: Any,
        market_key: str,
        futu_market: str,
        preset: str,
        limit: int,
    ) -> List[dict[str, Any]]:
        get_stock_filter = getattr(ctx, "get_stock_filter", None)
        if not callable(get_stock_filter):
            raise UniverseUnavailableError("get_stock_filter is not supported by this Futu SDK")

        filter_list = self._build_filter_list(futu=futu, market_key=market_key, preset=preset)
        if not filter_list:
            raise UniverseUnavailableError(f"No filter list could be built for preset={preset}")

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        page_size = min(FILTER_PAGE_SIZE, max(1, int(limit)))
        begin = 0
        # Hard guard: never iterate more than 10 pages even if OpenD keeps
        # reporting last_page=False, to bound RPC count.
        for _page in range(10):
            try:
                ret, data = get_stock_filter(
                    market=futu_market,
                    filter_list=filter_list,
                    begin=begin,
                    num=page_size,
                )
            except TypeError:
                # Older SDKs may use positional args only.
                ret, data = get_stock_filter(futu_market, filter_list, begin, page_size)
            if ret != futu.RET_OK:
                if begin == 0:
                    raise UniverseUnavailableError(f"get_stock_filter failed: {data}")
                # Mid-pagination failure: stop and return what we have.
                break
            last_page = True
            stock_list: Any = data
            if isinstance(data, tuple) and len(data) >= 3:
                last_page, _all_count, stock_list = data[0], data[1], data[2]
            elif isinstance(data, Mapping):
                last_page = bool(data.get("last_page", True))
                stock_list = data.get("stock_list") or data.get("data") or []
            page_rows = list(stock_list or [])
            for entry in page_rows:
                row = self._coerce_filter_entry(entry, market_key=market_key)
                if not row:
                    continue
                symbol = row["symbol"]
                if symbol in seen:
                    continue
                seen.add(symbol)
                normalized.append(row)
                if len(normalized) >= limit:
                    return normalized
            if not page_rows or last_page:
                break
            begin += page_size
        return normalized

    def _build_filter_list(self, *, futu: Any, market_key: str, preset: str) -> list[Any]:
        """Build the SDK-specific filter list for the requested preset.

        We only access SDK symbols that exist; missing fields silently fall
        through, allowing this code to keep working across SDK minor
        versions. Callers must catch ``UniverseUnavailableError`` if the
        resulting list is empty.
        """
        SimpleFilter = getattr(futu, "SimpleFilter", None)
        StockField = getattr(futu, "StockField", None)
        SortDir = getattr(futu, "SortDir", None)
        SortField = getattr(futu, "SortField", None)
        if SimpleFilter is None or StockField is None:
            return []

        thresholds = _LARGE_CAP_THRESHOLDS.get(market_key, {})
        filter_list: list[Any] = []

        def _try_simple(field_name: str, *, low: float | None = None, high: float | None = None, sort: bool = False) -> None:
            field = getattr(StockField, field_name, None)
            if field is None:
                return
            try:
                f = SimpleFilter()
            except Exception:
                return
            try:
                f.stock_field = field
                if low is not None:
                    f.filter_min = float(low)
                if high is not None:
                    f.filter_max = float(high)
                if sort:
                    f.is_no_filter = True
                    direction = getattr(SortDir, "DESCEND", None) if SortDir is not None else None
                    if direction is not None:
                        f.sort = direction
                else:
                    f.is_no_filter = False
            except Exception:
                return
            filter_list.append(f)

        # Common base filters (large_cap + momentum_cta share these baselines).
        if preset in {"large_cap", "momentum_cta"}:
            min_cap = thresholds.get("min_market_cap")
            min_price = thresholds.get("min_price")
            max_price = thresholds.get("max_price")
            min_turnover = thresholds.get("min_turnover")
            if min_cap is not None:
                _try_simple("MARKET_VAL", low=min_cap)
            if min_price is not None or max_price is not None:
                _try_simple("CUR_PRICE", low=min_price, high=max_price)
            if min_turnover is not None:
                _try_simple("TURNOVER", low=min_turnover)
            # Sort by market cap descending so the first page is the most
            # liquid / largest names.
            _try_simple("MARKET_VAL", sort=True)

        if preset == "momentum_cta":
            # Best-effort technical overlay — only the SDK-provided fields
            # that exist will be applied; missing ones are skipped silently.
            _try_simple("CHANGE_RATE_5DAY", low=0.0)
            _try_simple("VOLUME_RATIO", low=1.0)

        return filter_list

    def _coerce_filter_entry(self, entry: Any, *, market_key: str) -> dict[str, Any] | None:
        # SDK returns a list of StockQuote-like objects; we accept both
        # objects with attribute access and dict-like rows for testability.
        def _attr(name: str) -> Any:
            if isinstance(entry, Mapping):
                return entry.get(name)
            return getattr(entry, name, None)

        raw_code = str(_attr("stock_code") or _attr("code") or "").strip()
        if not raw_code:
            return None
        symbol = self._to_dot_market_symbol(raw_code, market_key)
        if not symbol or "." not in symbol:
            return None
        return {
            "symbol": symbol,
            "market": market_key,
            "name": str(_attr("stock_name") or _attr("name") or symbol),
            "lot_size": _safe_int(_attr("lot_size")),
            "stock_type": str(_attr("stock_type") or ""),
            "market_cap": _safe_float(_attr("MARKET_VAL") or _attr("market_val")),
            "last_price": _safe_float(_attr("CUR_PRICE") or _attr("cur_price")),
            "turnover": _safe_float(_attr("TURNOVER") or _attr("turnover")),
        }

    # ------------------------------------------------------------------
    # Backend: get_stock_basicinfo (legacy / fallback)
    # ------------------------------------------------------------------
    def _list_via_basicinfo(
        self,
        *,
        futu: Any,
        ctx: Any,
        market_key: str,
        futu_market: str,
        limit: int,
    ) -> List[dict[str, Any]]:
        ret, data = ctx.get_stock_basicinfo(futu_market, futu.SecurityType.STOCK)
        if ret != futu.RET_OK:
            raise UniverseUnavailableError(f"get_stock_basicinfo failed: {data}")
        rows = data.to_dict(orient="records") if hasattr(data, "to_dict") else list(data or [])

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_code = str(row.get("code") or "").strip()
            if not raw_code:
                continue
            symbol = self._to_dot_market_symbol(raw_code, market_key)
            if not symbol or "." not in symbol:
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            normalized.append(
                {
                    "symbol": symbol,
                    "market": market_key,
                    "name": str(row.get("name") or symbol),
                    "lot_size": _safe_int(row.get("lot_size")),
                    "stock_type": str(row.get("stock_type") or ""),
                }
            )
            if len(normalized) >= limit:
                break
        return normalized

    @staticmethod
    def _to_dot_market_symbol(raw_code: str, market_key: str) -> str:
        text = raw_code.upper().strip()
        if not text:
            return ""
        if "." in text:
            left, right = text.split(".", 1)
            if left in {"HK", "US", "SH", "SZ"}:
                # Futu format is e.g. "HK.00700"; flip to "00700.HK".
                return normalize_symbol(f"{right}.{left}", market_key)
            return normalize_symbol(text, market_key)
        # Bare ticker, attach market suffix.
        suffix = MARKET_FUTU_CODES.get(market_key, "").upper()
        if not suffix:
            return ""
        return normalize_symbol(f"{text}.{suffix}", market_key)


def _safe_int(value: Any) -> int | None:
    try:
        if value in {None, ""}:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
