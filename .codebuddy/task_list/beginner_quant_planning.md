## beginner_quant_planning task_list

> 对应计划：[task-item.md](/projects/vnpy/.codebuddy/plan/beginner_quant_planning/task-item.md)
>
> 本文件是 `beginner_quant_planning` 的权威完成进度记录；计划范围、需求背景和任务拆解以 `.codebuddy/plan/beginner_quant_planning/` 为准。
> 若计划结构变化，应同步更新本文件的任务映射；若仅完成状态变化，优先更新本文件。

- [x] 1. 建立结构化产物模型与编排接口
- [x] 2. 实现本地能力注册表与阶段边界映射
- [x] 3. 实现公开量化研究采集与证据标准化（当前为本地 fallback + 标准化接口，真实 LLM 执行链路仍待继续增强）
- [x] 4. 实现新手友好的说明文档渲染器
- [x] 5. 实现选标框架与候选观察名单生成器
  - [x] 5.1 基础候选框架与观察名单分类
  - [x] 5.2 新增 `BeginnerCandidateSelector` 增强层
  - [x] 5.3 将增强候选框架接入 `workflow_service`
  - [x] 5.4 补充回归测试并完成验证
  - [x] 5.5 评估后决定可标记完成
- [x] 6. 实现个性化交易计划与风险预算生成器
- [x] 7. 实现策略验证、防过拟合检查与阶段升级闸门
- [x] 8. 实现安全边界、版本化落盘与运行汇总能力
- [x] 9. 在 `scripts/` 下新增自动化入口模块与一键工作流脚本
  - [x] 2026-05-10 补充仓库根目录 [run.sh](/projects/vnpy/run.sh) 作为统一安全入口；默认 preview-only，显式 `--confirm` 后才执行 `check / plan / research / sim-gate / live-gate / backtest / us-sim`
  - [x] 2026-05-10 补充候选输入前置准备链路：新增 [run_prepare_candidate_inputs.py](/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py) 与 `workflow_service` 的可选 `prepare_candidates` 集成
- [x] 10. 补充自动化测试、回归样例与现有文档更新

### 当前执行焦点
- 当前状态：`beginner_quant_planning` 本轮 1-10 项已全部完成；候选输入链路已进一步升级为模块化 `CandidateScoringService` + `HybridCandidateGenerationService` + `CandidateMarketDataService` / `CandidateKnotEnrichmentService`，且默认 `knot_runtime` 已切到 `auto`，候选准备 / workflow / artifact / renderer 等对外时间戳已统一为北京时间；最新补充了候选 prepare 产物的非有限数值清洗，Futu snapshot 或其他 enrich 源中的 `NaN` / `Infinity` 现在统一写为 `null`，避免下游继续读取到非法 JSON
- 下一步：如需继续增强，可优先补真实 market regime / universe 数据源、按 `symbol` 精细 merge 规则、Knot 批量调度/缓存，以及 candidate scoring 在更多 workflow / research 场景下的直接复用接口
