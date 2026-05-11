from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from services.strategy.universe import (
    FutuMarketUniverseProvider,
    UniverseUnavailableError,
)


class _FakeData:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, orient="records"):
        return list(self._rows)


class _FakeQuoteContext:
    def __init__(self, rows, *, ret_ok=True, error="failed"):
        self._rows = rows
        self._ret_ok = ret_ok
        self._error = error
        self.closed = False

    def get_stock_basicinfo(self, market, security_type):
        if not self._ret_ok:
            return 1, self._error
        return 0, _FakeData(self._rows)

    def close(self):
        self.closed = True


def _make_fake_futu_module(rows, *, ret_ok=True):
    module = types.SimpleNamespace()
    module.RET_OK = 0
    module.OpenQuoteContext = lambda host, port: _FakeQuoteContext(rows, ret_ok=ret_ok)
    module.SecurityType = types.SimpleNamespace(STOCK="STOCK")
    return module


class _StubQuoteClient:
    def __init__(self, rows, *, available=True, message="ok", ret_ok=True):
        self._available = available
        self._message = message
        self._futu = _make_fake_futu_module(rows, ret_ok=ret_ok) if available else None

    def availability(self):
        if self._available:
            return True, self._message
        return False, self._message


def test_universe_provider_lists_us_symbols_with_prefix_codes():
    rows = [
        {"code": "US.NVDA", "name": "NVIDIA", "lot_size": 1, "stock_type": "STOCK"},
        {"code": "US.AAPL", "name": "Apple", "lot_size": 1, "stock_type": "STOCK"},
    ]
    provider = FutuMarketUniverseProvider(quote_client=_StubQuoteClient(rows))
    symbols = provider.list_symbols("us")
    assert [item["symbol"] for item in symbols] == ["NVDA.US", "AAPL.US"]
    assert all(item["market"] == "us" for item in symbols)


def test_universe_provider_lists_hk_symbols_and_normalizes_codes():
    rows = [
        {"code": "HK.00700", "name": "Tencent"},
        {"code": "HK.09988", "name": "Alibaba"},
        {"code": "HK.00700", "name": "Duplicate"},
    ]
    provider = FutuMarketUniverseProvider(quote_client=_StubQuoteClient(rows))
    symbols = provider.list_symbols("hong_kong")
    assert [item["symbol"] for item in symbols] == ["00700.HK", "09988.HK"]


def test_universe_provider_respects_max_symbols_cap():
    rows = [{"code": f"US.SYM{i:03d}", "name": f"Sym {i}"} for i in range(20)]
    provider = FutuMarketUniverseProvider(
        quote_client=_StubQuoteClient(rows), max_symbols=5,
    )
    symbols = provider.list_symbols("us")
    assert len(symbols) == 5


def test_universe_provider_raises_when_sdk_unavailable():
    provider = FutuMarketUniverseProvider(
        quote_client=_StubQuoteClient([], available=False, message="sdk missing"),
    )
    with pytest.raises(UniverseUnavailableError):
        provider.list_symbols("us")


def test_universe_provider_raises_for_unsupported_market():
    provider = FutuMarketUniverseProvider(quote_client=_StubQuoteClient([]))
    with pytest.raises(ValueError):
        provider.list_symbols("cn")


def test_universe_provider_raises_when_basicinfo_returns_error():
    class _ErrCtx:
        def __init__(self):
            self.closed = False

        def get_stock_basicinfo(self, *_args, **_kwargs):
            return 1, "boom"

        def close(self):
            self.closed = True

    class _ErrFutu(types.SimpleNamespace):
        pass

    err_module = _ErrFutu()
    err_module.RET_OK = 0
    err_module.OpenQuoteContext = lambda host, port: _ErrCtx()
    err_module.SecurityType = types.SimpleNamespace(STOCK="STOCK")

    class _Client(_StubQuoteClient):
        def __init__(self):
            super().__init__([], available=True)
            self._futu = err_module

    provider = FutuMarketUniverseProvider(quote_client=_Client())
    with pytest.raises(UniverseUnavailableError):
        provider.list_symbols("us")
