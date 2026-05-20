---
plan: us_multi_symbol_quant_phase2_v2
parent_plan: us_multi_symbol_quant_phase2
created: 2026-05-20
owner: us-quant
---

# 需求 — 多标量化阶段 ② v2（迁移版 + 真实回测通道 + 池周更）

## 1. 背景

阶段 ② 已完成 dry-run 骨架（commit `19a9302e`），但有 3 个落地缺口：

1. 现有 `phase2/strategy/us_multi_symbol_phase2_strategy.py` 含跨文件
   `from phase2.strategy.pool_loader / portfolio_risk import ...`，**违反
   "迁移到 Futu 平台必须单文件、零本地依赖"** 的硬约束；
2. `pool_config.yaml` 是手工维护，缺少**周度池更新流水线**；
3. dry-run runner 不撮合，没有打通**真实回测**的"本地影子轨 + Futu 主轨"
   双通道与对账流程。

## 2. 目标

- 在不动现有 `us_multi_symbol_phase2_strategy.py` 的前提下，新增一个
  **零外部 import、单文件**的 futumd 兼容策略：
  `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`。
- 新增一条**周度池更新流水线**：`phase2/runners/run_pool_update.py`，
  默认 `--dry-run`，`--apply --confirm` 才落盘；写盘前有 3 道安全闸。
- 新增**真实回测 runbook**：`docs/research/us_multi_symbol_quant/06_real_backtest_runbook.md`，
  指明本地影子轨命令 + Futu 平台手工流程 + 对账命令。

## 3. 硬约束（逐条写明，便于实施时核对）

### 3.1 迁移版策略
- 只能用 stdlib（`math` / `typing` / `os` / `sys` 等无副作用）+ 平台符号。
- **不允许** `from phase2.* import ...` / `from services.* import ...`。
- **不允许** 模块级新函数：所有指标 / 风控 / 预算逻辑必须收到 `Strategy`
  类内的 `_xxx` 私有方法（与 NVDA `_sma`/`_rsi`/`_volume_ratio` 同形态）。
- 接口对齐：`initialize` / `trigger_symbols` / `custom_indicator` /
  `global_variables` / `handle_data` 五件套不增删；`_enter_position` /
  `_exit_position` 与 NVDA 同名。
- 数据 API：必须用 `bar_close(symbol=..., bar_type=BarType.K_DAY,
  select=k, session_type=THType.RTH)`（NVDA 同款签名）。
- 池上限：`declare_trig_symbol()` 调用次数 ≤ 20（项目硬上限）。
- `LIVE_SUBMIT` 默认 `False`；阶段 ② v2 不允许翻 `True`。
- 状态：全部存在 `self._state[symbol]` 字典里；不能依赖任何磁盘 IO。

### 3.2 池更新脚本
- 默认 `--dry-run`（仅打印 diff）。
- `--apply` 必须搭配 `--confirm` 才会真写盘。
- 数据来源：仅从 `tmp/data/` 已落盘 K 线 + 本地 `earnings_calendar.json`，
  **不联网行情**（与项目规则 2 一致：联网执行需用户单独确认）。
- 3 道安全闸（任一触发即非零退出）：
  - 单次 add/remove 比例 > 30% → exit 5
  - 池规模 > `max_pool_size` → exit 2
  - sector 集中度 > 40% → exit 6
- 写盘后追加 `pool_update_log.jsonl` 审计日志（复用
  `pool_loader.append_pool_change_log` / 自带 jsonl 记录）。

### 3.3 真实回测通道
- 本地影子轨：复用现有 `services/backtest`（不改动），命令产物 JSON 落
  `state/runs/us_multi_symbol_quant_phase2_v2/real_local_<id>/local_expected.json`。
- Futu 主轨：手工在 Futu 客户端执行；脚本只产可上传文件 + 手工录入产物
  JSON；阶段 ② v2 仓库分支严禁自动连 Futu。
- 对账复用 `phase2/runners/run_phase2_reconcile.py`（已实现 5 项 / >20%
  退出码），不新增脚本。

## 4. 验收

- `python3 -m py_compile phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py` 通过。
- `python3 phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py --check` 退出 0。
- `python3 -m pytest phase2/strategy/tests/test_futumd_strategy.py phase2/strategy/tests/test_pool_updater.py -v` 全部通过。
- AST 扫描验证迁移版策略 0 个外部 `from phase2.*` / `from services.*` import。
- `python3 phase2/runners/run_pool_update.py --dry-run` 退出 0 且打印 diff。
- runbook `06_real_backtest_runbook.md` 列出本地影子轨命令 + Futu 手工流程 + 对账命令。

## 5. 边界

- 不动现有 `us_multi_symbol_phase2_strategy.py`（保 39 个测试绿）。
- 不动现有 39 个 phase2 单元测试，只新增。
- 不动 `services/backtest` 任何代码。
- 仍然不允许 REAL 提交；SIM 升级仍需独立 phase3 plan。

