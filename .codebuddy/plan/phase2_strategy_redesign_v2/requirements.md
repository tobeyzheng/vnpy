# Phase-② 策略重构 v2 — 趋势跟随版（Donchian + Chandelier）

## 背景

阶段 ② v1 (`us_multi_symbol_phase2_strategy_futumd.py`) 在 2020-05~2025-05
真实 5 年回测中只拿到 ~37% 总收益（约 6.5% 年化），远逊于固定池 12 只
mega-cap B&H 的 +300% 以上。诊断（见 docs/project_operation_log.md
2026-05-23 条目）定位 6 大缺陷：

1. 入场需 5 条 AND 同时成立（trend / RSI / vol_ratio / ATR-cap /
   concurrent），相关性互相否定，强趋势区被 ATR 闸门排除。
2. 固定 take-profit + trailing-drawdown 在大牛趋势中过早平仓
   （Kaminski-Lo 2014 已证否）。
3. 预算公式 `nav × pool_budget × position_pct / max_concurrent`
   双重缩放，单笔实际只用 ~1.6% NAV，造成 80% 现金长期闲置。
4. 无市场 regime 滤网，熊市仍尝试入场。
5. 无 vol-targeting，高低波股共享同一名义金额。
6. RSI / vol_ratio 摆动指标用作硬入场闸门，而非 score 加分项。

## 目标

新建 v2 文件 `us_multi_symbol_phase2_strategy_futumd_v2.py`，按
业界主流趋势跟随范式重写入场/出场逻辑，**保留 v1 不动以做 A/B**；
跑同区间同池子真实回测，目标超过 v1 best 的 37%，逼近 100%+。

## 范围

### 改

- **新增**：`phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2.py`
- **同步文档**：
  - `.codebuddy/plan/phase2_strategy_redesign_v2/{requirements,task-item}.md`
  - `.codebuddy/task_list/phase2_strategy_redesign_v2.md`
  - `docs/system_integration_guide.md`（新增 v2 章节）
  - `docs/adaptive_quant_engine_design.md`（新增 v2 设计要点）
  - `docs/project_operation_log.md`（追加 2026-05-23 条目）

### 不改

- `us_multi_symbol_phase2_strategy_futumd.py` 保留原样
- `us_multi_symbol_phase2_strategy.py` 保留原样
- `pool_config.yaml` 保留原样
- 回测引擎 / adapter / runner 保留原样（v2 通过 `--strategy-path`
  注入即可）
- `phase2/optimize/` 保留原样（兼容性 shim 已加，旧 search_space 仍可
  跑 v1）

## v2 入场/出场设计

### 入场（3 条 AND）
1. Regime 滤网：`Close > SMA(200)`
2. 长期价格滤网：`Close > SMA(100)`
3. Donchian 突破：`Close >= prior 55 日最高`

### 出场（2 优先级链）
1. Chandelier 止损：`stop = max_since_entry - 3.0 × ATR(22)`
2. 趋势离场：`Close < SMA(50)`

固定 TP / trailing-dd / fast-slow 死叉 / ATR-cap 全部删除。

### 仓位
- per_symbol_budget = `NAV × pool_budget_pct(0.95) / max_concurrent(6)`
- vol-targeting 缩放：`scale = target_vol(0.15) / annualised_atr_pct`，
  夹持 [0.4, 1.5]
- 单标的单仓位（无加仓）

## 硬约束（与 v1 一致，不放宽）

- 单文件、零本地 import、stdlib only
- Strategy 类公共生命周期方法名与 v1 一致
- LIVE_SUBMIT=False ships，不带破坏性默认值
- declare_trig_symbol 数量 ≤ 20
- 无磁盘 IO，状态仅 in-memory

## 回测计划

- 入口：`phase2/runners/run_phase2_multi_backtest.py`
- 池：`phase2/strategy/config/pool_config.yaml`（12 只）
- 区间：5 年（与 v1 baseline 对齐）
- 初始资金：100000 美元
- 手续费：0.0003（0.03%）
- 滑点：0.0
- 三档对照：
  1. v1 default
  2. v1 best（如可复用历史 best 参数）
  3. v2 default
- 产物：`state/runs/phase2_strategy_redesign_v2/<ts>/`

## 验收

- v2 文件 py_compile / `--check` 通过
- adapter 加载冒烟通过
- 现有 phase2 测试 119 项全绿（兼容性 shim 验证）
- 5 年区间回测产物完整：trades.csv / nav.csv / REPORT.md
- v2 总收益高于 v1 default
