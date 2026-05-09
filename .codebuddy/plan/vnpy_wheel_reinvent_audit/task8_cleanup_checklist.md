# Task 8 — 二轮清理与文档收尾 Checklist

> 范围：S5 后半（双跑 5 日通过 + 用户确认后才执行）。
> 本文件是 **只读盘点清单**，不执行任何删除。每项删除动作均需用户单独确认（项目规则 2）。
> 回滚凭证：`git tag classic-pre-vnpy-rewrite-v1`（旧分支冷藏）+ 当前分支删除前再打 `classic-vnpy-native-pre-task8` 局部回滚 tag。
>
> 生成时间：2026-05-09。基础 commit：`5eb8cb09`（HEAD）。
> 关联计划：`.codebuddy/plan/vnpy_wheel_reinvent_audit/task-item.md` Task 8。

---

## 0. 执行前置条件

- [ ] SIM 双跑 ≥ 5 个交易日全部 `rc=0`（`scripts/diff_dual_run.py` 输出 fail=0）。
- [ ] 用户回复 `确认执行 Task 8 清理`（项目规则 2）。
- [ ] 当前分支 `classic-vnpy-native-rewrite` 工作树干净 (`git status --porcelain` 为空)。
- [ ] 打回滚 tag：`git tag classic-vnpy-native-pre-task8`。

---

## 1. 待清理对象总览

按"风险等级"排序。每段都附 grep 反查命令，便于复核。

### 1.1 高优先级 — 计划明文要求删除（S0 1.4 推迟项 + Task 8 主项）

#### A. `services/trading_pipeline/live_task.py`（46.8 KB，43KB 手写轮询路径）
- **计划依据**：Task 8 第 1 条 + Task 1.4 暂留项。
- **当前依赖反查**：
  ```bash
  grep -rn "from services.trading_pipeline import" --include="*.py" .
  grep -rn "LiveTradingPipeline\|LiveTaskConfig" --include="*.py" .
  ```
- **当前结果**：
  - `services/trading_pipeline/__init__.py` Line 2 显式 `from .live_task import LiveTaskConfig, LiveTradingPipeline`。
  - `scripts/run_us_live_task.py`（白名单入口）当前实际**已切换**到 `run_intraday_loop.py` 路径？需要 grep 验证：
    ```bash
    grep -n "LiveTradingPipeline\|live_task" scripts/run_us_live_task.py
    ```
- **删除动作**：
  1. `git rm services/trading_pipeline/live_task.py`
  2. 修改 `services/trading_pipeline/__init__.py`：移除 live_task import；`__all__` 同步删 `LiveTaskConfig` / `LiveTradingPipeline`。
  3. 改写 `scripts/run_us_live_task.py`：转发到 `scripts/classic_multifactor/run_intraday_loop.py`，保留 `--live-submit` / `VNPY_LIVE_*` 三开关检查（项目规则 3）。
- **影响面**：
  - 删除后 `run_us_live_task.py` 必须可重新拉起；live_gate / risk_engine.live_guard 仍由 `execution_pipeline` 引用，无副作用。
- **回滚**：`git checkout classic-vnpy-native-pre-task8 -- services/trading_pipeline/live_task.py services/trading_pipeline/__init__.py scripts/run_us_live_task.py`。

#### B. `services/futu_account/` 轮询入口 dead code
- **计划依据**：Task 8 第 3 条。
- **背景**：S2 已把 `FutuAccountProvider` 改为 OmsEngine 只读适配器（commit `5e2e9461`）。SDK 轮询 4 个方法（`accinfo_query` / `position_list_query` / `order_list_query` / `deal_list_query`）按计划应保留作冷启动兜底，但其它显式轮询路径可删。
- **反查**：
  ```bash
  grep -n "accinfo_query\|position_list_query\|order_list_query\|deal_list_query" services/futu_account/*.py
  grep -rn "FutuSdkClient\." --include="*.py" .
  ```
- **建议保留**：`SDK fallback only` 冷启动路径（live_task.py 删除后由 OmsEngine 健康检查触发）。
- **建议删除**：仅在 live_task.py 路径中触发的轮询循环包装函数。
- **风险**：中。**必须先验证**：`run_intraday_loop.py` 重启时 OmsEngine 能从 EVENT_ACCOUNT/EVENT_POSITION 重建快照（任务 6 已实现）。

### 1.2 中优先级 — S0 Task 1.4 推迟的 services/ 子包清理

> ⚠️ 这部分原本属于 S0 1.4，因为用户曾"跳到 S2"被推迟。当前实际状态扫描结果如下，**与计划文档不一致**——以下子包仍存在依赖簇，**不能简单 `rm -rf`**。

#### C. 真正可直接删除（无任何主线依赖）

| 子包 | 大小 | 反查结果 | 删除策略 |
|---|---|---|---|
| `services/datahub/` | 仅 README | 无任何 import | 直删 |
| `services/backtest/` | 7 文件 | tests 不引用；scripts 不引用；execution 不引用 | **需先 grep 确认** `vnpy_alpha_bridge` 等是否还被回测脚本引用 |

反查命令：
```bash
grep -rn "from services.datahub\|services\.datahub" --include="*.py" .
grep -rn "from services.backtest\|services\.backtest" --include="*.py" .
```

#### D. 依赖簇 — 必须**整簇**评估

下列 5 个子包构成两个互联依赖簇，必须同进同退：

**簇 1：candidate / signals / scoring / watchlist 链路**
- `services/signals/` ← `services/candidate_engine/{providers, adapters}`
- `services/candidate_engine/` ← `services/reporting/demo_data.py`
- `services/scoring_engine/` 仅有 `practical_model.py` + `scorer.py`，无明显主线引用
- `services/watchlist_engine/` 主要服务 HK/A 股已删的入口

反查：
```bash
grep -rn "from services.signals\|services\.signals\." --include="*.py" .
grep -rn "from services.candidate_engine\|services\.candidate_engine\." --include="*.py" .
grep -rn "from services.scoring_engine\|services\.scoring_engine\." --include="*.py" .
grep -rn "from services.watchlist_engine\|services\.watchlist_engine\." --include="*.py" .
grep -rn "from services.reporting\|services\.reporting\." --include="*.py" .
```

- **当前主线（NVDA classic_multifactor）调用方**：均无（已切换到 `services/strategy/candidate_provider.py`）。
- **删除策略**：可整簇删除，但需先确认：
  - [ ] `services/strategy/candidate_provider.py` 不再 import 上述 4 个包；
  - [ ] `tests/` 中相关测试文件全部删除（参考计划 1.4：「同步删除 tests/ 下引用以上子包的测试文件」）。

**簇 2：decision_engine / approval_gate / paper_bridge 链路**
- `services/approval_gate/gate.py` → `services.decision_engine`
- `services/risk_engine/engine.py` → `services.decision_engine`（**主线引用！**）
- `execution/paper_bridge/bridge.py` → `services.decision_engine`
- `execution/{vnpy_bridge, futu_bridge, live_bridge}/bridge.py` → `execution.paper_bridge`

⚠️ **`services/risk_engine/engine.py` 引用了 `MarketDecision`**，但 `services/risk_engine/__init__.py` 实际导出的 `LiveRiskGuard` 在 `live_guard.py` 中（主线使用），engine.py 是 dead code 还是仍被某个入口拉起？

反查：
```bash
grep -n "from services.risk_engine import" --include="*.py" -r .
# 已知主线只用 LiveRiskGuard（来自 live_guard.py）
grep -n "RiskEngine\|risk_engine\.engine" --include="*.py" -r .
```

- **结论**：若 `services/risk_engine/engine.py` 不再被任何入口拉起，可一并删除，从而切断 decision_engine 的最后一个主线引用。
- **删除策略**：
  1. 先删 `services/risk_engine/engine.py`（如确认 dead code）。
  2. 再删 `services/approval_gate/`。
  3. 再删 `execution/paper_bridge/`、`execution/vnpy_bridge/`、`execution/futu_bridge/`、`execution/live_bridge/` 整体（这 4 个 bridge 是旧 PaperTradeIntent 流水线，已被 ExecutionGuardPipeline 取代）。
  4. 最后删 `services/decision_engine/`。

**风险**：高。建议在 Task 8 内**先做 grep 干预**确认每个文件的实际运行时引用，再分批删除，每删一个子包跑一次 `python -m py_compile services/**/*.py scripts/**/*.py` + `pytest tests/`。

#### E. `services/sim_account/` 不可删
- **原因**：`services/trading_pipeline/sim_task.py` Line 9 + `close_task.py` Line 9 均依赖。这两个 task 又是 `run_us_sim_task.py` / `run_us_sim_close.py`（白名单入口）的核心。
- **结论**：**与计划 1.4 文档冲突**——sim_account 必须保留。请用户确认是修订计划 1.4 文档，还是要把 sim 入口也一并下线（更激进的方案）。

### 1.3 低优先级 — `scripts/` 顶层第二轮扫查

按计划 Task 8 第 2 条："确认保留集合是需求 10.2 白名单子集"。

**当前 `scripts/` 顶层文件清单**：
- `diff_dual_run.py` — Task 7 新增，**保留**
- `futu_readonly_snapshot.py` — 白名单内
- `import_futu_history_to_vnpy.py` — 计划 1.3 注："S1 回测验证后再决定是否降级到 services/market_data/"
  - **建议**：S1 已完成，确认是否降级。
- `run_futu_sdk_probe.py` — 白名单内
- `run_healthcheck.py` — 白名单内
- `run_portfolio_brief.py` — 白名单内
- `run_us_futu_sim_session.py` — 白名单内
- `run_us_live_task.py` — 白名单内（待 1.1.A 改写）
- `run_us_sim_close.py` — 白名单内
- `run_us_sim_task.py` — 白名单内

**结论**：scripts/ 顶层基本清白，仅 `import_futu_history_to_vnpy.py` 需要决策。

### 1.4 文档收尾

按计划 Task 8 第 4-6 条：

- [ ] 更新 `docs/system_integration_guide.md`：
  - [ ] 删除"§17? MultiMarketSimTradingPipeline" 中关于 HK/A 股的描述（计划 1.3 已删 HK 入口）。
  - [ ] 新增 "§19 migration notice"：旧分支 `futu-dev-knot-setup` 冻结、tag `classic-pre-vnpy-rewrite-v1` 用于回退。
  - [ ] 新增 "§20 双入口架构图"：`run_intraday_loop.py`（分钟）/ `run_daily_rebalance.py`（日线）→ ExecutionGuardPipeline → CtaEngine。
  - [ ] 更新 "§? scripts / services 精简清单"。
  - [ ] 删除/更新所有提到 `paper_bridge` / `LiveTradingPipeline` 的段落。
- [ ] 在仓库根 `README.md` 顶部新增 migration notice 与旧分支回退指引。
- [ ] 打 tag `classic-vnpy-native-v1`（合并前最后一个 commit），合并需 `VNPY_LIVE_APPROVED=YES`。

---

## 2. 验证步骤（每步删除后执行）

```bash
# 1. 编译
python -m py_compile services/**/*.py scripts/**/*.py
# 2. 健康检查
python scripts/run_healthcheck.py
# 3. 单元测试
python -m pytest tests/
# 4. 双跑回归（对最近 1 个交易日 SIM 数据再跑一次 diff，确认指标无回退）
python scripts/diff_dual_run.py --run-a /path/legacy --run-b /path/new
```

**任何一步失败**：立即 `git checkout classic-vnpy-native-pre-task8 -- <path>`，并向用户汇报失败原因（项目规则 1）。

---

## 3. 推荐执行顺序（建议批次）

| Batch | 内容 | 验证 |
|---|---|---|
| T8-B1 | 删除 `services/datahub/`、`services/backtest/`（1.2-C 段） | py_compile + pytest |
| T8-B2 | 删除簇 1（signals + candidate_engine + scoring_engine + watchlist_engine + reporting + 对应 tests） | py_compile + pytest + healthcheck |
| T8-B3 | 删除簇 2（risk_engine.engine + approval_gate + 4 个 execution bridge + decision_engine + 对应 tests） | py_compile + pytest + healthcheck + scripts/run_us_sim_task.py 干跑 |
| T8-B4 | 删除 `services/trading_pipeline/live_task.py` + 改写 `scripts/run_us_live_task.py` | py_compile + pytest + 健康检查 + dry-run |
| T8-B5 | `services/futu_account/` 轮询 dead code 清理 | 全套测试 + 一次完整 SIM 双跑回归 |
| T8-B6 | 文档收尾（system_integration_guide / README / tag） | markdown lint + git push |

每个 Batch 之间要求用户单独确认（项目规则 2）。

---

## 4. 不删的文件（明确保留）

按计划 1.2 / 1.3 / 1.4 的"白名单"，叠加 S2-S5 新增物：

**scripts/classic_multifactor/**：
- `strategy.py` / `cta_backtest.py` / `model.py` / `data.py` / `fusion.py`
- `external.py` / `risk.py` / `account.py` / `minute_guard.py`
- `config_schema.py` / `run_llm_research.py`
- `execution_pipeline.py` ⭐（S3a 新增）
- `run_intraday_loop.py` ⭐（S3a 新增）
- `run_daily_rebalance.py` ⭐（S3b 新增）
- `run_vnpy_cta_backtest.py`（S1 入口）

**scripts/**：见 1.3 节白名单。

**services/**（保留集）：
- `execution_guard/` / `risk_engine/`（仅 `live_guard.py`+`models.py`+`__init__.py`，engine.py 待删）
- `strategy/` / `evaluation_hub/` / `knot_runtime/`
- `futu_account/`（S2 改造后只读适配器，部分 dead code 待清理）
- `futu_opend/` / `futu_sim_trade/` / `portfolio/` / `healthcheck/`
- `trading_pipeline/`（仅 sim_task / close_task，live_task 待删）
- `trade_state/`（含新增 `oms_recorder.py` ⭐）
- `common/`
- `sim_account/`（与计划 1.4 冲突项 — 必须保留，见 1.2-E）

---

## 5. 风险登记

| 风险 | 触发条件 | 应对 |
|---|---|---|
| 误删 sim_account 导致 sim 入口断链 | 严格按计划 1.4 文档执行 | 1.2-E 已标记，**禁止直删** |
| 删除 risk_engine.engine 后 LiveRiskGuard 受影响 | engine.py 与 live_guard.py 共享状态 | 删前完整 grep 确认；如有共享，先重构 |
| live_task.py 删除后 OmsEngine 冷启动失败 | EVENT_ACCOUNT 推送丢失 | 保留 SDK fallback；冷启动失败时降级到一次性 poll |
| 文档更新落后于代码 | 仅删代码不改 docs | 每个 Batch 同步更新 §相关章节 |
| 回退凭证失效 | 删除前未打 tag | 强制要求 0 段执行 `git tag classic-vnpy-native-pre-task8` |
