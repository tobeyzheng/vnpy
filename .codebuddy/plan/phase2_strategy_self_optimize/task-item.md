# 实施计划 — phase2 策略自迭代优化（Optimizer + Evaluator 双 subagent 闭环）

> 对应需求文档：`.codebuddy/plan/phase2_strategy_self_optimize/requirements.md`
> 权威进度：`.codebuddy/task_list/phase2_strategy_self_optimize.md`

- [ ] 1. 搭建模块骨架与会话目录约定
   - 在 `phase2/optimize/` 下新建包：`__init__.py`、`session.py`（`SessionPaths` / `session_id` 生成 / 目录创建）、`io_schemas.py`（proposals/evaluations/leaderboard/session_summary 的 dataclass 与序列化）。
   - 在 `state/runs/phase2_strategy_self_optimize/` 写一份 `.gitignore` 占位，确保产物隔离不污染 `phase2_multi_backtest`。
   - _需求：1.1、1.2、1.3、1.4、1.5_

- [ ] 2. 实现搜索空间加载器与 frozen_param 白名单校验
   - 新增 `phase2/strategy/config/optimize_search_space.yaml`（覆盖 MA/RSI 窗口与阈值、止损止盈、`max_concurrent_holdings`、`pool_budget_pct` 等 safe_param，给出默认值/范围/类型）。
   - 在 `phase2/optimize/search_space.py` 实现 `load(path)`：解析 YAML、对照 `FROZEN_PARAMS = {"LIVE_SUBMIT", "_pool", "max_orders_per_day", ...}` 拒绝、对 `pool_budget_pct/max_concurrent_holdings/stop_loss_pct` 校验硬上限。
   - 提供 `validate_proposal(proposal, space)`，越界返回 `ValueError`（不静默 clamp）。
   - _需求：2.1、2.2、2.3、2.5_

- [ ] 3. 实现 Trial 注入器（不修改策略源文件）
   - 新增 `phase2/optimize/trial_runner.py`：复用 `phase2/runners/run_phase2_multi_backtest.py` 的 `_load_strategy` 入口，在 `strategy.initialize()` 后用 `setattr` 把 `params.yaml` 覆盖到实例上；落 `params.yaml` 与 `applied_params.json`；注入后断言属性值与 yaml 一致，不一致写 `trial_status=injection_mismatch`。
   - 单 trial 异常捕获、`error.log` 落盘、`trial_status` 字段统一（`ok/failed/injection_mismatch/skipped`），不中断整轮。
   - 拒绝 `--respect-live-submit`；强制 `force_live_submit=True` 的纯回测路径。
   - _需求：3.1、3.2、3.3、3.4、3.5_

- [ ] 4. 实现 Optimizer subagent（本地规则）
   - 新增 `phase2/optimize/optimizer.py`：实现 `propose(iter_idx, history, space, n_trials)`，支持 `grid / random / local-perturb / explore` 四种 origin；首轮以 trial_0=默认参数为基线 + N-1 个变体；后续轮以上轮 `top_k` 为父代做小步长扰动 + 1 个 explore。
   - 每个 trial 写 `proposal.json`（`trial_id / origin / parent_trial / changed_params / rationale / predicted_metric_direction`）。
   - 预留 `--llm-optimizer` 接口（任务 7 接入），缺失/失败时降级 `random`。
   - _需求：4.1、4.2、4.3、4.5_

- [ ] 5. 实现 Evaluator subagent（多维评估）
   - 新增 `phase2/optimize/evaluator.py`：从 `summary.json` + `equity_curve.csv` 推算 `sharpe_proxy / calmar_proxy / turnover_proxy / exposure_days / win_rate`，计算复合分（默认权重 0.5/0.3/-0.2，支持 `--score-formula` YAML 注入）。
   - 输出 `evaluations.json`（含 ranking、`top_k`、过拟合/无效/超重仓诊断）与 `leaderboard.csv`；`summary.json` 缺失时该 trial `score=null, status=missing_summary`。
   - 给出 `continue/stop` 决策（`min_delta / patience / max_iters / max_runtime_min` 四重组合）。
   - _需求：5.1、5.2、5.3、5.4、5.6_

- [ ] 6. 实现 Coordinator 与 CLI 入口
   - 新增 `phase2/runners/run_phase2_strategy_self_optimize.py`：组装 Session → Optimizer → TrialRunner（串行）→ Evaluator → 终止判定循环。
   - CLI 参数：`--max-iters / --trials-per-iter / --start / --end / --pool-config / --rate / --slippage / --init-cash / --max-runtime-min / --dry-run / --resume / --score-formula / --llm-optimizer / --llm-evaluator`。
   - 终止条件：评估器主动 stop / `max_iters` / `time_budget` / 磁盘剩余 < 1 GB / `optimizer_dry`；写 `session_summary.json`（含 `best_trial_path` 软链/manifest）与 `progress.log`。
   - `--dry-run` 仅产出 `proposals.json` 与 trial 目录骨架，不跑回测；`--resume` 续跑且不覆盖既有 iter。
   - _需求：6.1、6.2、6.3、6.4、6.5、6.6、8.5_

- [ ] 7. 接入可选 LLM 通道（降级安全）
   - 在 `phase2/optimize/llm_bridge.py` 复用 `vnpy_llm.llm_client.OpenAICompatibleClient`（或现有 `KnotAgentRuntime`），实现 `llm_propose(history, space, n) → list[proposal]` 与 `llm_review(evaluations) → notes`，全部 JSON-only + schema 校验。
   - schema 校验失败、远端不可达、超时 → 自动降级为本地 `random` / 跳过 LLM 附注；不抛异常退出整个 session。
   - LLM 附注仅写入 `evaluations.json.llm_notes`，绝不改写 ranking 或 `stop_reason`。
   - _需求：4.4、5.5、8.3_

- [ ] 8. 落地报告生成器与产物可观测
   - 在 Coordinator 结束时调用 `phase2/optimize/reporter.py` 生成 `<session_id>/REPORT.md`：搜索空间摘要、各轮 leaderboard、最优 trial 参数 diff（vs 默认）、最优 trial vs 固定池 baseline 指标对比、下一步建议。
   - 实现 `--print-leaderboard <session_id>` 子命令：仅打印累计 leaderboard，不触发回测/LLM。
   - 每轮在 stdout 与 `progress.log` 输出紧凑摘要（轮次 / 本轮最优 score / Δ vs 上一轮 / Δ vs baseline / 累计耗时）。
   - _需求：7.1、7.2、7.3、7.4_

- [ ] 9. 安全护栏与硬隔离断言
   - 在 `phase2/optimize/__init__.py` 内增加导入守卫：`import sys; assert "futu" not in sys.modules`（或集成测试断言整个 `phase2.optimize` 包导入链不触达 `futu` / `phase2.live.*`）。
   - 集成测试 `test_no_live_imports.py`：`importlib` 加载 `phase2.optimize` 与 runner，断言无 `futu`/`OpenQuoteContext`/`OpenSecTradeContext`/`phase2.live` 引入。
   - 代码 patch 模式 stub：仅保留接口与"先备份再 `py_compile`+ pytest，失败回滚"的骨架（默认关闭，不实现完整 patch DSL，避免本期范围扩张）。
   - 闭环结束时**绝不**写回 `phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py` 与 `phase2/strategy/config/*`。
   - _需求：8.1、8.2、8.4_

- [ ] 10. 单测与文档同步
   - 新增 `phase2/strategy/tests/test_search_space_loader.py`、`test_param_injection.py`、`test_optimizer_local.py`、`test_evaluator_metrics.py`、`test_session_io.py`，覆盖需求 9.1 列出的 5 类用例；外加 `test_no_live_imports.py`（任务 9）。
   - 跑 `python3 -m pytest phase2/strategy/tests/` 全绿；跑一次 `--dry-run --max-iters 2 --trials-per-iter 2` 在 5 秒内完成。
   - 更新 `docs/system_integration_guide.md`（新增"phase2 策略自迭代优化闭环"章节：入口、产物路径、安全边界、降级策略）与 `docs/project_operation_log.md`（追加本次范围、关键文件、影响摘要）。
   - 同步创建 `.codebuddy/task_list/phase2_strategy_self_optimize.md` 作为权威进度文件。
   - _需求：9.1、9.2、9.3_
