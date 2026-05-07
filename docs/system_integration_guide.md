# 量化交易系统接入梳理

## 1. 交易主入口文件

### 港股交易入口
- 盘中主任务：`scripts/run_hk_sim_task.py`
- 盘后收盘：`scripts/run_hk_sim_close.py`
- 盘中盯市/估值：`scripts/run_hk_sim_mark.py`
- 港股 Knot 刷新：`scripts/run_knot_agent_hk_refresh.py`
- 港股盘中 Knot 决策：`scripts/run_intraday_knot_decision.py`

### 美股交易入口
- 盘中主任务：`scripts/run_us_sim_task.py`
- 盘后收盘：`scripts/run_us_sim_close.py`

## 2. 回测相关文件

### 单标的 CTA 回测
- Runner：`scripts/run_vnpy_backtest.py`
- 适配层：`services/backtest/vnpy_adapter.py`
- 策略桥接：`services/backtest/vnpy_strategy_bridge.py`
- 输出：`state/runs/vnpy_backtest_report.json`

### 多标的聚合回测
- Runner：`scripts/run_vnpy_portfolio_backtest.py`
- 输出：`state/runs/vnpy_portfolio_backtest_report.json`

### 共享现金池 / 组合级约束回测
- Runner：`scripts/run_shared_cash_portfolio_backtest.py`
- 引擎：`services/backtest/portfolio_engine.py`
- 输出：`state/runs/shared_cash_portfolio_backtest_report.json`

### 其他回测骨架
- Scaffold：`scripts/run_backtest_scaffold.py`
- 输出：`state/runs/backtest_scaffold_report.json`
- Alpha 尝试版：`scripts/run_vnpy_alpha_backtest.py`

## 3. Knot Agent 模块位置

### 评估适配层
- 主适配器：`services/evaluation_hub/adapters/knot_agent.py`
- Schema：`services/evaluation_hub/adapters/knot_agent_schema.py`
- Hub：`services/evaluation_hub/hub.py`
- 模型定义：`services/evaluation_hub/models.py`

### 运行时
- 本地运行时：`services/knot_runtime/runtime.py`
- 远程运行时：`services/knot_runtime/remote_runtime.py`

### 相关文档
- Prompt 设计：`docs/knot_agent_prompt_design.md`
- 远程批量集成：`docs/remote_knot_batch_integration.md`

## 4. 选股 / 策略入口

### 候选池入口
- 统一候选提供器：`services/strategy/candidate_provider.py`
- 静态候选文件：`state/runs/candidate_inputs.json`
- 动态候选文件：`state/runs/candidate_inputs.dynamic.json`

### 多因子与风控
- 多因子评分：`services/strategy/raw_score.py`
- 入场/离场 timing：`services/strategy/timing.py`
- 单标的风控：`services/strategy/risk_guard.py`
- 市场规则：`services/strategy/market_rules.py`
- 符号规范化：`services/strategy/symbols.py`

### 配置文件
- 评分配置：`configs/strategy/raw_score.yaml`
- 港股市场配置：`configs/strategy/markets/hk.yaml`
- 美股市场配置：`configs/strategy/markets/us.yaml`
- 评估源配置：`configs/evaluation/evaluation_sources.yaml`

## 5. 当前运行产物 / 状态文件

### Knot / Candidate
- `state/runs/remote_knot_batch_tasks.json`
- `state/runs/knot_agent_raw_output_hk.json`
- `state/runs/knot_agent_intraday_decision_hk.json`
- `state/runs/knot_agent_prompt_spec.json`
- `state/runs/knot_agent_validation_demo.json`
- `state/runs/hk_5w_candidate_refresh.json`

### 简报 / 汇总
- `state/runs/hk_final_brief.json`
- `state/runs/portfolio_brief.json`
- `state/runs/multi_market_brief.json`

### 回测输出
- `state/runs/vnpy_backtest_report.json`
- `state/runs/vnpy_portfolio_backtest_report.json`
- `state/runs/shared_cash_portfolio_backtest_report.json`

## 6. 当前系统结构概览

### 实盘/模拟主流程
1. 候选生成
   - 静态 candidate
   - 动态 candidate
2. 候选评估
   - 本地规则层
   - Knot agent 评估层
3. 多因子评分
   - `raw_score_v2`
4. timing 决策
   - 入场：`EntryTimingEngine`
   - 离场：`ExitTimingEngine`
5. 风控
   - 单标的风控：`RiskGuard`
   - 组合风控：`PortfolioRiskGuard`
6. 执行
   - 港股模拟交易
   - 美股模拟交易
7. 收盘与汇总
   - close / brief / portfolio brief

### 回测主流程
1. 历史数据导入
   - `scripts/import_futu_history_to_vnpy.py`
2. 单标的 CTA 回测
   - `scripts/run_vnpy_backtest.py`
3. 多标的聚合回测
   - `scripts/run_vnpy_portfolio_backtest.py`
4. 共享现金池组合回测
   - `scripts/run_shared_cash_portfolio_backtest.py`

## 7. 当前接入完成度

### 已完成
- 港股/美股交易入口明确
- Knot agent 模块位置明确
- 候选池入口明确
- vnpy 单标的回测跑通
- 多标的聚合回测跑通
- 共享现金池组合回测 v1 跑通

### 仍待继续接入
- 动态 candidate 历史化
- Knot agent 历史回放替身
- 更完整成本模型
- 真正组合再平衡
- 多市场货币/汇率处理
- 因子归因 / 策略归因

## 8. 给新系统接入的最小建议

### 如果只想先接交易主流程
优先接：
- `scripts/run_hk_sim_task.py`
- `scripts/run_us_sim_task.py`
- `services/strategy/*`
- `services/evaluation_hub/adapters/knot_agent.py`

### 如果只想先接回测
优先接：
- `scripts/import_futu_history_to_vnpy.py`
- `scripts/run_vnpy_backtest.py`
- `services/backtest/vnpy_adapter.py`
- `services/backtest/vnpy_strategy_bridge.py`

### 如果想接组合级回测
优先接：
- `services/backtest/portfolio_engine.py`
- `scripts/run_shared_cash_portfolio_backtest.py`
- `services/portfolio/risk.py`

## 9. 关键结论
- 港股交易入口：`scripts/run_hk_sim_task.py`
- 美股交易入口：`scripts/run_us_sim_task.py`
- 回测入口：`scripts/run_vnpy_backtest.py`
- 组合回测入口：`scripts/run_shared_cash_portfolio_backtest.py`
- KnotAgent 主模块：`services/evaluation_hub/adapters/knot_agent.py`
- 选股入口：`services/strategy/candidate_provider.py`
