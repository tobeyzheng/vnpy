## 项目操作记录

### 记录规则

- 记录对框架、需求、入口、规则、运行产物结构、阶段边界有明显影响的改动。
- 每条记录尽量包含日期、变更范围、涉及文件和影响摘要。
- 记录保持高层摘要，避免粘贴冗长实现细节。
- 记录中避免出现真实账户金额、完整账号、密钥、令牌等敏感信息。

### 历史记录

- **2026-05-10**：完成 US live 顶层入口迁移与静态修复收口
  - **代码文件**：[run_us_live_task.py](/projects/vnpy/scripts/run_us_live_task.py)、[__init__.py](/projects/vnpy/services/trading_pipeline/__init__.py)、[run_llm_research.py](/projects/vnpy/scripts/classic_multifactor/run_llm_research.py)
  - **文档/进度文件**：[system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)、[task-item.md](/projects/vnpy/.codebuddy/plan/vnpy_wheel_reinvent_audit/task-item.md)、[vnpy_wheel_reinvent_audit.md](/projects/vnpy/.codebuddy/task_list/vnpy_wheel_reinvent_audit.md)
  - **影响摘要**：`scripts/run_us_live_task.py` 不再直接走旧 `LiveTradingPipeline`，而是转发到 `scripts/classic_multifactor/run_intraday_loop.py`；`services/trading_pipeline/__init__.py` 已移除旧 live 导出，减少新的默认调用面；`scripts/classic_multifactor/run_llm_research.py` 的 schema preset 结构已修复，经典多因子脚本的静态编译阻塞已收敛；相关 docs / plan / task_list 已在同轮同步更新

- **2026-05-10**：新增项目级协作治理规则与文档记录约束
  - **规则文件**：新增 [project-collaboration-governance.mdc](/projects/vnpy/.codebuddy/rules/project-collaboration-governance.mdc)
  - **文档文件**：更新 [system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)，新增本记录文档
  - **影响摘要**：后续需及时更新 plan markdown 状态、同步维护历史操作记录、避免在输出中暴露真实账户金额等敏感信息，并在框架/需求/入口改动时同步更新 `docs/` 文档

- **2026-05-10**：优化 `plan` 与 `task_list` 的职责分离约束
  - **规则文件**：更新 [project-collaboration-governance.mdc](/projects/vnpy/.codebuddy/rules/project-collaboration-governance.mdc)
  - **计划/进度文件**：更新 [task-item.md](/projects/vnpy/.codebuddy/plan/beginner_quant_planning/task-item.md)、[beginner_quant_planning.md](/projects/vnpy/.codebuddy/task_list/beginner_quant_planning.md)、[task-item.md](/projects/vnpy/.codebuddy/plan/vnpy_wheel_reinvent_audit/task-item.md)、[task8_cleanup_checklist.md](/projects/vnpy/.codebuddy/plan/vnpy_wheel_reinvent_audit/task8_cleanup_checklist.md)，新增 [vnpy_wheel_reinvent_audit.md](/projects/vnpy/.codebuddy/task_list/vnpy_wheel_reinvent_audit.md)
  - **影响摘要**：明确 `.codebuddy/plan/` 维护计划背景、任务拆解和执行顺序，`.codebuddy/task_list/` 维护权威完成进度；同名计划应一一对应，后续完成状态以 `task_list` 为准
