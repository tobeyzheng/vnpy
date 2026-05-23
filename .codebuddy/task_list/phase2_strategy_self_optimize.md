# phase2 strategy self-optimization — 任务进度

> 本文件是 `.codebuddy/plan/phase2_strategy_self_optimize/` 的权威完成进度记录。
> 与 `task-item.md`（任务清单）一一对应；同名计划保持映射关系。

## 当前状态

- 阶段：**已交付 v0.1 闭环骨架**
- 全部 10 项任务：✅ 已完成
- 验证：phase2/strategy/tests 共 119 个 pytest 用例 100% 通过；
  其中本计划新增 6 个测试文件、44 条用例，全绿。
- 入口：`phase2/runners/run_phase2_strategy_self_optimize.py`
  - 默认搜索空间：`phase2/strategy/config/optimize_search_space.yaml`
  - 默认池：`phase2/strategy/config/pool_config_fixed.yaml`
  - 产物根目录：`state/runs/phase2_strategy_self_optimize/<session_id>/`

## 任务进度映射

| ID | 任务 | 状态 | 关键交付 |
| --- | --- | --- | --- |
| 1  | 模块骨架与会话目录约定 | ✅ | `phase2/optimize/{__init__,session,io_schemas}.py` + `state/runs/phase2_strategy_self_optimize/.gitignore` |
| 2  | 搜索空间加载器与 frozen_param 白名单 | ✅ | `optimize_search_space.yaml` + `phase2/optimize/search_space.py` |
| 3  | Trial 注入器（不修改策略源码，强制 force_live_submit=True） | ✅ | `phase2/optimize/trial_runner.py` + 引擎新增 `param_overrides` 钩子 |
| 4  | Optimizer subagent（grid/random/local-perturb/explore + LLM 钩子） | ✅ | `phase2/optimize/optimizer.py` |
| 5  | Evaluator subagent（多维评估 + continue/stop 决策） | ✅ | `phase2/optimize/evaluator.py` |
| 6  | Coordinator + CLI 入口 | ✅ | `phase2/optimize/coordinator.py` + `run_phase2_strategy_self_optimize.py` |
| 7  | 可选 LLM 通道（降级安全） | ✅ | `phase2/optimize/llm_bridge.py`（PHASE2_OPT_LLM_* env vars） |
| 8  | 报告生成器 + `--print-leaderboard` + progress.log | ✅ | `phase2/optimize/reporter.py` + CLI 子命令 |
| 9  | 安全护栏与硬隔离断言 | ✅ | `assert_no_live_imports` + `test_no_live_imports.py`（9 个隔离断言） |
| 10 | 单测与文档同步 | ✅ | 6 个 pytest 文件 + 此文件 + `docs/` 同步 |

## 下一阶段焦点

- 在真实 5 年区间用 `--max-iters 10 --trials-per-iter 4` 跑首次完整闭环；
  跑前需用户确认（涉及大产物落盘）。
- 根据首轮 REPORT.md 的 stop_reason 决定是否扩张 search_space 边界。
- 如需启用 LLM 通道，按 `docs/system_integration_guide.md` 设置
  `PHASE2_OPT_LLM_*` 环境变量后重启入口；任何 LLM 失败必须自动降级，
  绝不能让闭环空转。

## 隔离与安全约束（运行期不变量）

- `phase2.optimize` 整个包及其入口 `run_phase2_strategy_self_optimize.py`
  禁止导入 `futu`、`phase2.live.*`；由 9 个子进程级 pytest 守卫。
- 每个 trial 强制走 `force_live_submit=True` 的本地回测分支，永不连 OpenD。
- frozen_param 集合 `{LIVE_SUBMIT, _pool, max_orders_per_day}` 由
  `search_space.py` 与引擎 `param_overrides` 双重拒绝。
- 三个高风险参数硬上限固化在 `HARD_CEILINGS`：
  `pool_budget_pct ≤ 0.95`、`max_concurrent_holdings ≤ 8`、
  `stop_loss_pct ∈ [0.02, 0.10]`，YAML 试图突破即拒绝加载。
- 默认 stop_rules：`max_iters=10`、`patience=3`、`min_delta=0.5`、
  `max_runtime_min=90`；磁盘剩余 < 1 GB 触发 `stop_reason=disk_low`。
