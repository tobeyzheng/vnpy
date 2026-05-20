#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地回测运行脚本 - 使用 vnpy_ctastrategy BacktestingEngine + strategy_adapter

Usage:
    python3 tmp/run_local_backtest.py \\
        --strategy tmp/strategy/us_strategy_simple_multifactor2.py \\
        --symbol NVDA.SMART \\
        --interval 1d \\
        --start 2023-01-01 \\
        --end 2024-12-31 \\
        --capital 100000

数据需先下载到本地vnpy数据库（如通过 tmp/data_downloader.py）。
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TMP_DIR = Path(__file__).resolve().parent
for p in (_PROJECT_ROOT, _TMP_DIR):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData
from vnpy_ctastrategy.backtesting import BacktestingEngine

from strategy_adapter import make_cta_class  # type: ignore


# ---------------------------------------------------------------------------
# Interval mapping
# ---------------------------------------------------------------------------
INTERVAL_MAP: Dict[str, Dict[str, Any]] = {
    "5m":  {"base_interval": Interval.MINUTE, "window": 1, "annual_days": 240},
    "10m": {"base_interval": Interval.MINUTE, "window": 2, "annual_days": 240},
    "30m": {"base_interval": Interval.MINUTE, "window": 6, "annual_days": 240},
    "1h":  {"base_interval": Interval.HOUR,   "window": 1, "annual_days": 240},
    "4h":  {"base_interval": Interval.HOUR,   "window": 4, "annual_days": 240},
    "1d":  {"base_interval": Interval.DAILY,  "window": 1, "annual_days": 240},
}


def resample_bars(bars: List[BarData], window: int) -> List[BarData]:
    """Aggregate *window* consecutive bars into one (same as run_optimize.py)."""
    if window <= 1 or not bars:
        return list(bars)

    out: List[BarData] = []
    bucket: List[BarData] = []
    for b in bars:
        bucket.append(b)
        if len(bucket) == window:
            agg = BarData(
                symbol=bucket[0].symbol,
                exchange=bucket[0].exchange,
                datetime=bucket[0].datetime,
                interval=bucket[0].interval,
                gateway_name=bucket[0].gateway_name,
                open_price=bucket[0].open_price,
                high_price=max(x.high_price for x in bucket),
                low_price=min(x.low_price for x in bucket),
                close_price=bucket[-1].close_price,
                volume=sum(x.volume for x in bucket),
                turnover=sum(getattr(x, "turnover", 0.0) or 0.0 for x in bucket),
                open_interest=bucket[-1].open_interest,
            )
            out.append(agg)
            bucket = []
    return out


def load_bars_from_db(
    symbol: str, exchange: Exchange, interval: Interval,
    start: datetime, end: datetime,
) -> List[BarData]:
    db = get_database()
    bars = db.load_bar_data(symbol, exchange, interval, start, end)
    return list(bars)

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Local backtest runner for Futu DSL strategies (vnpy CTA engine)."
    )
    p.add_argument(
        "--strategy", required=True,
        help="Path to the Futu DSL strategy file "
             "(e.g. tmp/strategy/us_strategy_simple_multifactor2.py)",
    )
    p.add_argument(
        "--symbol", required=True,
        help="vt_symbol format, e.g. NVDA.SMART, 000300.SSE",
    )
    p.add_argument(
        "--interval", default="1d",
        choices=list(INTERVAL_MAP.keys()),
        help="Bar interval (default: 1d)",
    )
    p.add_argument("--start", default="2023-01-01", help="Backtest start date")
    p.add_argument("--end", default="2024-12-31", help="Backtest end date")
    p.add_argument("--capital", type=float, default=100_000.0, help="Initial capital")
    p.add_argument("--rate", type=float, default=0.0003, help="Commission rate")
    p.add_argument("--slippage", type=float, default=0.0, help="Slippage")
    p.add_argument("--size", type=float, default=1.0, help="Contract multiplier")
    p.add_argument("--pricetick", type=float, default=0.01, help="Minimum price tick")
    p.add_argument(
        "--setting", default=None,
        help="JSON string of strategy parameter overrides, "
             "e.g. '{\"fast_window\": 10, \"rsi_oversold\": 25.0}'",
    )
    p.add_argument("--output", default=None, help="Output file path for results JSON")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument(
        "--no-report", action="store_true",
        help="Skip generating HTML report and result directory",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Result directory & export helpers
# ---------------------------------------------------------------------------
def create_result_dir(
    strategy_name: str, symbol: str, interval: str,
    start: str, end: str,
) -> Path:
    """Create a timestamped result directory under tmp/data/backtest_results/."""
    base = _TMP_DIR / "data" / "backtest_results"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = strategy_name.replace(".py", "").replace(" ", "_")
    safe_symbol = symbol.replace(".", "_")
    dir_name = f"{safe_name}_{safe_symbol}_{interval}_{ts}"
    result_dir = base / dir_name
    result_dir.mkdir(parents=True, exist_ok=True)
    return result_dir


def serialize_value(v: Any) -> Any:
    """Convert non-serializable types for JSON output."""
    import numpy as np
    import pandas as pd
    from datetime import date
    if isinstance(v, (date,)):
        return v.isoformat()
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return v


def export_config(
    result_dir: Path, args: argparse.Namespace,
    setting: Dict[str, Any], iconf: Dict[str, Any],
) -> None:
    """Export full configuration parameters."""
    config = {
        "strategy": str(args.strategy),
        "symbol": args.symbol,
        "interval": args.interval,
        "start": args.start,
        "end": args.end,
        "capital": args.capital,
        "rate": args.rate,
        "slippage": args.slippage,
        "size": args.size,
        "pricetick": args.pricetick,
        "setting": setting,
        "interval_config": {
            "base_interval": str(iconf["base_interval"]),
            "window": iconf["window"],
            "annual_days": iconf["annual_days"],
        },
    }
    path = result_dir / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def export_trades(result_dir: Path, engine: BacktestingEngine) -> None:
    """Export trade details (buy/sell records)."""
    trades = engine.get_all_trades()
    rows = []
    for t in trades:
        rows.append({
            "tradeid": t.vt_tradeid,
            "symbol": t.vt_symbol,
            "direction": t.direction.value,
            "offset": t.offset.value,
            "price": round(float(t.price), 4),
            "volume": float(t.volume),
            "datetime": t.datetime.isoformat() if t.datetime else "",
            "gateway_name": t.gateway_name,
        })
    path = result_dir / "trades.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def export_daily_results(result_dir: Path, engine: BacktestingEngine) -> None:
    """Export daily PnL results."""
    daily_df = getattr(engine, "daily_df", None)
    if daily_df is None or daily_df.empty:
        path = result_dir / "daily_results.json"
        path.write_text("[]", encoding="utf-8")
        return

    # Skip columns that contain non-serializable objects (e.g. 'trades' with TradeData)
    skip_cols = {"trades"}
    records = []
    for idx, row in daily_df.iterrows():
        rec = {}
        rec["date"] = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
        for col in row.index:
            if col in skip_cols:
                continue
            rec[col] = serialize_value(row[col])
        records.append(rec)

    path = result_dir / "daily_results.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def export_statistics(result_dir: Path, stats: Dict[str, Any]) -> None:
    """Export statistics summary."""
    out = {}
    for k, v in stats.items():
        out[k] = serialize_value(v)
    path = result_dir / "statistics.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML report generator (inline, no external dependencies)
# ---------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest Report - __TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
.container { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin-bottom: 8px; color: #f1f5f9; }
.subtitle { color: #94a3b8; font-size: 0.9rem; margin-bottom: 24px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
.card { background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }
.card-label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
.card-value { font-size: 1.5rem; font-weight: 700; }
.card-value.positive { color: #34d399; }
.card-value.negative { color: #f87171; }
.card-value.neutral { color: #60a5fa; }
.section { background: #1e293b; border-radius: 12px; padding: 24px; border: 1px solid #334155; margin-bottom: 24px; }
.section-title { font-size: 1rem; font-weight: 600; margin-bottom: 16px; color: #f1f5f9; }
.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.chart-box { position: relative; height: 320px; }
.chart-box.full { grid-column: 1 / -1; height: 400px; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #334155; }
th { background: #0f172a; color: #94a3b8; font-weight: 500; position: sticky; top: 0; }
tr:hover { background: #334155; }
.buy { color: #34d399; }
.sell { color: #f87171; }
.config-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
.config-item { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #1e293b; }
.config-key { color: #94a3b8; }
.config-val { color: #f1f5f9; font-family: monospace; }
.table-wrap { max-height: 480px; overflow-y: auto; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
.tag-buy { background: #064e3b; color: #6ee7b7; }
.tag-sell { background: #7f1d1d; color: #fca5a5; }
@media (max-width: 768px) { .chart-row { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="container">
  <h1>{title}</h1>
<p class="subtitle">Generated at __GENERATED_AT__</p>

  <div class="grid" id="statsCards"></div>

  <div class="section">
    <div class="section-title">Equity Curve & Drawdown</div>
    <div class="chart-row">
      <div class="chart-box full"><canvas id="equityChart"></canvas></div>
    </div>
    <div class="chart-row">
      <div class="chart-box full"><canvas id="drawdownChart"></canvas></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Daily PnL Distribution</div>
    <div class="chart-row">
      <div class="chart-box"><canvas id="pnlBarChart"></canvas></div>
      <div class="chart-box"><canvas id="pnlHistChart"></canvas></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Trade Details</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Datetime</th><th>Side</th><th>Offset</th><th>Price</th><th>Volume</th></tr></thead>
        <tbody id="tradesBody"></tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Configuration</div>
    <div class="config-grid" id="configGrid"></div>
  </div>
</div>

<script>
const STATS = __STATS_JSON__;
const TRADES = __TRADES_JSON__;
const DAILY = __DAILY_JSON__;
const CONFIG = __CONFIG_JSON__;

// --- Stats cards ---
function renderCards() {
  const cards = [
    {label: 'Total Return', value: STATS.total_return?.toFixed(2) + '%', cls: STATS.total_return >= 0 ? 'positive' : 'negative'},
    {label: 'Annual Return', value: STATS.annual_return?.toFixed(2) + '%', cls: STATS.annual_return >= 0 ? 'positive' : 'negative'},
    {label: 'Max Drawdown', value: STATS.max_ddpercent?.toFixed(2) + '%', cls: 'negative'},
    {label: 'Sharpe Ratio', value: STATS.sharpe_ratio?.toFixed(2), cls: 'neutral'},
    {label: 'End Balance', value: Number(STATS.end_balance).toLocaleString('en', {minimumFractionDigits:2}), cls: 'neutral'},
    {label: 'Total Trades', value: STATS.total_trade_count, cls: 'neutral'},
    {label: 'Return/DD Ratio', value: STATS.return_drawdown_ratio?.toFixed(2), cls: STATS.return_drawdown_ratio >= 0 ? 'positive' : 'negative'},
    {label: 'Profit Days / Loss Days', value: (STATS.profit_days||0) + ' / ' + (STATS.loss_days||0), cls: 'neutral'},
  ];
  const el = document.getElementById('statsCards');
  cards.forEach(c => {
    el.innerHTML += `<div class="card"><div class="card-label">${c.label}</div><div class="card-value ${c.cls}">${c.value}</div></div>`;
  });
}

// --- Charts ---
function makeChart(id, type, data, options) {
  const ctx = document.getElementById(id).getContext('2d');
  new Chart(ctx, {type, data, options});
}

function renderCharts() {
  const dates = DAILY.map(d => d.date);
  const balance = DAILY.map(d => d.balance);
  const drawdown = DAILY.map(d => d.ddpercent || (d.drawdown / d.balance * 100));
  const netPnl = DAILY.map(d => d.net_pnl);

  makeChart('equityChart', 'line', {
    labels: dates,
    datasets: [{label: 'Balance', data: balance, borderColor: '#60a5fa', backgroundColor: 'rgba(96,165,250,0.1)', fill: true, pointRadius: 0, tension: 0.3}]
  }, {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}, scales: {x: {display: true, ticks: {maxTicksLimit: 10, color: '#64748b'}, y: {ticks: {color: '#64748b'}});

  makeChart('drawdownChart', 'line', {
    labels: dates,
    datasets: [{label: 'Drawdown %', data: drawdown, borderColor: '#f87171', backgroundColor: 'rgba(248,113,113,0.15)', fill: true, pointRadius: 0, tension: 0.3}]
  }, {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}, scales: {x: {display: true, ticks: {maxTicksLimit: 10, color: '#64748b'}, y: {ticks: {color: '#64748b'}});

  makeChart('pnlBarChart', 'bar', {
    labels: dates,
    datasets: [{label: 'Daily PnL', data: netPnl, backgroundColor: netPnl.map(v => v >= 0 ? '#34d399' : '#f87171'), pointRadius: 0}]
  }, {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}, scales: {x: {display: true, ticks: {maxTicksLimit: 10, color: '#64748b'}, y: {ticks: {color: '#64748b'}});

  // Histogram
  const bins = 40;
  const min = Math.min(...netPnl), max = Math.max(...netPnl);
  const step = (max - min) / bins || 1;
  const histLabels = [], histData = [];
  for (let i = 0; i < bins; i++) {
    const lo = min + step * i;
    histLabels.push(lo.toFixed(0));
    histData.push(0);
  }
  netPnl.forEach(v => {
    let idx = Math.floor((v - min) / step);
    if (idx >= bins) idx = bins - 1;
    if (idx < 0) idx = 0;
    histData[idx]++;
  });
  makeChart('pnlHistChart', 'bar', {
    labels: histLabels,
    datasets: [{label: 'Days', data: histData, backgroundColor: histLabels.map((_, i) => (min + step * i) >= 0 ? '#34d399' : '#f87171')}]
  }, {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}, scales: {x: {display: true, ticks: {maxTicksLimit: 8, color: '#64748b'}, y: {ticks: {color: '#64748b'}});
}

// --- Trades table ---
function renderTrades() {
  const tbody = document.getElementById('tradesBody');
  TRADES.forEach((t, i) => {
    const isBuy = t.direction === 'LONG' || t.direction === '多';
    const cls = isBuy ? 'buy' : 'sell';
    const tagCls = isBuy ? 'tag-buy' : 'tag-sell';
    const label = isBuy ? 'BUY' : 'SELL';
    tbody.innerHTML += `<tr><td>${i+1}</td><td>${t.datetime}</td><td><span class="tag ${tagCls}">${label}</span></td><td>${t.offset}</td><td class="${cls}">${t.price}</td><td>${t.volume}</td></tr>`;
  });
}

// --- Config ---
function renderConfig() {
  const el = document.getElementById('configGrid');
  function flat(obj, prefix) {
    const items = [];
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? prefix + '.' + k : k;
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        items.push(...flat(v, key));
      } else {
        items.push([key, v]);
      }
    }
    return items;
  }
  flat(CONFIG, '').forEach(([k, v]) => {
    el.innerHTML += `<div class="config-item"><span class="config-key">${k}</span><span class="config-val">${v}</span></div>`;
  });
}

renderCards();
renderCharts();
renderTrades();
renderConfig();
</script>
</body>
</html>"""


def generate_html_report(
    result_dir: Path,
    stats: Dict[str, Any],
    trades_data: List[Dict],
    daily_data: List[Dict],
    config_data: Dict[str, Any],
    title: str,
) -> Path:
    """Generate an inline HTML report with embedded Chart.js."""
    stats_json = json.dumps(stats, default=serialize_value, ensure_ascii=False)
    trades_json = json.dumps(trades_data, default=serialize_value, ensure_ascii=False)
    daily_json = json.dumps(daily_data, default=serialize_value, ensure_ascii=False)
    config_json = json.dumps(config_data, default=serialize_value, ensure_ascii=False)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = HTML_TEMPLATE.replace("__STATS_JSON__", stats_json)
    html = html.replace("__TRADES_JSON__", trades_json)
    html = html.replace("__DAILY_JSON__", daily_json)
    html = html.replace("__CONFIG_JSON__", config_json)
    html = html.replace("__TITLE__", title)
    html = html.replace("__GENERATED_AT__", generated_at)

    path = result_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # ---- Resolve strategy path ----
    strategy_path = Path(args.strategy)
    if not strategy_path.is_absolute():
        strategy_path = _PROJECT_ROOT / strategy_path
    if not strategy_path.exists():
        print(f"❌ Strategy file not found: {strategy_path}")
        return 2

    # ---- Parse symbol / exchange ----
    parts = args.symbol.split(".")
    if len(parts) != 2:
        print(
            f"❌ Invalid symbol format '{args.symbol}', "
            f"expected SYMBOL.EXCHANGE (e.g. NVDA.SMART)"
        )
        return 2
    symbol, exchange_str = parts
    try:
        exchange = Exchange(exchange_str)
    except ValueError:
        print(f"❌ Unsupported exchange: {exchange_str}")
        return 2

    # ---- Parse dates ----
    start_dt = datetime.fromisoformat(args.start)
    end_dt = datetime.fromisoformat(args.end).replace(hour=23, minute=59, second=59)

    # ---- Interval config ----
    iconf = INTERVAL_MAP[args.interval]
    base_interval: Interval = iconf["base_interval"]
    annual_days: int = iconf["annual_days"]
    resample_window: int = iconf["window"]

    # ---- Load bars from local DB ----
    print(
        f"📥 Loading data: {args.symbol} | interval={args.interval} | "
        f"{args.start} → {args.end}"
    )
    # Load with extra range for warmup / resampling alignment
    load_start = start_dt - timedelta(days=60)
    bars = load_bars_from_db(symbol, exchange, base_interval, load_start, end_dt)
    print(f"📊 Loaded {len(bars)} base bars from database")

    if not bars:
        print(
            f"❌ No data found for {args.symbol}. "
            f"Please download data first, e.g.: "
            f"python3 tmp/data_downloader.py --symbol {symbol} --exchange {exchange_str}"
        )
        return 1

    # ---- Resample ----
    bars = resample_bars(bars, resample_window)
    print(f"📊 After resampling (window={resample_window}): {len(bars)} bars")

    # ---- Build CTA class via adapter ----
    print(f"🔧 Building CTA strategy from: {strategy_path.name}")
    cta_cls = make_cta_class(
        strategy_path=str(strategy_path),
        runtime_interval=base_interval,
        class_name=f"FutuDsl_{strategy_path.stem}_{args.interval}",
        initial_capital=args.capital,
    )

    # ---- Parse setting overrides ----
    setting: Dict[str, Any] = {}
    if args.setting:
        try:
            setting = json.loads(args.setting)
        except json.JSONDecodeError as e:
            print(f"❌ Invalid --setting JSON: {e}")
            return 2

    # Ensure LIVE_SUBMIT is True for the strategy's place_limit() to fire
    # (harmless in backtest - CTA engine routes internally, never to a broker).
    setting.setdefault("LIVE_SUBMIT", True)

    # ---- Run backtest ----
    print(f"🚀 Running backtest: {args.symbol} | capital={args.capital:,.0f}")
    engine = BacktestingEngine()

    # Handle timezone awareness (vnpy stores bars tz-aware)
    tz = bars[0].datetime.tzinfo if bars and bars[0].datetime.tzinfo else None
    _start = start_dt
    _end = end_dt
    if tz is not None:
        if _start.tzinfo is None:
            _start = _start.replace(tzinfo=tz)
        if _end.tzinfo is None:
            _end = _end.replace(tzinfo=tz)

    engine.set_parameters(
        vt_symbol=args.symbol,
        interval=base_interval,
        start=_start,
        end=_end,
        rate=args.rate,
        slippage=args.slippage,
        size=args.size,
        pricetick=args.pricetick,
        capital=int(args.capital),
        annual_days=annual_days,
    )
    engine.add_strategy(cta_cls, setting)

    # Filter bars to the exact backtest window
    end_eod = _end.replace(hour=23, minute=59, second=59)
    filtered = [b for b in bars if _start <= b.datetime <= end_eod]
    engine.history_data = filtered
    print(f"📊 Filtered bars in backtest window: {len(filtered)}")

    if not filtered:
        print("❌ No bars in the specified date range")
        return 1

    engine.run_backtesting()
    engine.calculate_result()
    stats = engine.calculate_statistics(output=False)

    if not isinstance(stats, dict):
        stats = {}

    # ---- Display results ----
    # NOTE: vnpy BacktestingEngine.calculate_statistics returns:
    #   total_return / annual_return / max_ddpercent  — already *100 (absolute %)
    #   max_drawdown / total_net_pnl / daily_net_pnl  — absolute currency values
    #   end_balance / capital                          — absolute currency values
    #   sharpe_ratio                                   — ratio
    #   total_trade_count                              — integer
    # So we use :.2f for percentage fields (not :.2% which would *100 again).
    print("\n" + "=" * 60)
    print("📊 回测结果")
    print("=" * 60)
    print(f"  标的:          {args.symbol}")
    print(f"  策略:          {strategy_path.name}")
    print(f"  周期:          {args.interval}")
    print(f"  区间:          {args.start} → {args.end}")
    print(f"  初始资金:      {stats.get('capital', args.capital):>12,.2f}")
    print(f"  最终净值:      {stats.get('end_balance', 0):>12,.2f}")
    print(f"  总收益率:      {stats.get('total_return', 0):>11.2f}%")
    print(f"  年化收益率:    {stats.get('annual_return', 0):>11.2f}%")
    print(f"  最大回撤:      {stats.get('max_drawdown', 0):>11,.2f}")
    print(f"  最大回撤(%):   {stats.get('max_ddpercent', 0):>11.2f}%")
    print(f"  夏普比率:      {stats.get('sharpe_ratio', 0):>11.2f}")
    print(f"  总交易次数:    {int(stats.get('total_trade_count', 0)):>12}")
    print(f"  日均盈亏:      {stats.get('daily_net_pnl', 0):>12,.2f}")
    print("=" * 60)

    # ---- Export results to directory ----
    if not args.no_report:
        result_dir = create_result_dir(
            strategy_path.name, args.symbol, args.interval,
            args.start, args.end,
        )
        print(f"\n📁 Saving results to: {result_dir}")

        # Export each component
        export_config(result_dir, args, setting, iconf)
        export_trades(result_dir, engine)
        export_daily_results(result_dir, engine)
        export_statistics(result_dir, stats)

        # Build data for HTML report
        trades_data = []
        for t in engine.get_all_trades():
            trades_data.append({
                "tradeid": t.vt_tradeid,
                "symbol": t.vt_symbol,
                "direction": t.direction.value,
                "offset": t.offset.value,
                "price": round(float(t.price), 4),
                "volume": float(t.volume),
                "datetime": t.datetime.isoformat() if t.datetime else "",
            })

        daily_data = []
        daily_df = getattr(engine, "daily_df", None)
        if daily_df is not None and not daily_df.empty:
            skip_cols = {"trades"}
            for idx, row in daily_df.iterrows():
                rec = {"date": idx.isoformat() if hasattr(idx, "isoformat") else str(idx)}
                for col in row.index:
                    if col in skip_cols:
                        continue
                    rec[col] = serialize_value(row[col])
                daily_data.append(rec)

        config_data = {
            "strategy": str(args.strategy),
            "symbol": args.symbol,
            "interval": args.interval,
            "start": args.start,
            "end": args.end,
            "capital": args.capital,
            "rate": args.rate,
            "slippage": args.slippage,
            "size": args.size,
            "pricetick": args.pricetick,
            "setting": setting,
            "interval_config": {
                "base_interval": str(iconf["base_interval"]),
                "window": iconf["window"],
                "annual_days": iconf["annual_days"],
            },
        }

        stats_serializable = {k: serialize_value(v) for k, v in stats.items()}

        report_path = generate_html_report(
            result_dir, stats_serializable, trades_data,
            daily_data, config_data,
            title=f"{strategy_path.stem} | {args.symbol} | {args.interval}",
        )
        print(f"📄 HTML report: {report_path}")

    # ---- Save results to custom output path if requested ----
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = _PROJECT_ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        result = {
            "strategy": str(strategy_path),
            "symbol": args.symbol,
            "interval": args.interval,
            "start": args.start,
            "end": args.end,
            "capital": args.capital,
            "setting": setting,
            "statistics": {k: str(v) for k, v in stats.items()} if stats else {},
        }
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"💾 Results saved to: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())