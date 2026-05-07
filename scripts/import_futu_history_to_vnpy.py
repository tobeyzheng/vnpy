from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData

from services.futu_opend.config import OpenDConfig


def parse_vt_symbol(vt_symbol: str) -> tuple[str, Exchange, str]:
    symbol, exchange = vt_symbol.split('.', 1)
    exchange = exchange.upper()
    if exchange == 'SMART':
        return symbol.upper(), Exchange.SMART, f'US.{symbol.upper()}'
    if exchange == 'SEHK':
        return symbol.upper(), Exchange.SEHK, f'HK.{symbol.upper()}'
    raise ValueError(f'unsupported vt_symbol: {vt_symbol}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()

    import futu  # type: ignore

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    config = OpenDConfig()
    ctx = futu.OpenQuoteContext(host=config.host, port=config.port)
    db = get_database()
    total = 0
    try:
        for vt_symbol in args.symbols:
            symbol, exchange, futu_code = parse_vt_symbol(vt_symbol)
            ret, data, _ = ctx.request_history_kline(
                futu_code,
                start=start.strftime('%Y-%m-%d'),
                end=end.strftime('%Y-%m-%d'),
                ktype=futu.KLType.K_DAY,
                max_count=1000,
            )
            if ret != futu.RET_OK:
                raise RuntimeError(f'{vt_symbol} history fetch failed: {data}')
            rows = data.to_dict('records') if hasattr(data, 'to_dict') else []
            bars: list[BarData] = []
            for row in rows:
                dt = datetime.fromisoformat(str(row['time_key']))
                bars.append(
                    BarData(
                        symbol=symbol,
                        exchange=exchange,
                        datetime=dt,
                        interval=Interval.DAILY,
                        volume=float(row.get('volume', 0) or 0),
                        turnover=float(row.get('turnover', 0) or 0),
                        open_interest=0,
                        open_price=float(row.get('open', 0) or 0),
                        high_price=float(row.get('high', 0) or 0),
                        low_price=float(row.get('low', 0) or 0),
                        close_price=float(row.get('close', 0) or 0),
                        gateway_name='FUTU',
                    )
                )
            if not bars:
                print(f'{vt_symbol}: no rows')
                continue
            ok = db.save_bar_data(bars, stream=False)
            print(f'{vt_symbol}: imported {len(bars)} bars, save_ok={ok}')
            total += len(bars)
        print(f'total_imported={total}')
    finally:
        ctx.close()


if __name__ == '__main__':
    main()
