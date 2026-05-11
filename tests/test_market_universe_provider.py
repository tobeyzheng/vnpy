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


# ---------------------------------------------------------------------------
# get_stock_filter (preset-based screening) backend
# ---------------------------------------------------------------------------


class _RecordingFilterCtx:
    """Fake OpenQuoteContext that records get_stock_filter calls."""

    def __init__(self, pages, basicinfo_rows=None, basicinfo_ret_ok=True):
        # ``pages`` is a list of (last_page, all_count, stock_list) tuples.
        self._pages = list(pages)
        self._page_index = 0
        self._basicinfo_rows = basicinfo_rows or []
        self._basicinfo_ret_ok = basicinfo_ret_ok
        self.filter_calls = []
        self.basicinfo_calls = 0
        self.closed = False

    def get_stock_filter(self, *, market, filter_list, begin, num):
        self.filter_calls.append({
            "market": market,
            "begin": begin,
            "num": num,
            "filter_count": len(filter_list),
        })
        if self._page_index >= len(self._pages):
            return 0, (True, 0, [])
        page = self._pages[self._page_index]
        self._page_index += 1
        return 0, page

    def get_stock_basicinfo(self, market, security_type):
        self.basicinfo_calls += 1
        if not self._basicinfo_ret_ok:
            return 1, "boom"
        return 0, _FakeData(self._basicinfo_rows)

    def close(self):
        self.closed = True


def _make_filter_futu(ctx):
    module = types.SimpleNamespace()
    module.RET_OK = 0
    module.OpenQuoteContext = lambda host, port: ctx
    module.SecurityType = types.SimpleNamespace(STOCK="STOCK")

    class _SimpleFilter:
        def __init__(self):
            self.stock_field = None
            self.filter_min = None
            self.filter_max = None
            self.is_no_filter = False
            self.sort = None

    module.SimpleFilter = _SimpleFilter
    module.StockField = types.SimpleNamespace(
        MARKET_VAL="MARKET_VAL",
        CUR_PRICE="CUR_PRICE",
        TURNOVER="TURNOVER",
    )
    module.SortDir = types.SimpleNamespace(DESCEND="DESCEND")
    module.SortField = types.SimpleNamespace()
    return module


class _FilterStubQuoteClient(_StubQuoteClient):
    def __init__(self, ctx):
        super().__init__([], available=True)
        self._futu = _make_filter_futu(ctx)


def test_universe_provider_uses_get_stock_filter_for_large_cap_preset():
    pages = [
        (
            True,
            2,
            [
                {"stock_code": "US.NVDA", "stock_name": "NVIDIA", "MARKET_VAL": 3.2e12, "CUR_PRICE": 911.0, "TURNOVER": 8.5e9},
                {"stock_code": "US.AAPL", "stock_name": "Apple", "MARKET_VAL": 3.0e12, "CUR_PRICE": 220.0, "TURNOVER": 6.0e9},
            ],
        )
    ]
    ctx = _RecordingFilterCtx(pages)
    provider = FutuMarketUniverseProvider(quote_client=_FilterStubQuoteClient(ctx), max_symbols=10)

    symbols = provider.list_symbols("us", preset="large_cap")

    assert [item["symbol"] for item in symbols] == ["NVDA.US", "AAPL.US"]
    assert ctx.basicinfo_calls == 0
    assert ctx.filter_calls and ctx.filter_calls[0]["filter_count"] >= 3
    assert ctx.closed is True


def test_universe_provider_filter_paginates_until_limit_reached():
    page_one = (
        False,
        4,
        [
            {"stock_code": "US.A", "stock_name": "A"},
            {"stock_code": "US.B", "stock_name": "B"},
        ],
    )
    page_two = (
        True,
        4,
        [
            {"stock_code": "US.C", "stock_name": "C"},
            {"stock_code": "US.D", "stock_name": "D"},
        ],
    )
    ctx = _RecordingFilterCtx([page_one, page_two])
    provider = FutuMarketUniverseProvider(quote_client=_FilterStubQuoteClient(ctx), max_symbols=3)

    symbols = provider.list_symbols("us", preset="large_cap", extra_limit=3)

    # Should stop as soon as we have 3 candidates → only page 1 + part of page 2.
    assert [item["symbol"] for item in symbols] == ["A.US", "B.US", "C.US"]
    assert len(ctx.filter_calls) == 2


def test_universe_provider_falls_back_to_basicinfo_when_filter_raises():
    class _BrokenCtx(_RecordingFilterCtx):
        def get_stock_filter(self, **_kwargs):
            raise RuntimeError("filter not supported")

    ctx = _BrokenCtx(
        pages=[],
        basicinfo_rows=[
            {"code": "US.NVDA", "name": "NVIDIA"},
            {"code": "US.AAPL", "name": "Apple"},
        ],
    )
    provider = FutuMarketUniverseProvider(quote_client=_FilterStubQuoteClient(ctx))

    symbols = provider.list_symbols("us", preset="large_cap")

    assert [item["symbol"] for item in symbols] == ["NVDA.US", "AAPL.US"]
    assert ctx.basicinfo_calls == 1


def test_universe_provider_preset_none_uses_basicinfo_directly():
    ctx = _RecordingFilterCtx(
        pages=[],
        basicinfo_rows=[{"code": "US.NVDA", "name": "NVIDIA"}],
    )
    provider = FutuMarketUniverseProvider(quote_client=_FilterStubQuoteClient(ctx))

    symbols = provider.list_symbols("us", preset="none")

    assert [item["symbol"] for item in symbols] == ["NVDA.US"]
    assert ctx.filter_calls == []
    assert ctx.basicinfo_calls == 1
