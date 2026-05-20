# 需求文档 — 美股多标的量化策略阶段 ②（固定股票池落地）

## 引言

本计划是研究目录 [docs/research/us_multi_symbol_quant/](/projects/vnpy/docs/research/us_multi_symbol_quant/00_index.md) 的**阶段 ② 落地实现计划**，对应 [03_strategy_design.md §7.2](/projects/vnpy/docs/research/us_multi_symbol_quant/03_strategy_design.md) 的 M1–M6 里程碑。

### 背景

研究阶段已完成（`us_multi_symbol_quant` 计划已交付并推送至远端，commit `41423269`）：

- 01 指标调研：确定 MA / EMA / MACD / RSI / ATR / BOLL / OBV / ADX 短名单；
- 02 平台能力：Futu 单策略最多 50 运行标的，账户级现金共享需业务层做预算分配；
- 03 策略方案：4 因子（趋势 / 动量 / 波动率门控 / 量能）+ 2 层份制（池配额 + 单标份制）+ ≥4 条风控。

阶段 ② 目标：把单标 NVDA 多因子（[us_nvda_1d_strategy_multifactor.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_multifactor.py)）扩展为 **10–20 标的固定股票池**的多标的策略，跑赢 SPY 等权基准且最大回撤 ≤ 单标基线。

### 范围

**包含（In-Scope）**：

- 标的池配置文件（`pool_config.yaml`）与池过滤规则代码化；
- 多标的策略骨架（**新建文件**，不修改现有 NVDA 策略源码）；
- 单策略多标的资金预算分配（含 `cash` 共享池处理）；
- 单标的层 4 条 + 组合层 5 条风控规则；
- 双轨回测：Futu 平台 + 本地 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) 对账；
- 平台 50 标的性能基线验证（前置）；
- 文档同步：`docs/system_integration_guide.md`、`docs/project_operation_log.md`、`docs/research/us_multi_symbol_quant/00_index.md` 反向链接补全。

**不包含（Out-of-Scope）**：

- 阶段 ③ 横截面 top-K 排序选股（独立计划）；
- SIM → REAL 切换（独立计划，必须额外审批）；
- 跨市场（US+HK）混合；
- 行业映射表 / 财报日历的数据源选型（沿用占位实现，落地阶段 ③ 前再独立选型）；
- 机器学习 / 深度模型；
- 期权 / 牛熊证 / 高频。

### 关键边界与硬约束

1. **不修改现有策略源码**：[us_nvda_1d_strategy_multifactor.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_multifactor.py)、[us_nvda_1d_strategy_trend_momentum.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_trend_momentum.py)、[strategy_classic_multifactor.py](/projects/vnpy/tmp/strategy/strategy_classic_multifactor.py) 全部保持原状。
2. **不实施实盘下单**：本计划只交付到 SIM 灰度准入门槛，**不**触发 REAL 下单；`LIVE_SUBMIT` 默认 `False`。
3. **执行确认门**：所有"产生真实效果"的动作（回测运行、Futu/OpenD 连接、SIM 提交）执行前必须先发"影响范围预告"，等待用户明确确认（项目规则 2）。
4. **敏感信息脱敏**：全部资金量级使用占位符（NAV、`pool_budget_pct`、`per_symbol_budget`）；不暴露真实账号、token、完整金额。
5. **份制思想复用**：`base_capital` / `slice_value` / `max_slices` 思想在新策略中升级为"池配额 + 单标份制"两层结构（[03 §4.2](/projects/vnpy/docs/research/us_multi_symbol_quant/03_strategy_design.md)）。
6. **平台标的数上限**：单策略 ≤ 50 运行标的，本阶段池规模硬上限 = 20。

### 成功标准（验收门槛）

阶段 ② 通过的全部条件：

- 双轨回测结论一致（关键指标差异 ≤ 设定阈值）；
- 长样本（≥ 3 年日线、含 2020–2025 牛熊切换）OOS 评估指标全部达成（[03 §6.3](/projects/vnpy/docs/research/us_multi_symbol_quant/03_strategy_design.md)）：
  - 年化收益 ≥ SPY 等权；
  - 最大回撤 ≤ 单标基线 MDD；
  - Sharpe ≥ 1.0；
  - 换手率 ≤ 200% / 年；
  - 单笔成交占 ADV60 ≤ 1%；
- 风控全部生效（单标层 4 条 + 组合层 5 条，含组合熔断与黑天鹅日规则）；
- 文档同步落实，且 `.codebuddy/task_list/us_multi_symbol_quant_phase2.md` 标记全部完成。

---

## 需求

### 需求 1 — 标的池配置与构造

**用户故事：** 作为策略开发者，我希望以可配置文件的形式定义阶段 ② 固定股票池，以便在不改代码的前提下调整池成员、并满足"流动性 / 价格 / 波动率 / 事件冻结"四类过滤规则。

#### 验收标准

1. WHEN 计划落地任务执行时 THEN 系统 SHALL 在仓库新增 `pool_config.yaml`（路径在设计阶段确定，建议 `tmp/strategy/config/`），包含 ≥ 10 且 ≤ 20 个美股 symbol、`market`、`market_cap_bucket`、`sector` 字段。
2. WHEN 加载 `pool_config.yaml` 时 THEN 系统 SHALL 校验 schema（`symbols` 列表非空、≤ 20、币种统一为 USD、不含 OTC / IPO < 1 年标的）；schema 不通过 SHALL 拒绝运行并打印明确错误。
3. WHEN 计算池过滤指标时 THEN 系统 SHALL 实现 4 类过滤：流动性（ADV60）、价格区间（5 ≤ price ≤ 上限）、波动率（ATR%）、事件冻结（财报 ±2 日，财报日历允许阶段 ② 用静态占位文件）；任一过滤项不通过 SHALL 当日剔除该 symbol 但**不**从池配置中删除。
4. IF 池规模 > 20 THEN 系统 SHALL 在加载阶段直接报错退出（对齐研究 §1.2 硬约束）。
5. WHEN 池更新时 THEN 系统 SHALL 在 `docs/project_operation_log.md` 追加一条记录（日期、新增/移除 symbol、原因）。

---

### 需求 2 — 多标的策略骨架（新建文件，不动现有）

**用户故事：** 作为策略开发者，我希望基于现有 NVDA 多因子策略的"信号 + 份制"思想新建一份多标的版本策略文件，以便在不破坏现有单标策略的前提下扩展到固定股票池。

#### 验收标准

1. WHEN 落地任务创建策略文件时 THEN 系统 SHALL 新建 `tmp/strategy/us_multi_symbol_phase2_strategy.py`（最终路径在设计阶段确认），且**不**修改 `us_nvda_1d_strategy_multifactor.py` / `us_nvda_1d_strategy_trend_momentum.py` / `strategy_classic_multifactor.py`。
2. WHEN 策略初始化时 THEN 系统 SHALL 在 `trigger_symbols()` 内对池中每个 symbol 调用 `declare_trig_symbol()`，运行标的数 SHALL ≤ 20（[02 §2.1](/projects/vnpy/docs/research/us_multi_symbol_quant/02_futu_multi_symbol_capability.md)）。
3. WHEN `handle_data` 被驱动时 THEN 系统 SHALL 对池中每个 symbol 独立计算 4 因子：F_trend（EMA12 vs EMA26 + ADX > 25）、F_momentum（RSI 从超卖回升 或 EMA 金叉 ≤ 3 根）、F_volatility（ATR% ≤ 阈值，**门控**不计分）、F_volume（vol_ratio ≥ 1.2）。
4. WHEN 入场条件全部满足时 THEN 系统 SHALL 触发买入信号 candidate，但 SHALL 在通过组合层风控（需求 4）后才进入下单分配（需求 3）。
5. WHEN 出场条件触发时 THEN 系统 SHALL 按优先级执行：硬止损 > 移动止盈 > 趋势反转 > 波动率突变；同一根 K 线 SHALL 不重复触发多次相同出场。
6. IF 同一 symbol 在 N 个交易日内（默认 5）已出场 THEN 系统 SHALL 拒绝当日重新入场（避免反复进出）。
7. WHEN 策略代码生成时 THEN 系统 SHALL 沿用现有份制变量名（`base_capital` / `slice_value` / `max_slices` / `position_pct`），但作用域升级为"单标的内"，组合层另设 `pool_budget_pct` / `max_concurrent_holdings`。

---

### 需求 3 — 多标的资金预算分配

**用户故事：** 作为风控负责人，我希望在多标的同时触发买入信号时，业务层能正确分配账户共享现金池，以便不出现"前面 symbol 用光资金、后面 symbol 下单失败"或"超买导致总仓位破限"。

#### 验收标准

1. WHEN 同一根 K 线产生 ≥ 2 个买入 candidate 时 THEN 系统 SHALL 先调用账户级 API（`net_asset` / `cash`，[02 §3.3](/projects/vnpy/docs/research/us_multi_symbol_quant/02_futu_multi_symbol_capability.md)）拉取一次净资产与可用现金。
2. WHEN 计算单标预算时 THEN 系统 SHALL 按公式 `per_symbol_budget = NAV × pool_budget_pct / max_concurrent_holdings`、`slice_value = per_symbol_budget × position_pct`；公式与默认参数 SHALL 与 [03 §4.2](/projects/vnpy/docs/research/us_multi_symbol_quant/03_strategy_design.md) 一致。
3. WHEN 串行下单时 THEN 系统 SHALL 在每次 `place_market` / `place_limit` 后**重读** `cash`，并以 `min(slice_value / price, max_qty_to_buy_on_cash(symbol))` 作为下单数量上限。
4. IF 当前可用现金 < `slice_value` 时 THEN 系统 SHALL 跳过本次下单并记录跳单原因（"insufficient_cash"），不报错退出。
5. WHEN 任一时刻 THEN 系统 SHALL 保留 ≥ `cash_buffer_pct`（默认 5%）的现金不参与下单。
6. WHEN 单日下单笔数达 `max_orders_per_day`（默认 10）时 THEN 系统 SHALL 拒绝当日剩余下单 candidate。

---

### 需求 4 — 风控规则（单标层 4 条 + 组合层 5 条）

**用户故事：** 作为风控负责人，我希望策略包含可独立验证的多层风控规则，以便单标黑天鹅、组合黑天鹅、行业集中度都能被自动隔离。

#### 验收标准

**单标的层（4 条）**：

1. WHEN 单标浮亏达 `stop_loss_pct`（默认 5%） THEN 系统 SHALL 触发硬止损平仓。
2. WHEN 浮盈达 `take_profit_pct`（默认 10%） AND 持仓最高价回撤 ≥ `trailing_drawdown_pct`（默认 5%） THEN 系统 SHALL 触发移动止盈。
3. IF 同一标的连续 2 次加仓后仍亏损 THEN 系统 SHALL 禁止该标的再加仓，直至清仓重置。
4. WHEN 加仓时 THEN 系统 SHALL 校验 `min_add_interval`（默认 10 K 线）与"回调 ≥ 2%"两条件，任一不满足 SHALL 拒绝加仓。

**组合层（5 条）**：

5. WHEN 策略累计浮亏 ≥ `portfolio_dd_limit`（默认 8%） THEN 系统 SHALL 进入熔断态：仅允许减仓与止损，禁止新开仓；熔断状态 SHALL 持久化到 `state/runs/`，恢复条件需人工 review 后手动清除熔断标记。
6. IF 任一时刻同一行业（按 `pool_config.yaml` 中 `sector` 字段聚合）持仓总市值 / 池配额 > `sector_cap`（默认 40%） THEN 系统 SHALL 拒绝该行业的新开仓信号。
7. WHEN 单一标的开盘跳空 ≥ 5% THEN 系统 SHALL 当日跳过该标的的新开仓信号。
8. WHEN SPY 开盘跳空 ≥ 3%（黑天鹅日） THEN 系统 SHALL 全策略当日不开新仓（仅允许减仓与止损）。
9. WHEN 策略关闭或重启时 THEN 系统 SHALL 把组合状态（持仓、可用现金、信号列表、熔断标记）落盘到 `state/runs/<plan>/<run_id>/portfolio_state.json`，重启时 SHALL 从落盘文件恢复。

---

### 需求 5 — 平台 50 标的性能基线（前置验证）

**用户故事：** 作为工程负责人，我希望在阶段 ② 大规模回测前，先通过最小验证脚本确认 Futu 平台在 N 标的下的性能拐点，以便阶段 ② 池规模选择有可观测的依据。

#### 验收标准

1. WHEN 性能验证任务执行时 THEN 系统 SHALL 在 Futu 平台上以 1 / 10 / 30 标的三档分别跑短样本回测（30 个交易日），记录：`handle_data` 单次执行平均耗时、回测引擎总耗时、串行下单延迟（[02 §5.2](/projects/vnpy/docs/research/us_multi_symbol_quant/02_futu_multi_symbol_capability.md) 三项待验证）。
2. WHEN 验证结果写入 THEN 系统 SHALL 在 `docs/research/us_multi_symbol_quant/` 新增或追加 `04_platform_performance_baseline.md`（性能基线文档），含三档对比表与"是否阻塞阶段 ②"判定。
3. IF 30 标的下 `handle_data` 单次耗时 > 5 秒（阻塞实盘日 K 线触发） THEN 系统 SHALL 触发降级路径：阶段 ② 池上限从 20 调到 10，并在 `docs/project_operation_log.md` 记录降级。
4. WHEN 性能验证脚本存在时 THEN 它 SHALL 标记为"需用户确认才能跑回测"，不自动触发（项目规则 2）。

---

### 需求 6 — 双轨回测对账

**用户故事：** 作为策略开发者，我希望同一套规则在 Futu 平台与本地回测两条轨道上都能跑出可对账的结果，以便在平台黑盒撮合之外保留一条可重现、可单步调试的回归路径。

#### 验收标准

1. WHEN 双轨回测开始时 THEN 系统 SHALL 在 Futu 平台与本地 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) 上使用同一 `pool_config.yaml`、同一参数集（`stop_loss_pct` / `pool_budget_pct` / `max_concurrent_holdings` / 因子权重）、同一时间窗口（≥ 3 年）。
2. WHEN 两轨回测产物落盘时 THEN 系统 SHALL 在 `tmp/data/backtest_results/<run_id>/` 各自保存 `config.json`、`statistics.json`、`trades.json`、`daily_results.json`、`report.html`。
3. WHEN 双轨对账执行时 THEN 系统 SHALL 比较：年化收益、最大回撤、Sharpe、换手率、平均持仓天数 5 项指标；任一指标差异 > **20%** SHALL 判为不一致并触发追溯（行情对齐 / 撮合差异 / 成交量过滤口径）。
4. WHEN 本地回测扩展多 symbol 入参时 THEN 任何对 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) 的修改 SHALL 走独立 plan 推进，不在本计划内修改。本计划只 SHALL 通过外部参数文件传入多 symbol 配置。
5. WHEN 报告生成时 THEN 系统 SHALL 输出**参数邻域稳健性图**与 **OOS 衰减图**两个稳健性图（[03 §6.5](/projects/vnpy/docs/research/us_multi_symbol_quant/03_strategy_design.md)），衰减率 SHALL ≤ 30%。

---

### 需求 7 — SIM 灰度准入门槛（不进入实盘）

**用户故事：** 作为风控负责人，我希望本计划只交付到 SIM 灰度准入门槛，绝不触发实盘 / REAL 下单，以便 REAL 切换由独立计划与人工审批控制。

#### 验收标准

1. WHEN 任何下单代码路径生成时 THEN 系统 SHALL 在策略文件顶部声明 `LIVE_SUBMIT = False` 硬开关；切换 `True` SHALL 在策略代码注释中明确标记"必须走独立审批 plan"。
2. WHEN SIM 准入条件评估时 THEN 系统 SHALL 出具一份准入清单（建议 `docs/research/us_multi_symbol_quant/05_sim_gate_checklist.md`），逐项打勾：双轨回测一致 / 长样本 OOS 通过 / 风控生效 / 文档同步 / 性能基线达成。
3. IF 准入清单任一项不达标 THEN 系统 SHALL 拒绝把准入状态置为"通过"。
4. WHEN 准入达成 THEN 系统 SHALL 在 `docs/project_operation_log.md` 记录准入达成的日期与对应 commit；SIM 实际启动留待"独立 plan + 用户明确确认"。

---

### 需求 8 — 文档同步与计划进度

**用户故事：** 作为协作开发者，我希望本计划落地过程中所有框架性、入口性、规则性变化都同步到 `docs/`，以便任何人能从文档快速回溯阶段 ② 的边界与变化。

#### 验收标准

1. WHEN 策略骨架落地时 THEN 系统 SHALL 同步更新 [docs/system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)，新增"美股多标的策略阶段 ② 入口"章节，含文件路径、参数名、`pool_config.yaml` 引用。
2. WHEN 任一里程碑（M1–M6）完成时 THEN 系统 SHALL 在 [docs/project_operation_log.md](/projects/vnpy/docs/project_operation_log.md) 追加记录（日期、范围、关键文件、影响摘要）。
3. WHEN 性能基线产出时 THEN 系统 SHALL 把 `04_platform_performance_baseline.md` 的链接补到 [docs/research/us_multi_symbol_quant/00_index.md](/projects/vnpy/docs/research/us_multi_symbol_quant/00_index.md) 的"阅读路径"中。
4. WHEN 任务进度变化时 THEN 系统 SHALL 以 `.codebuddy/task_list/us_multi_symbol_quant_phase2.md` 为权威进度（项目规则 3），`.codebuddy/plan/us_multi_symbol_quant_phase2/tasks.md` 仅作任务定义。
5. IF `.codebuddy/plan/us_multi_symbol_quant_phase2/tasks.md` 与 `task_list` 编号 / 名称不一致 THEN 系统 SHALL 在同一轮改动内保持双方对齐。

---

### 需求 9 — 执行确认门与提交推送规则

**用户故事：** 作为项目负责人，我希望本计划在每个会"产生真实效果"的环节都先取得我的明确确认，且 git 推送严格遵循自动提交规则但不使用任何破坏性参数，以便我对运行边界保有最终控制权。

#### 验收标准

1. WHEN 任务计划要执行下列动作时 THEN 系统 SHALL 先发"影响范围预告"并等待用户明确确认（项目规则 2）：
   - 在 Futu 平台跑回测；
   - 在本地跑 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py)；
   - 启动 / 连接 OpenD；
   - SIM / REAL 提单；
   - 改写 `state/runs/`、订单状态、对账文件；
   - 任何 `git push`。
2. WHEN 用户明确确认后 THEN 系统 SHALL 执行对应动作，并在结果中说明产出。
3. WHEN 提交推送时 THEN 系统 SHALL 使用 `git add -A` + 普通 `git commit -m "<中文简洁说明>"` + `git push`；SHALL **不**使用 `--force` / `--force-with-lease` / `--no-verify` / `push --mirror` / `reset --hard`（项目规则 1）。
4. IF git commit 或 push 因权限 / 网络 / hooks / 冲突失败 THEN 系统 SHALL 停止并向用户汇报失败原因，不自行尝试破坏性手段。

---

### 需求 10 — 敏感信息脱敏与安全边界

**用户故事：** 作为安全责任人，我希望全部产物中不出现真实账户金额、完整账号、API token 等敏感信息，以便仓库可对外可见而不泄露账户细节。

#### 验收标准

1. WHEN 文档 / 代码 / 注释 / 日志生成时 THEN 系统 SHALL 不出现真实账户金额；资金量级 SHALL 仅以脱敏占位符（NAV、`pool_budget_pct`、`per_symbol_budget`）表达。
2. WHEN 引用账户级 API 时 THEN 系统 SHALL 不在示例输出 / 文档中粘贴真实 `cash` / `net_asset` 数值。
3. WHEN 参考已有日志（如 `nohup.out`、`tmp/data/backtest_results/`）时 THEN 涉及真实金额 / 账号 SHALL 先脱敏再引用。
4. WHEN 提交前自检时 THEN 系统 SHALL 用 `grep_search` 扫描本计划新增 / 修改文件中的关键词（`token` / `secret` / `password` / `api_key` / `account:` / `账号:` / `USD\d+`），命中数 SHALL = 0。

