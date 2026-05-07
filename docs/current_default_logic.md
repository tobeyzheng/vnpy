# Current Default Stock Selection and Order Logic

## 1. 当前默认选股逻辑
1. 候选输入来源：
   - 优先读取 `state/runs/candidate_inputs.json`（如果存在）
   - 若不存在，则回退到 `DemoCandidateProvider()`
2. 读取后的 `CandidateInput` 会通过 adapter 转成 `Candidate`。
3. 当前默认评分：按 market 使用 `configs/scoring/score_model.yaml` 中的 market 权重，结合 `build_factor_map()` 生成置信度。
4. 当前默认 rank：`CandidateRanker().top_n(..., n=5)`，取 Top5。
5. 当前默认观察池：固定观察池（`configs/watchlists/*.yaml`） + Top候选并集，再和上一交易日状态做 reconcile，得到新增/保留/升级/降级/待移出。
6. 当前默认决策：`DecisionEngine().decide(market, top_candidates)` 输出 action / regime / summary。

## 2. 当前默认下单逻辑
1. 系统默认不是自动实盘。
2. ApprovalGate 默认建议使用 `research` 或 `paper`，其中：
   - `research`：只出报告，不生成有效执行链
   - `paper`：允许生成 paper intents 和 draft
   - `semi_auto`：需要人工审批
   - `blocked`：完全禁止执行
3. RiskEngine 默认先做基础过滤：
   - 低置信度信号阻断
   - 高相关/重复提醒
   - 同时买卖提醒
   - 单轮建议过多提醒
4. 只有 ApprovalGate.allowed 且 RiskEngine.allowed 时，才会：
   - `PaperTradeBridge().build_intents()`
   - `VnpySignalBridge().build_drafts()`
   - `FutuPaperBridge().build_drafts()`
5. 当前 draft 的含义是：
   - 只是结构化下单草案
   - 不直接 submit 到实盘
6. 当前系统实际状态：
   - Futu SDK + OpenD + 模拟账户只读查询已打通
   - 交易流程已打通到 paper/draft 阶段
   - 未开启 live submit

## 3. 当前默认执行边界
- 默认是研究/模拟优先，不做自动实盘
- 默认要保留审批闸门和风险过滤
- 默认先完善只读链、quote snapshot、paper闭环，再考虑 semi-auto
