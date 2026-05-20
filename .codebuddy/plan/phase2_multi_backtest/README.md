# Phase-② 多标本地回测 — 计划

## 背景

Phase-② futumd 兼容策略文件（`phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`）需要在迁移到 Futu 平台之前，先在本地完成真实多标日线回测，验证：

1. **按 symbol 分桶**：策略中所有 `bar_close(symbol=, ...)` / `position_holding_qty(symbol=)` 等 DSL 调用按标的隔离，不串桶。
2. **组合层撮合**：12 标共享一个 USD 现金账户；每根 K 线生成的下单意图按"次日开盘"撮合，避免未来函数。
3. **futumd 策略源码零改动**：上传到 Futu 量化平台时无需修改一行代码。

## 边界

- 全部新代码落在 `phase2/backtest/`、`phase2/runners/run_phase2_multi_backtest.py`、`phase2/strategy/tests/`。
- **不动 `tmp/`**（与历史 sandbox 解耦，便于将来整体搬走）。
- **不连 OpenD / Futu / 任何远端服务**；仅读本地 vnpy SQLite 数据库。
- **不开 LIVE_SUBMIT**；策略硬开关保持 False。

## 任务拆解

参见 `.codebuddy/task_list/phase2_multi_backtest.md`（权威进度）。

## 关键设计

### `phase2/backtest/futumd_strategy_adapter.py`

- `PortfolioRuntime`：按 symbol 分桶的 OHLCV / positions / entry_costs / pending / alerts 容器；`cash_value` 是组合层单变量。
- `build_futumd_namespace(runtime)`：返回一个 dict，把 `bar_close` / `bar_open` / `bar_high` / `bar_low` / `bar_volume` / `cash` / `net_asset` / `position_holding_qty` / `place_limit` / `close_positions` / `alert` / 各 enum / `StrategyBase` / `declare_*` / `show_variable` 等所有平台符号绑定到 runtime。
- `load_futumd_strategy(strategy_path, runtime)`：用 `importlib.util.spec_from_file_location` 加载策略文件；**先把命名空间注入到模块 globals，再 exec**——这样策略文件顶部的 `try: bar_close ... except NameError` 看到名称已存在，就跳过 stub 区，全部 DSL 都路由到 runtime。
- `settle_pending(...)`：按"下一根 K 线"的 ref_price 撮合 BUY / SELL_CLOSE，处理现金不足回扣、卖单 qty clamp、滑点、手续费。

### `phase2/backtest/portfolio_backtest_engine.py`

- `PortfolioBacktestEngine`：从 vnpy 本地数据库拉 12 标日线，按交易日 union 排序驱动；每一天：写入桶 → `strategy.handle_data()` → 用 `all_dates[i+1]` 的开盘价撮合 → mark-to-close 写入 equity 曲线。
- 输出 4 个产物到 `state/runs/phase2_multi_backtest/<run_id>/`：
  - `equity_curve.csv` — 每日 cash / position_value / nav
  - `positions_daily.csv` — 每日各 symbol 持仓（宽表）
  - `trade_ledger.csv` — 全部成交记录（带费用、bar_index、bar_datetime）
  - `summary.json` — 总收益、年化、最大回撤、交易笔数、胜负数

### `phase2/runners/run_phase2_multi_backtest.py`

CLI：`--start --end --init-cash --pool-config --strategy-path --rate --slippage --exchange --interval --run-id --output-root`，默认池来自 `phase2/strategy/config/pool_config.yaml`。

## 验收

- T7 单测 17 项：分桶取数、cash 不串户、place_limit 路由、settle_pending 撮合规则全部通过。
- T8 单测 6 项：mock 数据库 + 微型 2 标策略，断言次日开盘成交、trade ledger 含两 symbol、报告字段齐备。
- T11 回归：`python3 -m pytest phase2/strategy/tests/` 73 项全绿。
- T12（独立确认动作）：12 标 1 年真回测 smoke。
