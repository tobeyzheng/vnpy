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
- 验证结果：已执行 `python3 -m pytest /projects/vnpy/tests/test_beginner_quant_workflow.py -q`，结果为 `26 passed, 1 warning`
- 下一步：如需继续增强，可优先把当前 evidence-only `backtest` 阶段扩展为“可显式确认后触发真实回测”的可执行模式，并补充更多分钟级/港股场景的本地证据样例
