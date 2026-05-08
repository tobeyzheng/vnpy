from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.classic_multifactor.account import ClassicSimOnceFlow
from scripts.classic_multifactor.backtest import ClassicSingleSymbolBacktester
from scripts.classic_multifactor.data import VnpyBarRepository, parse_us_symbol
from scripts.classic_multifactor.minute_guard import MinuteTradeGuard, MinuteTradeGuardConfig
from scripts.classic_multifactor.model import ClassicMultiFactorConfig, ClassicMultiFactorModel

from scripts.classic_multifactor.risk import ClassicOrderRiskManager

REPO_ROOT = Path(__file__).resolve().parents[2]


class UsSingleSymbolClassicFlow:
    def __init__(self, args):
        self.args = args
        self.symbol, self.vt_symbol, self.futu_code = parse_us_symbol(args.symbol)
        self.config = ClassicMultiFactorConfig(
            fast_window=int(args.fast_window),
            slow_window=int(args.slow_window),
            momentum_window=int(args.momentum_window),
            atr_window=int(args.atr_window),
            entry_score=float(args.entry_score),
            exit_score=float(args.exit_score),
            stop_loss_pct=float(args.stop_loss_pct),
            take_profit_pct=float(args.take_profit_pct),
            trailing_stop_pct=float(args.trailing_stop_pct),
            max_position_pct=float(args.max_position_pct),
            max_order_value=float(args.max_order_value),
            commission_rate=float(args.commission_rate),
            slippage_bps=float(args.slippage_bps),
            signal_interval_minutes=int(args.signal_interval_minutes),
            confirm_bars=int(args.confirm_bars),
            min_volume_ratio=float(args.min_volume_ratio),
            min_atr_pct=float(args.min_atr_pct),
            min_trend_score=float(args.min_trend_score),

            stop_atr=float(args.stop_atr),
            take_profit_atr=float(args.take_profit_atr),
            trailing_atr=float(args.trailing_atr),
        )
        self.model = ClassicMultiFactorModel(self.config)
        self.minute_guard = MinuteTradeGuard(
            MinuteTradeGuardConfig(
                max_intraday_trades=int(args.max_intraday_trades),
                entry_cooldown_minutes=int(args.entry_cooldown_minutes),
                min_hold_minutes=int(args.min_hold_minutes),
                no_new_entry_after=str(args.no_new_entry_after),
            )
        )

        self.risk_manager = ClassicOrderRiskManager(self.config, market="us")
        self.repository = VnpyBarRepository(fetch_futu_history=bool(args.fetch_futu_history))
        self.runs_dir = REPO_ROOT / "state" / "runs" / "classic_multifactor"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> dict[str, Any]:
        if self.args.mode == "sim-once":
            return self.run_sim_once()
        return self.run_backtest()

    def run_backtest(self) -> dict[str, Any]:
        start = datetime.fromisoformat(self.args.start)
        end = datetime.fromisoformat(self.args.end)
        load_start = start - timedelta(days=self.config.warmup_window * 3)
        _vt_symbol, _futu_code, bars = self.repository.load_us_bars(self.args.symbol, load_start, end, self.args.interval)

        bars = [bar for bar in bars if self._naive(bar.datetime) <= end]

        engine = ClassicSingleSymbolBacktester(model=self.model, risk_manager=self.risk_manager, capital=float(self.args.capital), minute_guard=self.minute_guard)

        result = engine.run(self.vt_symbol, bars)
        report = {
            "mode": "backtest",
            "strategy": "classic_multifactor_no_llm_vnpy_template_v2",
            "symbol": self.symbol,
            "vt_symbol": self.vt_symbol,
            "futu_code": self.futu_code,
            "start": self.args.start,
            "end": self.args.end,
            "params": {**vars(self.args), "model_config": asdict(self.config)},
            **result,
        }
        return self.write_report(report, "us_single_symbol_multifactor_backtest.json")

    def run_sim_once(self) -> dict[str, Any]:
        end = datetime.now()
        start = end - timedelta(days=self.config.warmup_window * 4)
        _vt_symbol, _futu_code, bars = self.repository.load_us_bars(self.args.symbol, start, end, self.args.interval)

        flow = ClassicSimOnceFlow(symbol=self.symbol, vt_symbol=self.vt_symbol, model=self.model, risk_manager=self.risk_manager, submit_sim=bool(self.args.submit_sim))
        report = {
            "mode": "sim-once",
            "strategy": "classic_multifactor_no_llm_vnpy_template_v2",
            "symbol": self.symbol,
            "vt_symbol": self.vt_symbol,
            "futu_code": self.futu_code,
            "params": {**vars(self.args), "model_config": asdict(self.config)},
            **flow.run(bars),
        }
        return self.write_report(report, "us_single_symbol_multifactor_sim_once.json")

    def _naive(self, value: datetime) -> datetime:
        return value.replace(tzinfo=None)

    def write_report(self, report: dict[str, Any], filename: str) -> dict[str, Any]:

        path = Path(self.args.output) if self.args.output else self.runs_dir / filename
        if not path.is_absolute():
            path = REPO_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {"report_path": str(path), **report}
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(path)
        print(json.dumps({k: report.get(k) for k in ["status", "mode", "strategy", "symbol", "vt_symbol", "stats", "decision"] if k in report}, ensure_ascii=False, indent=2, default=str))
        return report
