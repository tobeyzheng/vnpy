# 实施计划 — 美股多标的量化策略阶段 ②（固定股票池落地）

> 本任务清单基于 [requirements.md](./requirements.md) 编制；权威完成进度以 [.codebuddy/task_list/us_multi_symbol_quant_phase2.md](/projects/vnpy/.codebuddy/task_list/us_multi_symbol_quant_phase2.md) 为准（项目规则 3）。所有"产生真实效果"的执行动作（跑回测、连 OpenD、SIM 提单、git push）必须先发"影响范围预告"等待用户确认（项目规则 2）。
>
> **目录策略（重要）**：本计划全部新建产物落在新独立顶级目录 `phase2/` 下，**不**写入 `tmp/`。结构约定：
>
> ```text
> phase2/
> ├── strategy/
> │   ├── us_multi_symbol_phase2_strategy.py
> │   ├── pool_loader.py
> │   ├── config/{pool_config.yaml, earnings_calendar.json}
> │   └── tests/test_*.py
> └── runners/{run_futu_perf_baseline.py, run_phase2_backtest.py, run_phase2_reconcile.py}
> ```
>
> 现有 `tmp/strategy/*` / `tmp/run_*.py` / `tmp/data/` / `tmp/futu_quant.md` 全部保持原状，仅作只读引用源。

- [ ] 1. 平台 50 标的性能基线脚本与基线文档
   - 新建 `phase2/runners/run_futu_perf_baseline.py`（默认 `--dry-run`，需用户确认才能跑），对 1 / 10 / 30 标的三档跑短样本回测（30 个交易日），采集 `handle_data` 单次耗时、回测引擎总耗时、串行下单延迟。
   - 新建 `docs/research/us_multi_symbol_quant/04_platform_performance_baseline.md`，先落"测试矩阵 + 待填占位"骨架；待用户确认执行后回填实测。
   - 在 [00_index.md](/projects/vnpy/docs/research/us_multi_symbol_quant/00_index.md) 阅读路径中追加 04 链接。
   - 30 标的 `handle_data` 耗时 > 5s 时触发降级（池上限从 20 降到 10）的判定写入文档。
   - _需求：5.1、5.2、5.3、5.4、8.3_

- [ ] 2. 标的池配置文件与加载/校验模块
   - 新建 `phase2/strategy/config/pool_config.yaml`，含 10–20 个 US symbol，每条带 `market` / `market_cap_bucket` / `sector` 字段。
   - 新建 `phase2/strategy/pool_loader.py`：实现 schema 校验（≤ 20、币种 USD、剔除 OTC / IPO < 1 年）、池规模 > 20 直接报错退出。
   - 编写 unit test `phase2/strategy/tests/test_pool_loader.py`：覆盖正常加载、规模超限报错、非 USD 拒绝、字段缺失拒绝 4 个用例。
   - _需求：1.1、1.2、1.4_

- [ ] 3. 池过滤规则与池更新日志
   - 在 `phase2/strategy/pool_loader.py` 中扩展 4 类过滤函数：流动性（ADV60）、价格区间（5 ≤ price ≤ 上限）、波动率（ATR%）、事件冻结（财报 ±2 日，财报日历用 `phase2/strategy/config/earnings_calendar.json` 静态占位）。
   - 任一过滤项不通过 → 当日剔除 symbol 但保留池配置；过滤动作只读不改 `pool_config.yaml`。
   - 池变更时在 [docs/project_operation_log.md](/projects/vnpy/docs/project_operation_log.md) 追加记录的工具函数。
   - 补充 unit test：4 类过滤各 1 个正向 + 1 个反向用例。
   - _需求：1.3、1.5、8.2_

- [ ] 4. 多标的策略骨架（新建文件，不改现有 NVDA 策略）
   - 新建 `phase2/strategy/us_multi_symbol_phase2_strategy.py`；顶部声明 `LIVE_SUBMIT = False` 硬开关与"切换 True 必须走独立审批 plan"注释。
   - 在 `trigger_symbols()` 中按 `pool_config.yaml` 调用 `declare_trig_symbol()`（≤ 20）；`handle_data` 内对每 symbol 独立计算 4 因子（F_trend / F_momentum / F_volatility 门控 / F_volume）。
   - 实现入场（5 项条件全满足）与出场优先级链（硬止损 > 移动止盈 > 趋势反转 > 波动率突变）；同 K 线不重复出场；同 symbol N 日内已出场拒绝重新入场。
   - 沿用现有份制变量名（`base_capital` / `slice_value` / `max_slices` / `position_pct`），作用域升级为单标的内；新增组合层 `pool_budget_pct` / `max_concurrent_holdings`。
   - 通过 `py_compile` 语法校验；**不**修改 `tmp/strategy/us_nvda_1d_strategy_multifactor.py` / `tmp/strategy/us_nvda_1d_strategy_trend_momentum.py` / `tmp/strategy/strategy_classic_multifactor.py`。
   - _需求：2.1、2.2、2.3、2.4、2.5、2.6、2.7、7.1、10.1_

- [ ] 5. 多标的资金预算分配模块
   - 在 `phase2/strategy/us_multi_symbol_phase2_strategy.py` 中新增 `_allocate_budget()`：每根 K 线先调 `net_asset` / `cash` 拉一次基线；按 `per_symbol_budget = NAV × pool_budget_pct / max_concurrent_holdings`、`slice_value = per_symbol_budget × position_pct` 计算预算。
   - 串行下单实现：每次 `place_market` / `place_limit` 后**重读** `cash`，下单数量取 `min(slice_value/price, max_qty_to_buy_on_cash(symbol))`；现金 < `slice_value` 跳单并记录 "insufficient_cash"。
   - 始终保留 `cash_buffer_pct`（默认 5%）现金；`max_orders_per_day`（默认 10）后拒绝当日剩余下单。
   - 编写 unit test `phase2/strategy/tests/test_budget_allocator.py`（mock 账户 API）覆盖：2 candidate 资金充足、3 candidate 资金不足跳单、超 10 笔拒绝、低于 cash_buffer 拒绝 4 个用例。
   - _需求：3.1、3.2、3.3、3.4、3.5、3.6、10.2_

- [ ] 6. 风控规则（单标 4 条 + 组合 5 条）
   - 单标层在策略中实现：硬止损（5%）、移动止盈（浮盈 10% 激活、回撤 5%）、连续 2 次加仓未盈利禁加、加仓护栏（`min_add_interval=10` + 回调 ≥ 2%）。
   - 组合层实现：浮亏 ≥ 8% 进入熔断态（仅减仓不开仓，熔断标记落盘 `state/runs/<plan>/<run_id>/portfolio_state.json`，需人工清除）；`sector_cap=40%` 行业上限；单标跳空 ≥ 5% 当日跳过；SPY 跳空 ≥ 3% 全策略当日不开新仓；状态落盘与重启恢复。
   - 编写风控 unit test `phase2/strategy/tests/test_risk_rules.py`：覆盖 9 条规则各 1 个触发用例 + 组合熔断重启恢复用例。
   - _需求：4.1–4.9_

- [ ] 7. 双轨回测对账脚本与产物
   - 新建 `phase2/runners/run_phase2_backtest.py`（默认 `--dry-run`）：用统一 `pool_config.yaml` + 统一参数集 + ≥ 3 年时间窗，分别在 Futu 平台路径与本地 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) 路径产出 `tmp/data/backtest_results/<run_id>/{config.json,statistics.json,trades.json,daily_results.json,report.html}`（沿用现有数据目录习惯，不在该目录新增源码）。
   - 新建 `phase2/runners/run_phase2_reconcile.py`：比较两轨年化收益、最大回撤、Sharpe、换手率、平均持仓天数 5 项指标；任一差异 > 20% 输出 `reconcile_diff.json` + 失败退出码。
   - 报告脚本输出参数邻域稳健性图与 OOS 衰减图（衰减 ≤ 30%）。
   - **不**修改 `tmp/run_local_backtest.py`；多 symbol 配置通过外部参数文件传入。
   - 实际执行回测前必须先发"影响范围预告"等用户确认。
   - _需求：6.1、6.2、6.3、6.4、6.5、9.1_

- [ ] 8. SIM 灰度准入清单（不进入实盘）
   - 新建 `docs/research/us_multi_symbol_quant/05_sim_gate_checklist.md`，逐项打勾：双轨回测一致 / 长样本 OOS 通过 / 风控 9 条生效 / 文档同步 / 性能基线达成。
   - 任一未达标 → 准入态保持"未通过"，不允许在文档中标记 SIM 启动；达成后只在 [docs/project_operation_log.md](/projects/vnpy/docs/project_operation_log.md) 记录达成日期与 commit。
   - 在 `phase2/strategy/us_multi_symbol_phase2_strategy.py` 与清单中明确"SIM 实际启动需独立 plan + 用户明确确认"。
   - _需求：7.1、7.2、7.3、7.4_

- [ ] 9. 文档同步、敏感信息脱敏自检与进度同步
   - 在 [docs/system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md) 新增"美股多标的策略阶段 ② 入口"章节，登记新顶级目录 `phase2/` 与其下策略文件、`pool_config.yaml`、回测脚本、对账脚本路径。
   - 在 [docs/project_operation_log.md](/projects/vnpy/docs/project_operation_log.md) 追加阶段 ② 关键节点记录（含"新增独立顶级目录 phase2/"决策）。
   - 创建 [.codebuddy/task_list/us_multi_symbol_quant_phase2.md](/projects/vnpy/.codebuddy/task_list/us_multi_symbol_quant_phase2.md)，与本任务清单编号一致；后续完成态以该文件为准。
   - 用 `grep_search` 扫描 `phase2/` 目录与本计划新增/修改文件的敏感关键词（`token`/`secret`/`password`/`api_key`/`account:`/`账号:`/`USD\d+`），命中数 = 0；含命中 → 立即脱敏。
   - _需求：8.1、8.2、8.4、8.5、10.1、10.2、10.3、10.4_

- [ ] 10. 阶段 ② 收口提交与推送
   - 完成上述 1–9 项后，按"实际执行前确认规则"先发推送预告（含 `phase2/` 新目录变更范围）。
   - 用户确认后执行 `git add -A` + 中文 commit + `git push`；禁止 `--force` / `--no-verify` 等破坏性参数。
   - 推送失败则停止并向用户汇报失败原因。
   - 推送完成后在 [.codebuddy/task_list/us_multi_symbol_quant_phase2.md](/projects/vnpy/.codebuddy/task_list/us_multi_symbol_quant_phase2.md) 标记阶段 ② 收口，准入清单态置为"待 SIM 独立 plan"。
   - _需求：9.1、9.2、9.3、9.4_
