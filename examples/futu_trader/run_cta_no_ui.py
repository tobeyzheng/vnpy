from __future__ import annotations

import os
import sys
from pathlib import Path
from time import sleep

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT.joinpath(".vntrader").mkdir(exist_ok=True)
PROJECT_ROOT.joinpath("strategies").mkdir(exist_ok=True)
os.chdir(PROJECT_ROOT)

from vnpy.event import Event, EventEngine
from vnpy.trader.engine import LogEngine, MainEngine
from vnpy.trader.logger import INFO, logger
from vnpy.trader.setting import SETTINGS
from vnpy_ctastrategy import CtaEngine, CtaStrategyApp
from vnpy_ctastrategy.base import EVENT_CTA_LOG
from vnpy_futu import FutuGateway

SETTINGS["log.active"] = True
SETTINGS["log.level"] = INFO
SETTINGS["log.console"] = True
SETTINGS["log.file"] = True

GATEWAY_NAME = "FUTU"
STRATEGY_CLASS = "SoxlCtaStrategy"
STRATEGY_NAME = "soxl_cta"
VT_SYMBOL = "SOXL.SMART"

FUTU_SETTING = {
    "密码": "",
    "地址": "127.0.0.1",
    "端口": 11111,
    "市场": "US",
    "环境": "模拟",
}

STRATEGY_SETTING = {
    "fast_window": 10,
    "slow_window": 120,
    "atr_window": 10,
    "risk_pct": 0.03,
    "max_pos_pct": 0.5,
    "stop_atr": 1.5,
    "trail_atr": 2.5,
    "max_drawdown_pct": 0.15,
    "capital": 10_000,
    "init_days": 360,
    "min_volume": 1,
    "price_add": 0.001,
    "rebalance_seconds": 1800,
    "trade_in_session_only": True,
}


def print_cta_log(event: Event) -> None:
    log = event.data
    logger.info(f"{log.gateway_name}: {log.msg}")


def main() -> None:
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(FutuGateway)
    cta_engine: CtaEngine = main_engine.add_app(CtaStrategyApp)  # type: ignore

    log_engine: LogEngine = main_engine.get_engine("log")  # type: ignore
    event_engine.register(EVENT_CTA_LOG, log_engine.process_log_event)
    event_engine.register(EVENT_CTA_LOG, print_cta_log)

    logger.info("连接FUTU模拟交易接口")
    main_engine.connect(FUTU_SETTING, GATEWAY_NAME)
    sleep(15)

    cta_engine.init_engine()
    logger.info("CTA策略引擎初始化完成")

    if STRATEGY_CLASS not in cta_engine.classes:
        logger.error(f"找不到策略类：{STRATEGY_CLASS}，请检查 strategies/soxl_cta_strategy.py")
        main_engine.close()
        sys.exit(1)

    if STRATEGY_NAME not in cta_engine.strategies:
        cta_engine.add_strategy(STRATEGY_CLASS, STRATEGY_NAME, VT_SYMBOL, STRATEGY_SETTING)
        logger.info(f"创建策略：{STRATEGY_NAME} {VT_SYMBOL}")

    future = cta_engine.init_strategy(STRATEGY_NAME)
    future.result(timeout=180)
    logger.info(f"策略初始化完成：{STRATEGY_NAME}")

    cta_engine.start_strategy(STRATEGY_NAME)
    logger.info(f"策略已启动：{STRATEGY_NAME}")

    try:
        while True:
            sleep(10)
    except KeyboardInterrupt:
        logger.info("收到退出信号，停止策略并关闭主引擎")
        cta_engine.stop_strategy(STRATEGY_NAME)
        main_engine.close()


if __name__ == "__main__":
    main()
