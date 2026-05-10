# 需求文档

## 引言
本次功能旨在精简并重构 `quant workflow`，使其围绕一条更直接的量化执行准备链路运行。新流程仅保留并强化以下阶段：`candidate_prepare`、按任务类型区分的统一 `healthcheck`、基于候选输入的 `candidate_framework`、针对观察标的 `backtest`（含参数寻优）、以及基于证据的 `readiness` 校验。

本次改造的目标是让 `quant workflow` 更贴近“从候选到可执行证据”的主链路，减少暂时不需要的 planning、execution boundary、额外说明性产物等非核心逻辑，同时保持阶段边界清晰、输出可追踪、结果可供后续模拟盘或实盘阶段消费。

## 需求

### 需求 1：保留候选输入准备阶段

**用户故事：** 作为一名量化流程使用者，我希望保留 `candidate_prepare` 阶段，以便在后续筛选、回测和 readiness 判断前获得统一、可复用的候选输入。

#### 验收标准
1. WHEN 用户启动 `quant workflow` 且启用了候选准备 THEN 系统 SHALL 执行 `candidate_prepare` 阶段并生成标准化候选输入产物。
2. IF 本地已存在可复用的候选输入产物 THEN 系统 SHALL 支持复用该产物而不强制重复准备。
3. WHEN `candidate_prepare` 成功完成 THEN 系统 SHALL 记录输入来源、生成时间、覆盖市场和输出路径等可追踪信息。
4. IF `candidate_prepare` 失败或输出为空 THEN 系统 SHALL 在 workflow 结果中明确标记失败原因，并阻止依赖该产物的后续阶段进入成功状态。

### 需求 2：合并 `preflight` 与 `healthcheck` 为统一健康检查阶段

**用户故事：** 作为一名量化流程使用者，我希望 `preflight` 和 `healthcheck` 合并为一个统一的 `healthcheck` 阶段，以便按任务类型直接检查当前流程所需条件，减少重复判断与分散状态。

#### 验收标准
1. WHEN workflow 启动 THEN 系统 SHALL 仅执行一个统一的 `healthcheck` 阶段，而不再单独执行 `preflight`。
2. WHEN 用户指定任务类型为模拟盘 THEN 系统 SHALL 按模拟盘所需依赖检查候选输入、回测证据、模拟运行前提和相关本地状态。
3. WHEN 用户指定任务类型为实盘 THEN 系统 SHALL 按实盘所需依赖检查候选输入、回测证据、审批或风控前提、账户相关本地状态以及实盘阶段所需的更严格约束。
4. IF 某项检查缺失但不构成阻断 THEN 系统 SHALL 将该结果标记为 warning 并说明影响范围。
5. IF 某项关键检查缺失且会影响指定任务类型继续推进 THEN 系统 SHALL 将 `healthcheck` 标记为 blocked，并给出可执行的补救建议。
6. WHEN `healthcheck` 完成 THEN 系统 SHALL 输出结构化检查结果，至少包含检查项、任务类型、状态、阻断原因、警告信息和证据路径。

### 需求 3：从候选输入中筛选观察标并判断交易级别

**用户故事：** 作为一名量化研究者，我希望 `candidate_framework` 能从候选输入中筛选出候选观察标，并分析每个观察标更适合天级别还是分钟级别交易，以便后续回测和执行阶段采用更合适的策略粒度。

#### 验收标准
1. WHEN `candidate_framework` 处理候选输入 THEN 系统 SHALL 从输入中筛选出候选观察标列表。
2. WHEN 系统为观察标生成分析结果 THEN 系统 SHALL 为每个观察标给出推荐交易级别，至少支持“天级别”和“分钟级别”两类结论。
3. WHEN 系统输出观察标分析 THEN 系统 SHALL 同时输出支撑该级别判断的关键依据，例如流动性、波动特征、交易频率预期或输入中已有的结构化特征。
4. IF 某个候选标的数据不足以判断交易级别 THEN 系统 SHALL 将该标记为待补充分析，并在结果中说明缺失信息。
5. WHEN `candidate_framework` 完成 THEN 系统 SHALL 生成可供回测阶段直接消费的结构化观察标产物。

### 需求 4：对观察标执行历史回测并包含参数寻优

**用户故事：** 作为一名量化研究者，我希望 workflow 能针对观察标自动执行历史回测并寻找较优参数，以便获得进入下一阶段所需的历史证据。

#### 验收标准
1. WHEN `backtest` 阶段启动 THEN 系统 SHALL 针对 `candidate_framework` 输出的观察标逐一执行历史回测。
2. WHEN 观察标已被判定为不同交易级别 THEN 系统 SHALL 根据天级别或分钟级别采用对应的回测配置、数据粒度或参数空间。
3. WHEN 执行参数寻优 THEN 系统 SHALL 对每个观察标记录参数搜索范围、最优参数结果和对应关键绩效指标。
4. IF 某个观察标缺少足够历史数据或无法完成回测 THEN 系统 SHALL 将该观察标标记为回测失败或证据不足，并保留失败原因。
5. WHEN `backtest` 完成 THEN 系统 SHALL 输出结构化回测结果，至少包含观察标、交易级别、参数结果、关键绩效指标、结果摘要和产物路径。
6. IF workflow 被配置为仅生成证据而不触发真实执行 THEN 系统 SHALL 确保 `backtest` 只产生研究与验证产物，而不触发模拟盘或实盘动作。

### 需求 5：基于证据执行 readiness 校验

**用户故事：** 作为一名量化流程使用者，我希望 `readiness` 阶段能够基于健康检查、观察标分析和回测证据判断是否足以支撑进入下一阶段，以便避免在证据不足时推进到模拟盘或实盘。

#### 验收标准
1. WHEN `readiness` 阶段执行 THEN 系统 SHALL 综合 `healthcheck`、`candidate_framework` 和 `backtest` 的结果进行阶段就绪判断。
2. WHEN 当前任务类型为模拟盘 THEN 系统 SHALL 使用适用于模拟盘的最小证据标准进行 readiness 校验。
3. WHEN 当前任务类型为实盘 THEN 系统 SHALL 使用比模拟盘更严格的就绪标准进行 readiness 校验。
4. IF 关键证据缺失、质量不足或相互矛盾 THEN 系统 SHALL 将 readiness 标记为 blocked 或 warning，并列出未满足项。
5. WHEN readiness 校验完成 THEN 系统 SHALL 输出清晰的下一阶段建议，包括允许推进、建议补充项和阻断原因。
6. WHEN readiness 结果被持久化 THEN 系统 SHALL 保证结果能够追溯到对应的健康检查、观察标分析和回测证据。

### 需求 6：移除当前阶段中暂不需要的非核心逻辑

**用户故事：** 作为一名量化流程维护者，我希望暂时移除非核心逻辑，以便让 workflow 专注于候选准备、检查、观察标分析、回测和 readiness 主链路。

#### 验收标准
1. WHEN 新版 workflow 运行 THEN 系统 SHALL 仅围绕 `candidate_prepare`、`healthcheck`、`candidate_framework`、`backtest` 和 `readiness` 组织主流程。
2. IF 旧版 workflow 中存在 planning、execution boundary 或其他当前未被需求覆盖的附加步骤 THEN 系统 SHALL 默认不执行这些步骤。
3. WHEN 用户查看 workflow 输出 THEN 系统 SHALL 不再生成与当前需求无关的多余阶段状态或误导性的执行提示。
4. WHEN 非核心逻辑被停用 THEN 系统 SHALL 保持主流程阶段命名、状态表达和产物结构清晰一致。

## 范围说明
- 本次需求聚焦于 `quant workflow` 的流程重组与阶段职责重定义。
- 本次需求不包含模拟盘或实盘下单执行逻辑本身的实现。
- 本次需求不要求恢复或保留当前 workflow 中与主链路无关的说明型、规划型或执行边界型步骤。

## 成功标准
- `quant workflow` 的阶段收敛为用户指定的五个核心部分。
- 健康检查能够按模拟盘/实盘任务类型输出不同检查结果。
- 观察标产物可直接驱动回测阶段。
- 回测结果包含参数寻优证据。
- readiness 能基于证据明确判断是否足以进入下一阶段。