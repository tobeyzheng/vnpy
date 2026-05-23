# 需求文档 — phase2 策略自迭代优化（Optimizer + Evaluator 双 subagent 闭环）

## 引言

当前 `phase2` 已具备：多标的本地回测（`phase2/runners/run_phase2_multi_backtest.py` + `PortfolioBacktestEngine`）、futu 模拟 / 真实账户日级交易链路、固定候选池（`pool_config_fixed.yaml`）。策略本体 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py` 由 18+ 个全局参数（MA / RSI 窗口与阈值、止损止盈、并发上限、池预算等）控制，已在 5 年固定池基线上验证可成交（315 笔 / +26.85% / max_dd 12.83%）。

本需求面向**策略自我迭代优化闭环**，引入两个独立 subagent 角色：

- **Optimizer Agent（优化者）**：基于历史评估反馈，提出"下一轮"参数变体（必要时也可提出"受白名单约束的代码片段变体"），生成 N 个候选配置，调度回测；
- **Evaluator Agent（评估者）**：对每一轮所有候选回测产物做**多维度评估**（收益、风险、稳健、过拟合风险、与基线的统计差异），输出统一 ranking 与 stop-or-continue 建议；
- **Coordinator（编排器）**：驱动"优化 → 回测 → 评估"循环，按收敛/上限规则自动决定何时结束。

整个闭环只跑**本地回测**，绝不连 OpenD / 不发任何 SIM 或 REAL 订单；策略文件 futu 单文件契约（`LIVE_SUBMIT=False` 出厂、stdlib only、单文件）必须保持不变。

---

## 全局术语

| 术语 | 含义 |
|---|---|
| **trial（试验）** | 一组完整策略参数 + 回测窗口 → 一次回测 → 一个 `summary.json`。 |
| **iteration（迭代轮）** | 一次"Optimizer 提案 → 一组 trial 并行/串行回测 → Evaluator 评估"完整循环。 |
| **baseline（基线）** | 当前已落盘的最优 trial（用 `state/runs/phase2_strategy_self_optimize/baseline/` 软链或 manifest 指向）。 |
| **search_space（搜索空间）** | YAML 描述的可调参数及范围；优化器只能在该空间内提案。 |
| **safe_param（安全参数）** | 不会破坏 futu 沙箱契约的策略参数（global_variables 中已 `show_variable` 暴露的项）。 |
| **frozen_param（冻结参数）** | 用户禁止修改的项（如 `LIVE_SUBMIT`、池构成、`max_orders_per_day` 上限等）。 |

---

## 需求

### 需求 1 — Plan / 产物目录与命名隔离

**用户故事：** 作为研究者，我希望策略自迭代闭环的所有产物与现有 phase2 回测/实盘产物完全隔离，以便随时清理或归档而不污染其他流程。

#### 验收标准

1. WHEN 闭环首次运行 THEN 系统 SHALL 在 `state/runs/phase2_strategy_self_optimize/<session_id>/` 下创建会话目录，`session_id` 默认为 `opt_YYYYMMDDTHHMMSSZ`。
2. WHEN 一轮迭代生成 N 个 trial THEN 系统 SHALL 把每个 trial 的回测产物落到 `state/runs/phase2_strategy_self_optimize/<session_id>/iter_<k>/trial_<m>/`，结构与 `phase2_multi_backtest` 一致（`equity_curve.csv / positions_daily.csv / trade_ledger.csv / summary.json`）。
3. WHEN 评估器完成评估 THEN 系统 SHALL 在 `iter_<k>/` 同级写出 `proposals.json`（优化器原始提案）、`evaluations.json`（评估器输出）、`leaderboard.csv`（含 `iter, trial, score, total_return, max_dd, trade_count, sharpe_proxy`）。
4. WHEN 闭环结束 THEN 系统 SHALL 在 `<session_id>/` 根下写 `session_summary.json`（含 `iterations / total_trials / best_trial_path / stop_reason`），并以**软链 / manifest** 指向 best trial，绝不覆盖 `phase2_multi_backtest` 既有目录。
5. IF 任何 trial 的产物路径与 `state/runs/phase2_multi_backtest/` 下任何已有 run_id 冲突 THEN 系统 SHALL 拒绝该 trial 并退出非零码。

---

### 需求 2 — 参数搜索空间与白名单（safe_param 约束）

**用户故事：** 作为研究者，我希望优化器只能调试策略中已暴露的安全参数，不能破坏 futu 单文件契约或硬安全开关。

#### 验收标准

1. WHEN 闭环启动 THEN 系统 SHALL 从 `phase2/strategy/config/optimize_search_space.yaml` 加载搜索空间，文件至少描述：参数名、类型（int/float/bool）、范围（min/max/step 或离散候选集）、默认值。
2. WHEN 搜索空间引用任意 `frozen_param` THEN 系统 SHALL 拒绝加载并明确报错。`frozen_param` 至少包括：`LIVE_SUBMIT`、`_pool`（池构成）、`max_orders_per_day`（上限不得提高）、池预算硬上限（`pool_budget_pct ≤ 0.95`）、`max_concurrent_holdings ≤ 8`、`stop_loss_pct ∈ [0.02, 0.10]`。
3. WHEN 优化器提出参数变体 THEN 系统 SHALL 校验每个值都落在搜索空间内，**越界即整 trial 拒绝**，不静默 clamp。
4. WHEN 用户希望优化器也调试**策略代码片段**（如条件 `cond_trend / cond_momentum` 阈值或表达式） THEN 系统 SHALL 通过"代码白名单 patch"机制实现：每个白名单 patch 只能改动**已显式标注的代码区块**（用类似 `# OPT_BLOCK: cond_momentum` 注释包裹），且 patch 必须能通过 `python3 -m py_compile` 与 `phase2/strategy/tests/` 全部测试。
5. IF 当前轮次未启用代码 patch 模式 THEN 系统 SHALL 仅生成"参数 YAML 覆盖文件"，绝不修改策略源文件。

---

### 需求 3 — Trial 注入与回测执行（不破坏单文件契约）

**用户故事：** 作为研究者，我希望每个 trial 用一份独立的参数覆盖文件运行回测，不污染默认策略文件，以便随时回滚。

#### 验收标准

1. WHEN trial 启动 THEN 系统 SHALL 把该 trial 的参数写入 `iter_<k>/trial_<m>/params.yaml`，并通过**回测引擎注入路径**（仿照 A1 的 `strategy._pool` 注入方式）在 `strategy.initialize()` 之后用 `setattr` 覆盖到 `Strategy` 实例上，**绝不**修改 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py` 源文件。
2. WHEN 注入完成 THEN 系统 SHALL 校验 `Strategy` 实例上对应属性的最终值与 `params.yaml` 完全一致；若不一致，trial 失败并落 `trial_status=injection_mismatch`。
3. WHEN 回测窗口未显式指定 THEN 系统 SHALL 默认使用最近 5 年（与已确认基线一致），固定 `--rate=0.0003 --init-cash=100000`，`pool-config` 默认 `pool_config_fixed.yaml`。
4. WHEN 单个 trial 抛出未捕获异常 THEN 系统 SHALL 捕获、把异常写入 `trial_<m>/error.log`、标记 `trial_status=failed`、继续下一个 trial，不中断整轮。
5. IF 用户给出 `--respect-live-submit` THEN 系统 SHALL 拒绝，因优化闭环必须在 `force_live_submit=True` 的纯回测路径下，且**绝不连 OpenD**。

---

### 需求 4 — Optimizer subagent（优化者）

**用户故事：** 作为研究者，我希望优化者基于上一轮所有 trial 的评估结果，按可解释规则提出下一轮的参数变体，并把决策依据落盘。

#### 验收标准

1. WHEN 第 1 轮启动 THEN 优化器 SHALL 以 `pool_config_fixed.yaml + 当前策略默认参数` 作为 trial_0 基线，**额外**提出 N-1 个变体（默认 N=4）；变体生成方式至少支持两种：`grid`（按搜索空间步长枚举局部邻域）与 `random`（在搜索空间内均匀采样）；提出 `bayesian` 作为可选的进阶模式（依赖 `optuna`，缺失则降级为 `random`，并在日志中说明）。
2. WHEN 第 k≥2 轮启动 THEN 优化器 SHALL 读取上一轮 `evaluations.json`，按评估器给出的 `top_k`（默认 2）作为父代，在父代邻域内进行小步长扰动（局部搜索）+ 在当前 leaderboard 之外的方向再各扔 1 个"探索 trial"，整体保持 N 个/轮。
3. WHEN 提案生成 THEN 优化器 SHALL 为每个 trial 写一个 `proposal.json`，包含：`trial_id`、`origin`（grid/random/bayes/local-perturb/explore）、`parent_trial`（如有）、`changed_params`（与父代/默认的 diff）、`rationale`（为什么改这些参数，至少 1 句中文）、`predicted_metric_direction`（`up/down/unknown`）。
4. WHEN 用户启用 `--llm-optimizer` THEN 优化器 SHALL 通过现有 `vnpy_llm.llm_client.OpenAICompatibleClient`/`KnotAgentRuntime` 发起一次 JSON-only 调用，让远端 LLM 给出"在搜索空间内的 N 个候选参数 + 改动理由"；返回值 SHALL 经 schema 校验，校验失败立即降级为 `random` 模式并继续。
5. IF 优化器在某一轮未能产生任何合法 trial（全部越界/全部失败提案） THEN 系统 SHALL 中止整个会话并以 `stop_reason=optimizer_dry` 写入 `session_summary.json`。

---

### 需求 5 — Evaluator subagent（评估者）

**用户故事：** 作为研究者，我希望评估者用统一标准对每轮所有 trial 打分排序，并清晰告诉我"是否应继续"。

#### 验收标准

1. WHEN 一轮所有 trial 回测完成 THEN 评估者 SHALL 对每个 trial 至少计算以下指标：`total_return_pct`、`annualised_return_pct`、`max_drawdown_pct`、`trade_count`、`win_rate=win/(win+loss)`（loss=0 时记 `unknown`）、`sharpe_proxy = annualised_return_pct / (年化波动率，从 equity_curve 推算)`、`calmar_proxy = annualised_return_pct / max_drawdown_pct`、`turnover_proxy = trade_count / trading_days`、`exposure_days = 持仓非零的天数 / trading_days`。
2. WHEN 计算完成 THEN 评估者 SHALL 用一个**复合分**给出 ranking：默认 `score = 0.5 * normalized(annualised_return_pct) + 0.3 * normalized(calmar_proxy) - 0.2 * normalized(max_drawdown_pct)`，归一化方式在 `evaluations.json` 中显式记录；用户可通过 `--score-formula` 注入自定义权重 YAML。
3. WHEN ranking 完成 THEN 评估者 SHALL 输出 `top_k`（默认 2）trial 列表 + 每个 trial 的"问题诊断"（至少包含：是否疑似过拟合 = `trade_count < 20 或 calmar_proxy 大幅高于全样本均值的 +3σ`；是否疑似无效 = `trade_count == 0`；是否疑似超重仓 = `exposure_days > 0.95`）。
4. WHEN 评估完成 THEN 评估者 SHALL 给出 `continue / stop` 决策与 `stop_reason`，决策输入至少包括：`best_score 较上一轮提升 < ε`（默认 ε=0.5）、`top_k 在最近 patience 轮（默认 3）未变化`、`迭代轮数达到 max_iters`（默认 10）、`总耗时达到 max_runtime_min`（默认 90 分钟）。
5. WHEN 用户启用 `--llm-evaluator` THEN 评估者 SHALL 在本地诊断之上**追加**一次远端 LLM JSON-only 调用，让 LLM 给"非数值层"的判断（如"参数集是否互相矛盾"、"是否触发了已知反模式"）；LLM 输出仅作为附注（`evaluations.json.llm_notes`），不得改写 ranking 或 stop_reason。
6. IF 评估器无法读取某 trial 的 `summary.json` THEN 该 trial 在 ranking 中记 `score=null`，并在 `leaderboard.csv` 标注 `status=missing_summary`，不影响其他 trial 排序。

---

### 需求 6 — Coordinator 闭环与终止条件

**用户故事：** 作为研究者，我希望整个闭环能由一条命令启动并自动终止，不需要我盯着每一轮。

#### 验收标准

1. WHEN 用户运行 `python3 phase2/runners/run_phase2_strategy_self_optimize.py --max-iters 10 --trials-per-iter 4 --start <YYYY-MM-DD> --end <YYYY-MM-DD>` THEN 系统 SHALL 串行执行 ≤ `max_iters` 轮闭环，每轮 ≤ `trials-per-iter` 个 trial，输出落到需求 1 定义的目录。
2. WHEN 任意一轮评估器返回 `continue=False` THEN 系统 SHALL 立即停止后续轮次，写 `session_summary.json` 并退出 0。
3. WHEN `max_iters` 达到 THEN 系统 SHALL 停止并以 `stop_reason=max_iters` 写 `session_summary.json`。
4. WHEN 累计 wall-clock 耗时达到 `--max-runtime-min`（默认 90） THEN 系统 SHALL 完成当前 trial 后立即停止，`stop_reason=time_budget`。
5. IF 用户传入 `--dry-run` THEN 系统 SHALL 只生成 proposals.json 与 trial 目录骨架、不真正跑回测，便于检查搜索空间是否被正确加载。
6. IF 用户传入 `--resume <session_id>` THEN 系统 SHALL 从该 session 的最后一轮 `evaluations.json` 续跑，绝不覆盖既有 iter 目录。

---

### 需求 7 — 可观测性与可解释性

**用户故事：** 作为研究者，我希望随时能看到当前进展、最优 trial、与基线的差距，并知道每个改动来自哪一步。

#### 验收标准

1. WHEN 任意一轮完成 THEN 系统 SHALL 在 stdout 打印一段紧凑摘要（轮次、trial 数、本轮最优 score、相对上一轮的 Δ、相对 baseline 的 Δ、累计耗时），并把同样内容追加到 `<session_id>/progress.log`。
2. WHEN 闭环结束 THEN 系统 SHALL 生成一份 markdown 报告 `<session_id>/REPORT.md`，至少包含：搜索空间摘要、各轮 leaderboard 表、最优 trial 的参数 diff（vs 默认）、最优 trial 关键指标对比表（vs 当前固定池 baseline）、建议下一步。
3. WHEN 用户使用 `--print-leaderboard` 单独运行 THEN 系统 SHALL 仅打印当前 session 的累计 leaderboard，不触发任何回测/LLM 调用。
4. IF 任意 trial 改动了 `frozen_param` 区块（参数模式不可能；仅在代码 patch 模式下可能） THEN 系统 SHALL 在 `REPORT.md` 用醒目段落标注并在 stdout 打 ERROR 日志。

---

### 需求 8 — 安全边界与硬约束

**用户故事：** 作为运维者，我必须确认这套自迭代闭环不会造成任何真实交易、远端写入或污染既有产物。

#### 验收标准

1. WHEN 任意闭环代码运行 THEN 系统 SHALL 绝不 import `futu`、绝不调用 `OpenQuoteContext / OpenSecTradeContext`、绝不通过 `phase2/live/*` 任何模块，并由集成测试断言。
2. WHEN 代码 patch 模式启用 THEN 系统 SHALL 在 patch 应用前先备份原文件到 `iter_<k>/trial_<m>/strategy.bak.py`，patch 应用完后立即 `python3 -m py_compile` 与 `pytest phase2/strategy/tests/`，二者任一失败则丢弃 patch、回滚源文件、`trial_status=patch_invalid`。
3. WHEN LLM 模式启用且远端不可达 THEN 系统 SHALL 自动降级为本地规则模式继续运行，不抛异常退出。
4. WHEN 闭环结束 THEN 系统 SHALL **绝不**自动把 best trial 的参数写回 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py` 或任何 `phase2/strategy/config/*`；任何"采用 best trial"必须通过用户明确的后续动作（属于本闭环之外）。
5. IF 闭环检测到磁盘剩余空间不足（默认阈值 1 GB） THEN 系统 SHALL 立即停止当前轮次并以 `stop_reason=disk_low` 退出。

---

### 需求 9 — 测试与回归

**用户故事：** 作为开发者，我希望该闭环本身有覆盖良好的单测，避免迭代过程出错被掩盖。

#### 验收标准

1. WHEN 仓库 CI/手动跑 `python3 -m pytest phase2/strategy/tests/` THEN 系统 SHALL 包含至少以下新测：
   - `test_search_space_loader.py`：搜索空间合法/越界/`frozen_param` 拒绝；
   - `test_param_injection.py`：参数注入到 `Strategy` 实例后属性值完全一致；
   - `test_optimizer_local.py`：grid/random/local-perturb 三种模式至少各 1 用例，验证产出落在搜索空间内；
   - `test_evaluator_metrics.py`：用合成 `summary.json + equity_curve.csv` 验证 `sharpe_proxy / calmar_proxy / score / continue` 正确计算；
   - `test_session_io.py`：会话目录 / leaderboard / session_summary 写入与 schema。
2. WHEN 任意上面 5 个测试失败 THEN PR / 提交 SHALL 被拒（或开发者必须明确说明并修复后再提交）。
3. WHEN 用户运行 `python3 phase2/runners/run_phase2_strategy_self_optimize.py --dry-run --max-iters 2 --trials-per-iter 2` THEN 系统 SHALL 在 < 5 秒内完成 dry-run，不连任何外部服务、不读 vnpy 数据库。

---

## 边界与非目标

- **不做**：实盘自动调参、跨 phase（phase1/phase3）扩展、自动把最优参数写回策略文件、自动生成新的策略文件、向 OpenD/Futu 推送任何东西。
- **不依赖**：optuna/ray/skopt 等三方库（可选；缺失时降级为本地 grid/random）；新增 LLM 通道（复用现有 `vnpy_llm` / `KnotAgentRuntime`）。
- **延后**：滚动窗口（walk-forward）评估、组合参数与池构成联合优化、自动生成新因子—— 留给后续 plan。

---

## 成功标准

- 在固定池 6 标 5 年窗口下，闭环能在 ≤ 10 轮内给出 score 严格高于当前 baseline（A1 后 +26.85%）的至少一个 trial，且没有触发 `frozen_param` 越界或 OpenD 连接告警；
- `<session_id>/REPORT.md` 能让一位不参与开发的同事在 5 分钟内理解：本次跑了什么、最优是什么、与基线差距、下一步建议；
- `phase2/strategy/tests/` 全部测试 + 新增 5 个测试全绿；
- 整个闭环单次（10 轮 × 4 trial = 40 trial）在本机可在 ≤ 90 分钟内完成。
