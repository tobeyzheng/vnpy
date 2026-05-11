from __future__ import annotations

from typing import Any, List

from services.futu_account.quote_client import FutuQuoteClient
from services.futu_opend import OpenDConfig

from .symbols import normalize_symbol

DEFAULT_UNIVERSE_LIMIT = 800

MARKET_FUTU_CODES = {
    "hong_kong": "HK",
    "us": "US",
}


class UniverseUnavailableError(RuntimeError):
    """Raised when the upstream universe provider cannot be used."""


class FutuMarketUniverseProvider:
    """Lists tradable equity symbols for a target market via the Futu quote SDK.

    The provider talks to the local OpenD service through the official ``futu``
    SDK and returns a normalized symbol list that can be fed into the candidate
    preparation pipeline (score-first / fallback path).
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

    def list_symbols(self, market: str) -> List[dict[str, Any]]:
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

        ctx = futu.OpenQuoteContext(host=self.config.host, port=self.config.port)
        try:
            ret, data = ctx.get_stock_basicinfo(futu_market, futu.SecurityType.STOCK)
            if ret != futu.RET_OK:
                raise UniverseUnavailableError(f"get_stock_basicinfo failed: {data}")
            rows = data.to_dict(orient="records") if hasattr(data, "to_dict") else list(data or [])
        finally:
            try:
                ctx.close()
            except Exception:
                pass

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
            if len(normalized) >= self.max_symbols:
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
