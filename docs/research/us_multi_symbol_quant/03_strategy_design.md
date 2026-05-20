# 03 多标的策略方案

> ⚠️ **非可执行声明**：本文为研究/设计文档，**不含可直接运行代码**。所有策略思路、参数、伪代码片段仅作设计示意，**不构成可执行的策略实现**。
>
> 落地约束（与项目规则一致）：
> - 任何代码落地必须通过 `.codebuddy/plan/` 流程发起新计划，并遵守"实际执行前确认规则"；
> - 不修改 [us_nvda_1d_strategy_multifactor.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_multifactor.py) 等现有策略源码；
> - 实盘必须满足项目硬开关、人工审批、对账、风控、订单幂等要求；本文不涉及真实账户金额，所有资金量级使用脱敏占位符（"账户净资产 NAV"）。
>
> 关联：[00 汇总入口](./00_index.md) ｜ [01 指标调研](./01_indicator_research.md) ｜ [02 平台能力评估](./02_futu_multi_symbol_capability.md)

## 1. 目标与约束

### 1.1 设计目标

| 维度 | 目标 |
|------|------|
| 战略目标 | 把现有"NVDA 单标的多因子"扩展为"美股大盘流动股多标的轮动 / 选股" |
| 评估目标 | 在长样本（≥3 年日线）上跑赢 SPY 等权基准、且最大回撤可控（< 单标基线） |
| 风险目标 | 单标的爆仓不影响组合存活；个股黑天鹅在仓位上被自然稀释 |
| 工程目标 | 同一套规则可在 Futu 平台回测与实盘运行；与本地回测形成"双轨对账" |

### 1.2 硬约束

1. **平台限制**：单策略最多 50 个运行标的（[02 §5.1](./02_futu_multi_symbol_capability.md)）。
2. **数据范围**：仅日线（阶段 ②），小时线作为阶段 ③+ 选项；不引入财报基本面、不引入期权/衍生品。
3. **资金范围**：使用现金购买力，不使用融资融券；不卖空（阶段 ②）。
4. **指标范围**：仅使用 [01 §10 短名单](./01_indicator_research.md) 中的核心指标（MA / EMA / MACD / RSI / ATR / BOLL / OBV / ADX）+ 横截面动量。
5. **业务约束**：不修改现有策略源码；新策略以"扩展"形式存在，复用现有份制思想。

### 1.3 非目标（明确剔除项）

- ❌ 高频/Tick 级策略
- ❌ 跨市场（US+HK）混合（阶段 ②）
- ❌ 期权 / 期货 / 牛熊证
- ❌ 财报事件驱动 / 新闻情绪驱动
- ❌ 机器学习 / 深度模型（保持白盒可解释）

## 2. 标的池设计

### 2.1 池的来源

阶段 ② 采用**固定股票池**，由人工 + 规则共同确定，存在 `pool_config.yaml`（落地阶段才创建，本文仅作设计示意）：

| 来源 | 候选 | 数量量级 |
|------|------|----------|
| 大盘宽基代表 | SPY、QQQ 成分中的高市值流动股 | 头部 30–50 只 |
| 行业代表 | 半导体（NVDA / AMD / TSM）、软件（MSFT / GOOG）、消费（AAPL / AMZN）等 6–8 个行业各取 2–3 只 | 12–24 只 |
| 排除 | OTC、ADR 二级、停牌中、IPO 不满 1 年、近 60 日日均成交额 < 阈值 | — |

阶段 ② 目标池规模 **N = 10–20**；阶段 ③ 扩到 **N = 30–50**（用满平台上限）。

### 2.2 标的过滤规则（每日开盘前快照式）

| 过滤项 | 规则 | 备注 |
|--------|------|------|
| 流动性 | 60 日日均成交额（ADV60） ≥ 阈值 | 阈值在 `pool_config.yaml` 中配置，避免硬编码 |
| 价格区间 | 5 ≤ price ≤ 上限 | 排除仙股与极高价（保证份制可分股） |
| 波动率 | ATR% ≤ 上限 | 排除极端高波动（如生物科技小盘） |
| 事件冻结 | 财报前后 ±2 个交易日不入选 | 业务层维护财报日历（落地时再实现） |

### 2.3 池更新频率

- **阶段 ②**：每月或每季度人工 review，运行期内**池冻结**。
- **阶段 ③**：每周或每月按规则自动 rebalance（流动性、波动率、行业分布）。

## 3. 信号体系（4 因子 + 横截面排序）

### 3.1 因子框架（与 [01 §10](./01_indicator_research.md) 对齐）

```text
Score(symbol) = w_trend  · F_trend(symbol)
              + w_mom    · F_momentum(symbol)
              + w_vol    · F_volatility_filter(symbol)
              + w_volume · F_volume(symbol)
```

| 因子 | 指标 | 输出 | Futu API（行号引用 [01](./01_indicator_research.md)） |
|------|------|------|--------------------------------------------------------|
| **F_trend** 趋势 | EMA12 vs EMA26 + ADX(14) | EMA 多头排列且 ADX > 25 → 1；否则 0 | `is_ema_bullish_alignment` / 麦语言 ADX |
| **F_momentum** 动量 | RSI(14) 从超卖回升 + 12-1 月收益率排名 | RSI 回升给 +1；横截面动量 top 30% → +1 | `rsi()` / 业务层排序 |
| **F_volatility** 波动率过滤 | ATR% ≤ 阈值 | 通过给 1，否则 0（**作为门控因子，不计入加分**） | `atr_atr` |
| **F_volume** 量能 | 当日成交量 / MA20(volume) ≥ 1.2 | 通过给 1，否则 0 | 业务层（`vol_ratio` 与现有策略一致） |

权重默认值（设计示意，实际待回测调参）：`w_trend = 0.4 / w_mom = 0.4 / w_volume = 0.2`；`F_volatility` 作为门控不参与加权。

### 3.2 入场信号（阶段 ② 固定池）

对池中每个 symbol 独立判断，满足以下**全部条件**触发买入：

1. **趋势对齐**：EMA12 > EMA26 且 ADX(14) > 25；
2. **动量确认（任一）**：
   - RSI(14) 从超卖区（≤ 35）回升 — 复用现有策略的 `is_rsi_oversold_recovering` 思路；
   - 或 EMA 金叉发生在最近 3 根 K 线内；
3. **波动率门控**：ATR(14) / Close ≤ 上限阈值；
4. **量能确认**：vol_ratio ≥ 1.2；
5. **位置过滤**：Close ≤ 60 日最高价 × 0.95（不在历史高位接飞刀）；
6. **组合层风控通过**（详见 §5）。

### 3.3 横截面排序（阶段 ③）

阶段 ③ 在阶段 ② 基础上增加"全池排序选 top-K"层：

```text
# 设计示意，非可执行
1. 对池中每个 symbol 计算 Score(symbol)
2. 过滤掉 F_volatility = 0（波动率门控不通过）的 symbol
3. 按 Score 降序，取 top-K（K = 持仓数上限）
4. 与当前持仓做差集：
   - 在持仓但跌出 top-K → 卖出
   - 不在持仓但进入 top-K → 买入（等权或按 Score 加权）
5. 通过组合层风控后下单
```

**关键设计决策**：
- 排序周期 = 每日收盘后；
- 调仓最低间隔（避免频繁换手）：单标的从入场到卖出最少持有 N 个交易日（默认 5）；
- 调仓上限：单日换仓不超过组合的 30%（避免市场冲击）。

### 3.4 出场信号

| 出场类型 | 触发条件 | 优先级 |
|----------|----------|--------|
| **硬止损** | Close ≤ entry_price × (1 - stop_loss_pct) | 最高 |
| **移动止盈** | 持仓最高价回撤 ≥ trailing_drawdown_pct（激活阈值：浮盈 ≥ take_profit_pct） | 高 |
| **趋势反转** | EMA12 < EMA26 且 ADX 下行；或 MACD 死叉 | 中 |
| **跌出 top-K**（阶段 ③） | 横截面 Score 跌出选股名单 | 中 |
| **波动率突变** | 单日 ATR% 突破上限 1.5× | 中 |

## 4. 仓位与资金管理（份制扩展到组合）

### 4.1 现有份制回顾（[us_nvda_1d_strategy_multifactor.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_multifactor.py)）

现有单标的份制（详见现有源码 `global_variables`）：

- `position_pct = 0.2`（单次投入 = 首次本金基数的 20%）
- `max_slices = 5`（满仓上限 = 5 份）
- `base_capital`：首次建仓时锁定的本金基数，避免随浮盈漂移
- `slice_value = base_capital × position_pct` — 单份金额恒定

### 4.2 多标的份制扩展

将"单标的份制"扩展到"组合份制"，新增 2 层结构：

```text
账户层 (Account)
  └─ 池配额 (Pool Budget)：本策略可用 NAV 的比例上限（如 60%）
       └─ 单标的配额 (Per-Symbol Budget)：池配额 / 持仓数上限
            └─ 份制 (Slices)：单标的内沿用现有 5 份制
```

**设计参数**（脱敏，所有量值为占位符）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `pool_budget_pct` | 0.6 | 本策略可用比例：占账户净资产 NAV 的 60% |
| `max_concurrent_holdings` | 5（阶段 ②） / 10（阶段 ③） | 同时持仓的最大 symbol 数 |
| `per_symbol_budget` | `NAV × pool_budget_pct / max_concurrent_holdings` | 单标的预算 |
| `position_pct` | 0.2 | 单次投入 = 单标的预算的 20% |
| `max_slices` | 5 | 单标的满仓 5 份（沿用现有） |
| `cash_buffer_pct` | 0.05 | 始终保留 5% 现金缓冲（应对滑点与再平衡） |

### 4.3 资金分配（关键 — 多标的下的资金竞争）

参考 [02 §3.3](./02_futu_multi_symbol_capability.md)：`cash` 是账户共享池，`max_qty_to_buy_on_cash(symbol)` 假设全资金用于该 symbol，**业务层必须先做预算分配再下单**。

下单前预算分配伪代码（设计示意）：

```text
# 1. 拉取账户级数据（[02 §3.3](./02_futu_multi_symbol_capability.md)）
#    nav = net_asset(currency)
#    available_cash = cash(currency)
# 2. 计算池配额
#    pool_budget = nav × pool_budget_pct
# 3. 取信号通过的待买列表 buy_list（已经过 §3.2 / §3.3 过滤）
# 4. 单标的预算
#    per_budget = pool_budget / max_concurrent_holdings
#    slice_value = per_budget × position_pct
# 5. 对每个 sym in buy_list 串行下单：
#    qty = floor(slice_value / price)
#    qty = min(qty, max_qty_to_buy_on_cash(sym))
#    if qty > 0: place_market(symbol=sym, qty=qty)
# 6. 实际可用现金每次下单后会减少，需在循环内重读 available_cash
```

### 4.4 加仓 / 减仓

- **加仓**：单标的内沿用现有规则（最小加仓间隔 = 10 K 线、回调 ≥ 2% 才加仓、最多 5 份）。
- **减仓 / 清仓**：只在出场信号触发时执行（§3.4）。
- **跨标的再平衡（阶段 ③）**：每日收盘后按横截面排序产生 buy_list / sell_list；先执行 sell（释放现金），再执行 buy。

## 5. 风控规则（≥4 条）

### 5.1 单标的层（symbol-level）

1. **硬止损**：单标的最大亏损 = `stop_loss_pct`（默认 5%）；
2. **移动止盈**：浮盈达 `take_profit_pct`（默认 10%）后激活，回撤 `trailing_drawdown_pct`（默认 5%）即出；
3. **加仓护栏**：连续 2 次加仓后未盈利则禁止再加仓；
4. **持仓最长天数**：阶段 ② 默认无限期；阶段 ③ 设上限（如 60 个交易日）。

### 5.2 组合层（portfolio-level）

5. **组合最大回撤熔断**：策略浮亏 ≥ `portfolio_dd_limit`（默认 8%）→ 禁止新开仓，仅允许减仓与止损；恢复条件由人工 review；
6. **单日下单笔数上限**：≤ `max_orders_per_day`（默认 10），防止单日异常多次触发；
7. **行业暴露上限**：单一行业持仓总市值 / 池配额 ≤ `sector_cap`（默认 40%），防止集中度风险；
8. **现金缓冲**：始终保留 ≥ `cash_buffer_pct`（默认 5%）现金；
9. **黑天鹅日**：开盘跳空 ≥ 5% 的标的当日不入场；指数（SPY）跳空 ≥ 3% 时全策略当日不开新仓。

### 5.3 系统层（safety）

10. **Live 硬开关**：`LIVE_SUBMIT` 默认 `False`；切到 `True` 必须走"实际执行前确认规则"；
11. **订单幂等**：每次下单生成 `order_idempotency_key`，避免网络重试导致重复下单；
12. **状态落盘**：每根 K 线把组合状态（持仓、可用现金、信号列表）落盘到 `state/runs/`，断点可恢复。

## 6. 回测与评估方法

### 6.1 双轨回测（与 [02 §4](./02_futu_multi_symbol_capability.md) 对齐）

| 轨道 | 工具 | 主要用途 |
|------|------|----------|
| 主轨 | Futu 平台回测 | 信号正确性、贴合实盘行为 |
| 影子轨 | [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) | 回归测试、极端场景压测、参数搜索 |

两轨结论一致才视为通过；不一致需追溯到行情对齐、撮合差异、成交量过滤口径等。

### 6.2 数据范围

- 回测样本：2020-01-01 ~ 当前；
- 训练 / 验证切分：前 70% 调参、后 30% 留作 OOS（out-of-sample）；
- 标的池：阶段 ② 用 10–20 只；阶段 ③ 用 30–50 只。

### 6.3 评估指标

| 维度 | 指标 | 阶段 ② 目标 | 阶段 ③ 目标 |
|------|------|-------------|-------------|
| 收益 | 年化收益、累计收益 | ≥ SPY 等权 | ≥ 阶段 ② |
| 风险 | 最大回撤（MDD） | ≤ 单标基线 MDD | 进一步收窄 |
| 风险调整 | Sharpe / Sortino / Calmar | ≥ 1.0 / 1.5 / 0.5 | ≥ 阶段 ② |
| 行为 | 换手率、平均持仓天数、单笔胜率 | 换手率 ≤ 200%/年 | 换手率 ≤ 400%/年 |
| 稳健性 | OOS 与 IS 衰减比、参数邻域稳健性、滚动窗口稳定性 | 衰减 ≤ 30%；邻域差异 ≤ 20% | 同左 |
| 容量 | 单笔成交占 ADV 比例 | ≤ 1% | ≤ 1% |

### 6.4 基准对照

- 主基准：SPY；
- 次基准：QQQ、池内等权 buy & hold；
- 单标基线：现有 NVDA 单标多因子策略（[us_nvda_1d_strategy_multifactor.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_multifactor.py)）。

### 6.5 调参与防过拟合

- 参数维度优先级：`stop_loss_pct` > `pool_budget_pct` > `max_concurrent_holdings` > 因子权重；
- 网格步长保守（如 stop_loss 取 [3%, 5%, 7%]，不做精细网格）；
- 报告必须含**参数邻域稳健性**与**OOS 衰减**两个稳健性图。

## 7. 三阶段上线路径

### 7.1 阶段总览

| 阶段 | 范围 | 标的数 | 选股逻辑 | 状态 |
|------|------|--------|----------|------|
| ① 单标的多因子 | NVDA 日线 | 1 | 无 | 已存在（基线） |
| ② 固定股票池 | 美股大盘股 | 10–20 | 无（同套规则跑全部） | 本方案设计目标 |
| ③ 横截面选股 | 美股大盘股 | 30–50 | 每日 top-K | 本方案设计目标 |

### 7.2 阶段 ② 落地里程碑（本文设计 — 不在本任务执行）

1. M1 — 池定义与冻结：产出 `pool_config.yaml`，含 10–20 个 symbol；
2. M2 — 策略骨架：基于现有 NVDA 策略扩展为多 symbol 循环（不改原策略，**新建文件**）；
3. M3 — 资金分配实现：实现 §4.3 预算分配；
4. M4 — 风控规则实现：单标的层 4 条 + 组合层 5 条；
5. M5 — 双轨回测对账：Futu 平台 + 本地回测结论对齐；
6. M6 — SIM 灰度：SIM 账户运行 ≥ 4 周，对账无差异 → 准备阶段 ③。

### 7.3 阶段 ③ 落地里程碑

1. M7 — 横截面排序模块：实现 §3.3；
2. M8 — 调仓节流：单日换仓上限、最小持仓天数；
3. M9 — 行业暴露与容量约束：实现 §5.2 #7 与 §6.3 容量指标；
4. M10 — 长样本压测：≥ 3 年 OOS 通过；
5. M11 — SIM 灰度 → REAL 切换：必须通过项目硬开关、人工审批、对账、风控、订单幂等。

### 7.4 阶段间退出门槛（gate）

每个阶段必须满足以下全部门槛才可进入下一阶段：

- ✅ OOS 评估指标全部达成（§6.3）；
- ✅ 双轨回测结论一致；
- ✅ 风控规则全部生效（含组合层熔断与黑天鹅日规则）；
- ✅ SIM 灰度 ≥ 4 周对账无差异；
- ✅ 文档同步更新（[docs/system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md) 与 [docs/project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)）。

## 8. 与现有策略的关系

| 现有资产 | 在本方案中的定位 | 是否修改 |
|----------|------------------|----------|
| [us_nvda_1d_strategy_multifactor.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_multifactor.py) | 阶段 ① 单标的多因子基线 | ❌ 不修改 |
| [us_nvda_1d_strategy_trend_momentum.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_trend_momentum.py) | 单标的趋势动量变体（参考） | ❌ 不修改 |
| [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) | 双轨对账中的本地轨 | ❌ 不修改（落地时如需扩展多 symbol 入参，走新 plan） |
| [run_futu_data_pull.py](/projects/vnpy/tmp/run_futu_data_pull.py) | 数据拉取（阶段 ② 需扩到全池） | ❌ 本方案不修改 |
| 份制思想（`base_capital`/`slice_value`/`max_slices`） | 在 §4.2 升级为组合份制 | 思想复用 |

## 9. 降级方案与风险标记

### 9.1 降级路径

| 触发条件 | 降级动作 | 终态 |
|----------|----------|------|
| 阶段 ② OOS 衰减 > 30% | 退回阶段 ① | 单标 NVDA |
| 阶段 ③ 横截面排序未跑赢阶段 ② | 退回阶段 ② | 固定股票池 |
| 平台运行标的数 > 50 | 强制退回 ≤ 50；或拆分多策略实例 | 多实例并行 |
| 组合最大回撤熔断触发 | 全策略仅减仓不开仓；人工 review | 暂停期 |

### 9.2 已识别风险（含 [02 §5.2](./02_futu_multi_symbol_capability.md) 待验证项）

1. **平台性能未验证**：50 标的 `handle_data` 单次执行耗时、回测引擎耗时（[02 §5.2](./02_futu_multi_symbol_capability.md) 第 1、3 项）；
2. **多 symbol 串行下单的延迟**（[02 §5.2](./02_futu_multi_symbol_capability.md) 第 2 项）；
3. **资金竞争边界 case**：连续多个 symbol 同时触发买入信号时，可用现金动态变化的精确性；
4. **横截面参数过拟合风险**：top-K、调仓节流参数对历史样本敏感；
5. **池更新引入的"幸存者偏差"**：阶段 ② 人工选池可能引入历史已知优胜者；
6. **市场状态切换风险**：本方案在牛市趋势行情中预期占优，震荡 / 熊市需通过 §5 风控保持存活但不期待跑赢。

### 9.3 待落地阶段验证项（不在本研究范围）

- [ ] 平台 50 标的性能验证（最小验证脚本，独立 plan）
- [ ] `pool_config.yaml` schema 与池构造规则代码化
- [ ] 行业映射表数据源（避免硬编码）
- [ ] 财报日历数据源
- [ ] 订单幂等键格式与状态落盘 schema

---

> **文档结束**。本方案不含任何可执行实现；下一步若进入落地，应走 `.codebuddy/plan/` 新建计划，并按 §7.2 / §7.3 里程碑推进，每个里程碑独立确认后执行。
