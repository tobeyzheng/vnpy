## vnpy_wheel_reinvent_audit task_list

> 对应计划：[task-item.md](/projects/vnpy/.codebuddy/plan/vnpy_wheel_reinvent_audit/task-item.md)
> 补充清单：[task8_cleanup_checklist.md](/projects/vnpy/.codebuddy/plan/vnpy_wheel_reinvent_audit/task8_cleanup_checklist.md)
>
> 本文件是 `vnpy_wheel_reinvent_audit` 的权威完成进度记录；计划范围、任务拆解、前置条件和风险说明以 `.codebuddy/plan/vnpy_wheel_reinvent_audit/` 为准。
> 本轮状态基于 2026-05-10 的仓库只读验收结果更新：未启动 Futu/OpenD，未运行 SIM/REAL，只检查了代码、文档、入口、产物与轻量编译结果。

- [ ] 1. 新分支初始化与清理基线（S0）
  - 状态：**部分完成**
  - 已验收：`classic-vnpy-native-rewrite` 分支主线已存在；`scripts/classic_multifactor/` 旧回测/循环入口已大幅清理；`scripts/` 顶层已基本收敛到主线相关入口；`scripts/classic_multifactor/run_llm_research.py` 的语法错误已修复，`python3 -m py_compile scripts/classic_multifactor/*.py` 不再被该文件阻塞
  - 未完成：`services/approval_gate/`、`services/backtest/`、`services/candidate_engine/`、`services/datahub/`、`services/decision_engine/`、`services/reporting/`、`services/scoring_engine/`、`services/signals/`、`services/watchlist_engine/` 等仍在；`state/candidates/`、`state/watchlists/` 与 `state/runs/` 仍保留大量 HK/A 股和历史运行产物
  - 阻塞：S0 的 services / state 清理收尾仍未完成

- [x] 2. 回测路径统一到 vnpy BacktestingEngine（S1）
  - 已验收：`scripts/classic_multifactor/cta_backtest.py` 已支持 `--optimize bf|ga` 并接入 `OptimizationSetting`；旧 `run_vnpy_cta_*` 扫参与分段入口已完成收敛/清理；存在 `state/runs/classic_multifactor/vnpy_cta_backtest_report.json`
  - 备注：未看到按计划命名的 `state/runs/reports/cta_backtest_baseline.json`，当前回测基线证据仍可继续补强

- [x] 3. 实盘数据面切换到 OmsEngine（S2）
  - 已验收：`services/futu_account/provider.py` 已优先读取 `MainEngine` / `OmsEngine` 的 account、position、order、trade 快照；OMS 过期时再降级到 SDK fallback

- [x] 4. 分钟级常驻入口 `run_intraday_loop.py`（S3a）
  - 已验收：`scripts/classic_multifactor/run_intraday_loop.py` 存在并通过轻量编译；`scripts/classic_multifactor/_base_runner.py` 会构建 `ExecutionGuardPipeline` 并把它挂到 strategy `execution_hook`

- [x] 5. 日线级调度入口 `run_daily_rebalance.py`（S3b）
  - 已验收：`scripts/classic_multifactor/run_daily_rebalance.py` 存在并通过轻量编译；支持 `loop_mode=daily`、`rebalance_time` 与 `target_positions`
  - 备注：文件内仍自述为 `skeleton`，完整 EOD 闭环验收按计划继续归入 Task 7

- [x] 6. 订单状态机收敛（S4）
  - 已验收：`services/trade_state/oms_recorder.py` 已将 `EVENT_ORDER` / `EVENT_TRADE` 写入 `OrderStateStore`；`ExecutionGuardPipeline` 已在提交后通过 `register_request` 绑定 `vt_orderid`

- [ ] 7. SIM 双跑对账与上线验收（S5 前半）
  - 状态：**进行中 / 等待周一开盘**
  - 已验收：`scripts/diff_dual_run.py` 与 `scripts/dual_run_preflight.py` 已存在；仓库内已有 `state/runs/reports/preflight_20260509.json` 样本，说明双跑前置检查链路已搭好
  - 未完成：当前仓库内没有可直接证明“连续 5 个交易日 rc=0、且 0 显著差异”的最终双跑验收记录；`preflight_20260509.json` 中也仍可见工作区锚点检查失败样本

- [ ] 8. 二轮清理与文档收尾（S5 后半）
  - 状态：**进行中**
  - 已完成：`scripts/run_us_live_task.py` 已切到 `scripts/classic_multifactor/run_intraday_loop.py` 转发路径；`services/trading_pipeline/__init__.py` 已移除 `LiveTradingPipeline` / `LiveTaskConfig` 默认导出；`docs/system_integration_guide.md` 与 `docs/project_operation_log.md` 已同步本轮入口迁移与静态修复记录
  - 未完成：`README.md` 顶部没有 migration notice；`task8_cleanup_checklist.md` 中列出的删除项尚未执行；`services/trading_pipeline/live_task.py` 仍保留为待清理历史实现
  - 前置条件：删除动作与更激进的清理仍应等待 Task 7 双跑通过并获得用户确认后再继续

### 当前执行焦点
- 当前状态：`vnpy_wheel_reinvent_audit` 已完成本轮静态修复与 live 顶层入口迁移，可判定为**核心 vnpy 原生化主线已基本落地，S1-S4 主体完成，S0 / Task 7 / Task 8 仍未完成**
- 当前阻塞：等待周一开盘补齐双跑验收；Task 8 删除项与 README/migration 收尾未完成
- 下一步：先完成双跑前置检查与周一开盘后的对账闭环，再决定是否执行 Task 8 删除清理与最终文档收尾
