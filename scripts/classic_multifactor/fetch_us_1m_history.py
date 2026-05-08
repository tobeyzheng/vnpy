from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.futu_opend.config import OpenDConfig
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData

from scripts.classic_multifactor.data import parse_us_symbol, safe_float


def to_vnpy_interval(interval: str) -> Interval:
    text = interval.lower()
    if text in {"1m", "minute", "min"}:
        return Interval.MINUTE
    if text in {"1d", "d", "daily", "day"}:
        return Interval.DAILY
    raise ValueError(f"unsupported interval: {interval}")


def to_futu_ktype(futu, interval: str):
    text = interval.lower()
    if text in {"1m", "minute", "min"}:
        return futu.KLType.K_1M
    if text in {"1d", "d", "daily", "day"}:
        return futu.KLType.K_DAY
    raise ValueError(f"unsupported futu interval: {interval}")


def fetch_and_save(
    symbol: str,
    start: datetime,
    end: datetime,
    interval: str,
    *,
    batch_sleep: float = 0.3,
) -> dict:
    try:
        import futu  # type: ignore
    except Exception as exc:
        return {"status": "error", "reason": f"futu import failed: {exc}"}

    _us_symbol, vt_symbol, futu_code = parse_us_symbol(symbol)
    code, exchange_name = vt_symbol.split(".", 1)
    exchange = Exchange(exchange_name)
    vnpy_interval = to_vnpy_interval(interval)

    config = OpenDConfig()
    print(f"[opend] connecting {config.host}:{config.port}")
    ctx = futu.OpenQuoteContext(host=config.host, port=config.port)
    all_bars: list[BarData] = []
    pages = 0
    try:
        page_req_key = None
        ktype = to_futu_ktype(futu, interval)
        while True:
            pages += 1
            ret, data, page_req_key = ctx.request_history_kline(
                futu_code,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                ktype=ktype,
                max_count=1000,
                page_req_key=page_req_key,
            )
            if ret != futu.RET_OK:
                return {
                    "status": "error",
                    "reason": f"request_history_kline failed: {data}",
                    "pages": pages,
                }
            rows = data.to_dict("records") if hasattr(data, "to_dict") else []
            print(f"[page ] #{pages} rows={len(rows)} has_next={bool(page_req_key)}")
            for row in rows:
                all_bars.append(
                    BarData(
                        symbol=code,
                        exchange=exchange,
                        datetime=datetime.fromisoformat(str(row["time_key"])),
                        interval=vnpy_interval,
                        volume=safe_float(row.get("volume")),
                        turnover=safe_float(row.get("turnover")),
                        open_interest=0,
                        open_price=safe_float(row.get("open")),
                        high_price=safe_float(row.get("high")),
                        low_price=safe_float(row.get("low")),
                        close_price=safe_float(row.get("close")),
                        gateway_name="FUTU",
                    )
                )
            if not page_req_key:
                break
            time.sleep(batch_sleep)
    finally:
        ctx.close()

    all_bars.sort(key=lambda bar: bar.datetime)
    if not all_bars:
        return {"status": "empty", "pages": pages}

    db = get_database()
    db.save_bar_data(all_bars, stream=False)

    db_bars = db.load_bar_data(code, exchange, vnpy_interval, start, end)
    return {
        "status": "ok",
        "vt_symbol": vt_symbol,
        "interval": interval,
        "pages": pages,
        "fetched_count": len(all_bars),
        "db_count_after": len(db_bars),
        "first_dt": all_bars[0].datetime.isoformat() if all_bars else None,
        "last_dt": all_bars[-1].datetime.isoformat() if all_bars else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch US 1m history via Futu OpenD and write to vn.py DB.")
    parser.add_argument("--symbol", default="NVDA.US")
    parser.add_argument("--start", default="2026-04-08")
    parser.add_argument("--end", default="2026-05-07")
    parser.add_argument("--interval", default="1m")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    print(f"[task ] symbol={args.symbol} interval={args.interval} {start.date()}~{end.date()}")
    result = fetch_and_save(args.symbol, start, end, args.interval)
    print(f"[done ] {result}")


if __name__ == "__main__":
    main()
