# 实施计划

- [ ] 1. 重构 `quant workflow` 主流程阶段定义与调度入口
  - 修改 `scripts/quant_workflow/workflow_service.py` 中的阶段编排，移除 `planning`、`execution_boundary` 等非核心步骤，仅保留 `candidate_prepare`、`healthcheck`、`candidate_framework`、`backtest`、`readiness`
  - 调整步骤解析与 workflow 汇总输出，确保新阶段顺序、状态和产物字段一致
  - 更新 `scripts/quant_workflow/run_quant_workflow.py` 的参数和摘要输出，使其与新流程术语和阶段保持一致
  - _需求：6.1、6.2、6.3、6.4_

- [ ] 2. 保留并规范 `candidate_prepare` 作为首段输入准备能力
  - 复用或调整现有候选输入准备服务，保证可按开关执行并支持复用已有候选产物
  - 为准备结果补齐来源、生成时间、覆盖市场、输出路径和失败原因等结构化元数据
  - 在 workflow 阶段结果中明确 `candidate_prepare` 对后续步骤的阻断关系
  - _需求：1.1、1.2、1.3、1.4_

- [ ] 3. 合并 `preflight` 与 `healthcheck` 并按任务类型实现差异化检查
  - 将现有 `preflight` 与健康检查逻辑合并为单一 `healthcheck` 阶段
  - 为模拟盘与实盘定义不同检查项集合、阻断规则、warning 规则和证据路径输出
  - 在结果模型中统一表达检查项、任务类型、状态、阻断原因、警告信息和补救建议
  - 为关键缺失场景补充单元测试
  - _需求：2.1、2.2、2.3、2.4、2.5、2.6_

- [ ] 4. 重写 `candidate_framework`，输出观察标及交易级别判断
  - 基于候选输入筛选观察标，定义统一的观察标结果结构
  - 为每个观察标增加“天级别 / 分钟级别 / 待补充分析”的判定逻辑与依据字段
  - 生成可直接供 `backtest` 消费的结构化观察标产物，并覆盖数据不足场景
  - 为观察标筛选和级别判定补充测试
  - _需求：3.1、3.2、3.3、3.4、3.5_

- [ ] 5. 实现面向观察标的 `backtest` 阶段与参数寻优编排
  - 新增或改造 `backtest` 阶段，使其按观察标逐一执行历史回测
  - 根据交易级别切换不同数据粒度、回测配置和参数搜索空间
  - 记录参数搜索范围、最优参数、关键绩效指标、失败原因和产物路径
  - 确保该阶段只生成研究/验证证据，不触发模拟盘或实盘执行
  - 为回测成功、数据不足、参数寻优输出等场景补充测试
  - _需求：4.1、4.2、4.3、4.4、4.5、4.6_

- [ ] 6. 实现基于证据的 `readiness` 校验阶段
  - 基于 `healthcheck`、观察标分析结果和回测结果构建 readiness 输入模型
  - 为模拟盘与实盘定义不同的最小证据标准、warning/blocked 规则和下一阶段建议
  - 持久化 readiness 结果，并建立到健康检查、观察标产物和回测产物的追踪关联
  - 为 readiness 的通过、警告、阻断场景补充测试
  - _需求：5.1、5.2、5.3、5.4、5.5、5.6_

- [ ] 7. 同步更新 workflow 相关文档与历史记录
  - 更新 [system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md) 中关于 `quant_workflow` 阶段、入口、默认行为和产物路径的描述
  - 视改动范围同步更新 [adaptive_quant_engine_design.md](/projects/vnpy/docs/adaptive_quant_engine_design.md) 中与 workflow 阶段职责相关的内容
  - 在 [project_operation_log.md](/projects/vnpy/docs/project_operation_log.md) 记录本次流程重构的范围、关键文件与影响摘要
  - _需求：6.1、6.4_