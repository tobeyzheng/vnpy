## quant-workflow-refactor task_list

> 对应计划：[task-item.md](/projects/vnpy/.codebuddy/plan/quant-workflow-refactor/task-item.md)
>
> 本文件是 `quant-workflow-refactor` 的权威完成进度记录；计划范围、需求背景和任务拆解以 `.codebuddy/plan/quant-workflow-refactor/` 为准。
> 若计划结构变化，应同步更新本文件的任务映射；若仅完成状态变化，优先更新本文件。

- [x] 1. 重构 `quant workflow` 主流程阶段定义与调度入口
- [x] 2. 保留并规范 `candidate_prepare` 作为首段输入准备能力
- [x] 3. 合并 `preflight` 与 `healthcheck` 并按任务类型实现差异化检查
- [x] 4. 重写 `candidate_framework`，输出观察标及交易级别判断
- [x] 5. 实现面向观察标的 `backtest` 阶段与参数寻优编排
- [x] 6. 实现基于证据的 `readiness` 校验阶段
- [x] 7. 同步更新 workflow 相关文档与历史记录

### 当前执行焦点
- 当前状态：`quant-workflow-refactor` 1-7 项已全部完成；`quant_workflow` 已收敛为 `candidate_prepare`（可选）→ `healthcheck` → `candidate_framework` → `backtest` → `readiness` 五阶段主链路，旧的 `preflight / research / planning / execution_boundary` 默认不再执行
- 验证结果：本轮已补充“显式开启真实回测/自动扫参”相关代码与测试；待完成本轮回归后同步刷新这里的通过数
- 当前补充能力：`backtest` 默认仍支持复用本地 evidence，但在显式开启 `auto_execute_backtests` 时，允许 workflow 为 observation target 自动拉取历史数据、执行真实 vn.py CTA backtest 与参数搜索，并把结果回写到 `state/runs/classic_multifactor/`
