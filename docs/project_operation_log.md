## 项目操作记录

### 记录规则

- 记录对框架、需求、入口、规则、运行产物结构、阶段边界有明显影响的改动。
- 每条记录尽量包含日期、变更范围、涉及文件和影响摘要。
- 记录保持高层摘要，避免粘贴冗长实现细节。
- 记录中避免出现真实账户金额、完整账号、密钥、令牌等敏感信息。

### 历史记录

## 2026-05-24 — docs 根入口收敛到 phase2 / 数据下载 / Knot

- **变更范围**：重写 `docs/index.rst` 的根入口，并物理删除 `docs/` 下与当前项目协作主线无关的上游文档目录/文件，仅保留 `phase2`、数据下载、`Knot` 相关文档。
- **关键文件**：`docs/index.rst`、`docs/data_download_guide.md`、`docs/project_operation_log.md`，以及删除的 `docs/community/`、`docs/elite/`、`docs/research/` 等无关文档。
- **影响摘要**：后续从 `file:///projects/vnpy/docs` 进入时，默认只保留当前仓库主线文档入口，避免其他入口继续干扰协作上下文。

## 2026-05-16
- **变更范围**: 策略逻辑调整
- **关键文件**: `/projects/vnpy/tmp/strategy/us_strategy_simple_multifactor2.py`
- **影响摘要**: 调整了精简多因子策略的开仓条件为 '(RSI超卖 + 放量) OR (金叉 + 放量)'，并引入了 `used_slices` 全局变量以支持分5份建仓的资金管理逻辑，同时修复了加权平均成本价的计算问题。

## 2026-05-16
- **变更范围**: 策略指标计算修复
- **关键文件**: `/projects/vnpy/tmp/strategy/us_strategy_simple_multifactor2.py`
- **影响摘要**: 修复了 `_rsi` 函数，将其改为标准的 Wilder 平滑算法以对齐标准行情软件；修复了 `_volume_ratio` 函数，在计算平均量时排除了当前 K 线，以提高对突发放量的敏感度。

## 2026-05-16
- **变更范围**: 策略补仓逻辑调整
- **关键文件**: `/projects/vnpy/tmp/strategy/us_strategy_simple_multifactor2.py`
- **影响摘要**: 增加了补仓策略的限制条件，引入了最小加仓间隔参数（`min_add_interval`，默认10根K线）和最小加仓亏损比例参数（`min_add_loss_pct`，默认2%）。在已有持仓的情况下，只有满足这两个条件才会触发加仓，避免了频繁加仓和在未达到足够跌幅时加仓。

## 2026-05-16
- **变更范围**: 策略卖出逻辑调整
- **关键文件**: `/projects/vnpy/tmp/strategy/us_strategy_simple_multifactor2.py`
- **影响摘要**: 修改了卖出策略，引入了移动止盈机制。增加了 `take_profit_pct`（移动止盈激活阈值，默认10%）和 `trailing_drawdown_pct`（移动止盈回撤比例，默认5%）参数，并记录持仓期间的最高价 `highest_price`。当收益率大于激活阈值，且当前价格从最高价回撤超过设定比例时，触发卖出平仓。

## 2026-05-16
- **变更范围**: 策略移动止盈逻辑修正
- **关键文件**: `/projects/vnpy/tmp/strategy/us_strategy_simple_multifactor2.py`
- **影响摘要**: 修正了移动止盈的计算逻辑。1. 激活条件由"当前收益率"改为"最高收益率"，防止价格回落导致止盈条件失效；2. 回撤比例的计算基准由"最高价格"改为"最高收益率"，即 `(最高收益率 - 当前收益率) / 最高收益率`。

## 2026-05-20
- **变更范围**: 阶段② 美股多标的量化策略骨架（`us_multi_symbol_quant_phase2`，dry-run only）
- **关键文件**:
  - `phase2/strategy/us_multi_symbol_phase2_strategy.py`（4 因子 + 入场 5 项 + 出场链 + N 日冷却，`LIVE_SUBMIT=False` 硬开关）
  - `phase2/strategy/pool_loader.py` + `phase2/strategy/config/pool_config.yaml`（schema 校验 + 4 类过滤）
  - `phase2/strategy/portfolio_risk.py`（单标 4 条 + 组合 5 条；熔断状态 JSON 落盘可重启恢复）
  - `phase2/runners/run_phase2_backtest.py` / `phase2/runners/run_phase2_reconcile.py` / `phase2/runners/run_futu_perf_baseline.py`（默认 `--dry-run`）
  - `phase2/strategy/tests/`（39 unit tests，全部通过）
  - `docs/research/us_multi_symbol_quant/04_platform_performance_baseline.md` / `05_sim_gate_checklist.md`
  - `docs/system_integration_guide.md` 阶段② 入口章节
- **影响摘要**: 新增阶段② 多标的量化骨架与配套 dry-run / 对账 / 性能基线 runner；不连接 OpenD、不下任何 SIM/REAL 单。从 dry-run 升级到 SIM 必须新开独立 plan 并按 `05_sim_gate_checklist.md` 5 项打勾。

## 2026-05-20 — 阶段② v2：futumd 单文件迁移版策略 + 池周更流水线 + 真实回测 runbook

- **变更范围**: 阶段② v2（plan `us_multi_symbol_quant_phase2_v2`）落地 3 条通道，仍严格 dry-run 不连 OpenD：
  - 新增 futumd 兼容**单文件零依赖**多标策略 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`（严格对齐 NVDA 接口；AST 测试强制无 `from phase2.* / from services.*` import）。
  - 新增池周更 runner `phase2/runners/run_pool_update.py`（默认 `--dry-run`；`--apply` 必须配 `--confirm`；3 道安全闸：变更 > 30% / sector > 40% / 池超限）。
  - 新增候选大池 `phase2/strategy/config/pool_universe.yaml` + 离线行情快照 `pool_metrics_snapshot.json`。
  - 新增双轨真实回测 runbook `docs/research/us_multi_symbol_quant/06_real_backtest_runbook.md`。
  - 新增 11 个单元测试（接口契约 6 + 池更新 5），phase2 测试集 39 → 50 全过。
  - 同步：`docs/system_integration_guide.md` 新增"阶段② v2"章节；`.codebuddy/plan/us_multi_symbol_quant_phase2_v2/`；`.codebuddy/task_list/us_multi_symbol_quant_phase2_v2.md`。
- **影响摘要**: 新增 futumd 平台投放路径、池周度更新通道与真实回测双轨流程；不动现有 `us_multi_symbol_phase2_strategy.py` 与 39 个旧测试。仍然不允许翻 `LIVE_SUBMIT=True`；SIM 升级需要新开 phase3 plan。

## 2026-05-21 — 阶段② live：多标美股天级别 live 交易（phase2/live/）

- **变更范围**: 阶段② live（plan `phase2_live_trading`）落地 `dry_run / futu_sim / futu_real` 三态天级别 live 交易能力，零侵入 phase2 回测；仍硬保 dry_run 默认值，需要 6 开关全部置位 + CLI `--futu-env` + CLI `--live-submit` 才能离开 dry 模式。
- **关键文件**:
  - `phase2/live/order_state.py`（OrderIntent/OrderState 复用 services；新增 `build_request_id` / `transition` / `OrderStateStoreExt`）
  - `phase2/live/risk.py`（从 `scripts/classic_multifactor/risk.py` 物理 copy 后改 phase2 命名空间，去掉 classic_multifactor 依赖；幂等性交给 IdempotencyGate）
  - `phase2/live/safety.py`（6 开关：VNPY_LIVE_CONFIG / VNPY_LIVE_SUBMIT / VNPY_LIVE_APPROVED / FUTU_TRADE_PASSWORD / `--futu-env` / `--live-submit`）
  - `phase2/live/broker.py` + `phase2/live/futu_broker.py`（LiveBroker 协议 + `OpenSecTradeContext` + `OpenQuoteContext` 实现）
  - `phase2/live/live_adapter.py`（与 `phase2/backtest/futumd_strategy_adapter.py` 同接口的 live runtime）
  - `phase2/live/guards.py`（4 级 pre-trade gate：幂等 → 对账 → 单标风控 → 组合风控；events.jsonl + 状态机推进）
  - `phase2/live/runner.py`（`DailyLiveRebalanceRunner`，clock/sleep 全注入；REAL 强制 auto_cancel_on_eod；RunnerConfig 新增 `strategy_live_submit_override` 选项供 dry_run smoke 走通全链路）
  - `phase2/runners/run_phase2_live_daily.py`（CLI 入口，所有重 import lazy 化；产物三态分目录；与 phase2 其他 runner 对齐的 `_REPO_ROOT` 自注入 `sys.path`）
  - `phase2/live/tests/`（132 项新增单测，全部通过；含 InMemoryBroker + FakeClock + 临时 strategy 文件，不依赖外部网络）
  - `docs/system_integration_guide.md`（新增"阶段② live"章节）
  - `.codebuddy/plan/phase2_live_trading/`（背景与需求；进度以同名 `task_list` 为权威）
  - `.codebuddy/task_list/phase2_live_trading.md`（10 项任务进度表）
- **影响摘要**: 在 phase2 回测之上补齐了真实 OpenD 连接 + SIM/REAL 天级别 live 交易闭环；4 级 gate + 6 开关 + 状态机 + 三态分目录产物全部可单测。phase2 现有 75 个回测单测全数复跑通过；新增 132 项 live 单测（phase2/ 全集 215/215，1.06s）；dry_run smoke 已跑通产物落盘于 `state/runs/phase2_live/dry_run/smoke_dry_run_t10/`（`daily_report.json` + `positions_snapshot.csv` + 空 `orders/`）；既有 `phase2/strategy/*` 与 `phase2/backtest/*` 源码零改动；`tmp/` 与 `scripts/classic_multifactor/` 零改动。仍**不允许**直接连真实账户下单——必须 6 开关全置 + 人工审批后再动手。

## 2026-05-23 phase2 策略自我优化闭环（双 subagent，纯本地回测）

- **变更范围**: 在 phase2 之上新增 `phase2.optimize` 子包与 `phase2/runners/run_phase2_strategy_self_optimize.py` CLI；引擎补一个安全的 `param_overrides` 注入钩子；新增搜索空间 YAML 与 6 个 pytest 文件；同步 `docs/system_integration_guide.md`、`.codebuddy/task_list/phase2_strategy_self_optimize.md`。
- **关键文件**:
  - `phase2/optimize/__init__.py`（包级 `assert_no_live_imports` 守卫）
  - `phase2/optimize/session.py`（`SessionPaths`、session_id 校验、与 `phase2_multi_backtest` 的命名空间冲突保护）
  - `phase2/optimize/io_schemas.py`（`Proposal` / `TrialResult` / `Evaluation` / `IterEvaluations` / `LeaderboardRow` / `SessionSummary` + JSON/CSV 原子写）
  - `phase2/optimize/search_space.py`（YAML 加载、frozen 拒绝、`HARD_CEILINGS` 防御）
  - `phase2/optimize/trial_runner.py`（构建 `PortfolioBacktestEngine(force_live_submit=True, param_overrides=...)`，捕获异常 → `injection_mismatch` / `failed`）
  - `phase2/optimize/optimizer.py`（grid / random / local-perturb / explore + LLM 钩子失败降级）
  - `phase2/optimize/evaluator.py`（多维归一化打分 + ranking + continue/stop 决策 + 5 项诊断）
  - `phase2/optimize/coordinator.py`（主循环；progress.log；resume 支持；磁盘下限 1 GB）
  - `phase2/optimize/llm_bridge.py`（`OpenAICompatibleClient` 包装；`PHASE2_OPT_LLM_*` env vars；任何失败返回空降级）
  - `phase2/optimize/reporter.py`（`REPORT.md` + 与 `fixed_pool_5y_a1` 基线对比表）
  - `phase2/runners/run_phase2_strategy_self_optimize.py`（CLI，含 `--dry-run` / `--resume` / `--print-leaderboard` / `--llm-*`）
  - `phase2/backtest/portfolio_backtest_engine.py`（新增 `param_overrides: Optional[Dict[str, Any]]`，在 `initialize()` 之后做 `setattr` + `getattr==value` 一致性断言）
  - `phase2/strategy/config/optimize_search_space.yaml`（18 个安全可调参数 + frozen + 硬上限）
  - `phase2/strategy/tests/test_optimize_search_space.py`（10 项）
  - `phase2/strategy/tests/test_optimizer_subagent.py`（8 项）
  - `phase2/strategy/tests/test_evaluator_subagent.py`（8 项）
  - `phase2/strategy/tests/test_optimize_trial_runner.py`（5 项）
  - `phase2/strategy/tests/test_optimize_coordinator_dryrun.py`（3 项）
  - `phase2/strategy/tests/test_no_live_imports.py`（9 项子进程隔离断言）
  - `state/runs/phase2_strategy_self_optimize/.gitignore`（产物目录占位 + 排除）
  - `docs/system_integration_guide.md`（新增"阶段③ phase2 策略自我优化闭环"章节）
  - `.codebuddy/plan/phase2_strategy_self_optimize/`（背景与需求；进度以同名 `task_list` 为权威）
  - `.codebuddy/task_list/phase2_strategy_self_optimize.md`（10 项任务进度表）
- **影响摘要**: 在 phase2 多标回测之上叠加了一套"提案 → 注入 → 回测 → 评估 → 决策"的双 subagent 自优化闭环；策略源码零改动（参数全部走 `param_overrides` setattr 注入）；frozen_param 集合 + 三参数硬上限 + 9 项子进程级 import 守卫共同把闭环锁在 `force_live_submit=True` 的本地回测分支，禁止任何 OpenD/Futu 接触；新增 44 项 pytest 全绿，phase2/strategy 全集 119/119 通过；dry-run smoke 在 0.1 秒内完整跑通 2 iter × 2 trial，产物结构与 REPORT.md 渲染均符合预期。仍**不允许**自动落地"最优参数"——必须由人手动整理为新 baseline、再走原 phase2 多标回测复跑确认后，才可考虑替换运行参数。

## 2026-05-23 phase2 策略自优化首次真实 5 年区间闭环（session `opt_20260523T150600Z`）
- **变更范围**: 运行产物（不修改代码）。首次以真实 5 年日线（2021-05-23 → 2026-05-22）固定池（NVDA、MSFT、AVGO、TSM、TSLA、AMZN）启动 phase2 自优化双 subagent 闭环，验证全链路在生产时长下的稳定性。
- **运行配置**: `--max-iters 10 --trials-per-iter 4 --top-k 2 --rate 0.0003 --slippage 0.0 --init-cash 100000 --max-runtime-min 90 --patience 3 --min-delta 0.5`；`--llm-optimizer/--llm-evaluator` 已开启但未配置 `PHASE2_OPT_LLM_*` env，按设计自动降级为本地规则路径，全程未发起远端 LLM 请求。
- **结果**: 10 iter × 4 trial = 40 次回测全部 ok，总耗时约 30 秒；最佳 trial = `iter02_trial03`（origin=`explore`），score=0.8，年化 ~6.67%、最大回撤 ~10.92%、夏普 ~0.76、卡玛 ~0.61、交易 309 次；vs `fixed_pool_5y_a1` 基线 +约 11 个百分点总收益、回撤改善 ~1.9 个百分点。停机原因 `max_iters`，近 3 iter 未持续刷新最佳分。
- **关键参数差异（best vs default）**: `max_slices 5→2`、`pool_budget_pct 0.8→0.6`、`stop_loss_pct 0.05→0.04`、`take_profit_pct 0.10→0.29`，其余 14 个参数维持默认。
- **关键产物**:
  - `state/runs/phase2_strategy_self_optimize/opt_20260523T150600Z/REPORT.md`（含每 iter leaderboard 与最佳参数 diff、与 baseline 对比表）
  - `.../iter_02/trial_03/{summary.json,applied_params.json,...}`（best trial 详情）
  - `.../session_summary.json` / `.../progress.log`
  - 整个 session 目录受 `state/runs/phase2_strategy_self_optimize/.gitignore` 排除，不进入 git。
- **影响摘要**: 自优化闭环在真实 5 年区间下端到端跑通，纯本地降级路径表现稳定（与 dry-run 结果同形）；最佳参数仍属"建议项"，按规则**不**自动落地——需后续人工以新 baseline 走 `phase2/runners/run_phase2_multi_backtest_real.py` 复跑确认后再决定是否替换默认参数。

## 2026-05-23 phase2 策略 v2 趋势跟随重构（plan `phase2_strategy_redesign_v2`）
- **变更范围**: 新增 v2 单文件策略 + 计划/任务 + 系统集成指南 v2 章节 + REPORT；
  v1 文件保持不动以做 A/B 对照。
- **新增/修改文件**:
  - `phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2.py`（489 行单文件，
    stdlib only，零本地 import，私有 helper，LIVE_SUBMIT 默认 False）
  - `.codebuddy/plan/phase2_strategy_redesign_v2/{requirements,task-item}.md`
  - `.codebuddy/task_list/phase2_strategy_redesign_v2.md`
  - `docs/system_integration_guide.md`（在 futumd 策略章节内追加 "v2 趋势跟随版" 子章节）
  - `state/runs/phase2_strategy_redesign_v2/20260523T153754Z/{REPORT.md,v1_latest/,v2_default/}`
- **设计要点**: 入场从 5 条 AND（MA + RSI + vol_ratio + ATR-cap + concurrent）
  改为 3 条 AND（SMA200 regime + SMA100 长期 + Donchian55 突破）；出场从
  固定 TP/trailing-dd/fast-slow 死叉/ATR-cap 改为 Chandelier 止损（max_since_entry
  - 3·ATR(22)）+ 趋势离场（Close < SMA(50)）；预算公式去掉 position_pct 二次缩放，
  per_symbol_budget = NAV × 0.95 / 6；新增 vol-targeting (target_vol=0.15, scale ∈
  [0.4, 1.5])；单仓位无加仓。
- **5 年回测对照（同区间同池同手续费）**:
  - v1 latest：total_return +26.85%、年化 +4.89%、MaxDD 12.83%、315 trades、胜率 42%
  - v2 default：total_return **+32.49%**、年化 +5.81%、MaxDD **6.18%（减半）**、
    135 trades、胜率 **52%**
  - v1 复跑数值与历史 `fixed_pool_5y_a1` 完全一致 → 回测可重复。
- **影响摘要**: v2 在 mega-cap 趋势池上验证了"少做、做对、跟住"的趋势跟随范式
  在本仓库引擎 + 兼容硬约束下可正确落地；v1 保留作为参照，runner / adapter / 引擎
  / pool_loader 均零改动；现有 phase2 测试套件 119/119 仍全绿。**LIVE_SUBMIT 仍硬
  开关 False；本轮没有任何真实订单/远端连接发生**；仅本地数据库回测产物落盘。

## 2026-05-23（晚）phase2 v3 趋势跟随策略迭代优化完成
- **变更范围**: 在 v2 基础上做 6 轮迭代优化（iter1~iter6），最终选定 iter5 提升为 v3，达成 5 年回测 +320% 目标。
- **新增文件**:
  - `phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py`（最终版，单文件 stdlib only，零本地 import，完整继承所有硬约束）
  - `phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2_iter1.py` ~ `v2_iter6.py`（中间溯源版本，保留以做归因）
  - `state/runs/phase2_strategy_redesign_v2/20260523T153754Z/REPORT_OPTIMIZATION.md`（全程对照报告）
  - `state/runs/.../v2_iter1/`~`v2_iter6/`、`v3_final/` 7 套回测产物
- **设计要点**:
  - 入场更早：Donchian 55→10 抓拐头；regime 仅保留 SMA(200) 单层闸门
  - 出场更宽：Chandelier 3→6×ATR、ATR 22→40、ma_exit SMA50→SMA150
  - 单仓更重：max_concurrent 6→3，单仓 ~33% NAV
  - 抗洗：`min_hold_bars=10` 入场宽限期，`disaster_loss_pct=0.12` 单仓硬止损
  - 回撤护栏：`regime_flat`（SMA200 下穿即清仓）
  - 滚雪球：金字塔加仓 max_slices=3，每涨 1×ATR 加 0.5×base
- **5 年回测对照**（pool_config_fixed.yaml 6 只 / 100k / fee=0.0003）:
  - v1 latest: +26.85% / MDD 12.83%
  - v2 default: +32.49% / MDD 6.18%
  - **v3 final: +320.49% / 年化 33.40% / MDD 23.92% / 82 trades** ✅ 超 200% 目标 120 pp
- **失败试验记录**: iter6 引入 portfolio-level 15% 高水线 dd_cut 导致初期触发后无法恢复，最终只 +12.76% — 全局 dd_cut 在高 beta mega-cap 池上不可用，应坚持单仓位级硬止损 + regime 翻负清仓。
- **影响摘要**: phase2 测试套件 119/119 仍全绿；v1 / v2 文件未改动以保留 A/B 基线；LIVE_SUBMIT 仍硬开关 False；本轮 7 次回测均为本地数据库读取，无任何远端连接、无任何真实订单。

## 2026-05-24（凌晨）phase2 v3 — iter7：删除 max_slices shim，金字塔真正生效
- **变更范围**: 单文件 1 行级修复——删除 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py` `global_variables()` 末尾兼容 shim 段中 `self.max_slices = show_variable(1, GlobalType.INT)` 这一行；该行本是为旧 optimizer / report writer 兼容而设，但**默默把顶部金字塔块定义的 `max_slices=3` 覆盖回 1**，导致此前所有 v3 / iter5 回测中金字塔加仓**从未真正发生**。
- **修改文件**:
  - `phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py`（删除 shim，改为说明性注释）
- **新增产物**:
  - `state/runs/phase2_strategy_redesign_v2/20260523T161713Z/v3_baseline_slices1/`（修复前对照，max_slices=1）
  - `state/runs/phase2_strategy_redesign_v2/20260523T161713Z/v3_max_slices_3/`（修复后，max_slices=3）
  - `state/runs/phase2_strategy_redesign_v2/20260523T161713Z/REPORT_iter7_pyramid_unlock.md`
- **5 年回测对照**（pool_config_fixed.yaml 6 只 / 100k / fee=0.0003，同区间同池同费率）:
  - v3 baseline (shim, max_slices=1, = 历史 iter5)：+320.49% / 年化 33.40% / MDD 23.92% / 82 trades / 17W 22L
  - **v3 unlocked (max_slices=3)：+456.77% / 年化 41.13% / MDD 27.57% / 93 trades / 28W 26L** ✅
  - Δ Total Return **+136.29 pp**、Δ Annualised **+7.73 pp**、Δ MDD **+3.65 pp**
- **影响摘要**: 收益从超目标 1.6× 提升到 2.28×；金字塔加仓单（NVDA/AVGO/TSM 等强趋势 +1·ATR 加 0.5·base）按 Turtle 风格在强势区段叠加曝光；MDD 仅小幅扩张（+3.65 pp），仍由 regime_flat + disaster_stop 控制在 30% 以内。phase2 测试套件未受影响；LIVE_SUBMIT 仍硬开关 False；本轮 2 次回测均为本地 vnpy 数据库读取，无任何远端连接、无任何真实订单。

## 2026-05-24 phase2 v2 标的池 — pool_config_fixed_2.yaml 选股重构 + 5 年回测
- **变更范围**: 完全重写 `phase2/strategy/config/pool_config_fixed_2.yaml`，由原 6 只科技/消费集中池（NVDA/MSFT/AVGO/TSM/TSLA/AMZN）改为 10 只跨 4 行业分散池，目标降低相关性与回撤。
- **选股决策依据**: 基于 westock-data 实时拉取的 22 只候选标的的 PE/PS/市值/动量/52 周回撤/机构评级/日均成交额做横向打分；结合投资大师视角（巴菲特/芒格/段永平/李录）做护城河与估值合理性筛选。
- **新池构成**:
  - 半导体/AI: NVDA, TSM
  - 软件/云/广告: MSFT, GOOGL, META, AMZN
  - 医疗/创新药: LLY, UNH
  - 支付/金融科技: V
  - 必需消费: COST
- **剔除**: AVGO（PE 80 估值透支）、TSLA（高波动+管理人风险）
- **新增**: GOOGL/META（广告云双引擎，估值更便宜）、LLY（GLP-1 龙头）、V（支付双寡头）、UNH（健康保险+Optum）、COST（会员制+防御）
- **数据补全**: 通过 `tmp/run_futu_data_pull.py` 从 OpenD 拉取 LLY/V/COST/GOOGL/META/UNH 的 2021-05-23 ~ 2026-05-22 日线（每只约 1004-1256 根 bar）至本地 vnpy SQLite，仅缓存读取无远端订单。
- **新增产物**:
  - `phase2/strategy/config/pool_config_fixed_2.yaml`（v2 池配置，locked=true）
  - `state/runs/phase2_pool_v2_backtest/20260523T164520Z/v3_pool_v2_5y/{summary.json,trade_ledger.csv,equity_curve.csv,positions_daily.csv}`
- **5 年回测对照**（v3 策略 max_slices=3 / 100k / fee=0.0003 / 区间 2021-05-23 ~ 2026-05-22）:
  - 旧 6 只池: +456.77% / 年化 41.13% / MDD 27.57% / 93 trades
  - **v2 10 只池: +121.84% / 年化 17.33% / MDD 31.00% / 151 trades**
  - Δ Total Return **−334.93 pp**、Δ Annualised **−23.80 pp**、Δ MDD **+3.43 pp**
- **影响摘要**: 分散池在该 v3 策略下表现弱于集中池——主因是 v3 的金字塔/趋势加仓逻辑高度依赖 NVDA/AVGO/TSM 这类高 beta 强趋势标的的连续突破，而 V/COST/UNH/LLY 等防御/支付标的趋势信号稀疏导致信号利用率下降；MDD 反而略有扩大（31% vs 27.57%），分散并未带来回撤改善。仍超 100% 总收益，年化 17.33% 跑赢同期标普约 3-5 pp，但显著低于用户 200% 目标。phase2 测试套件未受影响；LIVE_SUBMIT 仍硬开关 False；本轮回测为本地数据库读取，无任何远端连接、无任何真实订单。

## 2026-05-24 — TED/Knot 流程迁移到独立目录

- **变更范围**：将 TED / Knot 研究流程从 `phase2/` 根目录整理到独立目录 `phase2/ted/`，统一收拢入口脚本、核心模块、测试脚本、配置文件和说明文档。
- **新主路径**：
  - `phase2/ted/run_knot_4dim_research.py`
  - `phase2/ted/run_candidate_preparation.py`
  - `phase2/ted/run_ted_discovery.py`
  - `phase2/ted/run_ted_full_pipeline.py`
  - `phase2/ted/trend_explosion_discovery.py`
  - `phase2/ted/test_ted_strategy.py`
  - `phase2/ted/README_TED.md`
  - `phase2/ted/README_TED_PIPELINE.md`
  - `phase2/ted/ted_config.yaml`
- **兼容处理**：保留 `phase2/` 根目录下的旧同名入口与核心模块作为兼容包装层，统一转发到 `phase2.ted`，避免已有命令和导入路径直接失效。
- **功能修复**：修复上层 TED 流程对 Knot 结果结构的兼容问题；底层 `run_knot_4dim_picks_us.py` 产出的顶层 `dimensions` 字段现在可被 `TrendExplosionDiscovery` 与候选准备脚本正确识别。
- **验证增强**：在 `phase2/ted/run_knot_4dim_research.py` 新增 `--validate-only` 轻量验证模式，只校验脚本可用性与 CLI，不触发远端 Knot 研究，也不改写关键研究产物。
- **文档同步**：已同步更新 `docs/system_integration_guide.md` 与 `phase2/ted/` 下说明文档，统一以 `phase2/ted/` 作为主使用目录。

## 2026-05-24 — TED 结果归档改为独立日期目录

- **变更范围**：将 TED 三阶段产物统一归档到 `state/runs/ted/YYYYMMDDTHHMMSS/`，并按阶段拆分保存，便于后续遍历、批次追溯和下游复用。
- **阶段结果目录**：
  - `stage1_knot_4dim/knot_4dim_us.json`
  - `stage2_candidate_preparation/candidate_inputs.prepare.report.us.json`
  - `stage2_candidate_preparation/candidate_inputs.dynamic.us.json`
  - `stage3_ted_discovery/ted_aggressive_pool_report.json`
  - `stage3_ted_discovery/pool_config.yaml`
  - 顶层 `manifest.json`
- **实现方式**：第一阶段直接通过底层 `--output` 写入 `TED` 目录；第二阶段在保留 `state/runs/candidate_inputs.*` 权威产物的同时额外归档快照；第三阶段生成最终报告后，同步导出一份兼容 `phase2` 标的池加载器的 `pool_config.yaml`。
- **兼容处理**：保留 `state/runs/ted_aggressive_pool_report.json` 与新增 `state/runs/ted_pool_config.yaml` 作为兼容镜像，避免旧读取方立刻失效。
- **影响摘要**：后续所有按批次回看 `TED` 的脚本、人工分析和结果遍历应优先读取 `state/runs/ted/`；若需要直接喂给 `phase2` 池加载器，可使用同批次 `stage3_ted_discovery/pool_config.yaml`。

## 2026-05-24 — docs 根入口收敛到 phase2 / 数据下载 / Knot

- **变更范围**：重写 `docs/index.rst` 的根入口，仅保留当前项目协作直接相关的 `phase2`、数据下载、`Knot` 文档链接，避免上游通用说明继续占据主入口。
- **新增文档**：新增 [data_download_guide.md](/projects/vnpy/docs/data_download_guide.md)，以项目实际工作流为准，聚焦 `run_prepare_candidate_inputs.py`、`TED` 三阶段和历史数据入库三条数据路径。
- **影响摘要**：后续从 `file:///projects/vnpy/docs` 进入时，默认将优先看到当前仓库主线文档；`community/`、`elite/` 等上游文档即使仍存在，也不再作为根入口暴露。
