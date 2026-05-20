# 02 Futu 多标的能力评估

> 本文档为研究性文档，**不含可执行代码**。所有结论均带 [futu_quant.md](/projects/vnpy/tmp/futu_quant.md) 行号引用，关键事实经 `grep_search` 二次校验。
>
> 关联：[00 汇总入口](./00_index.md) ｜ [01 指标调研](./01_indicator_research.md) ｜ [03 策略方案](./03_strategy_design.md)

## 1. 结论摘要表（能力矩阵）

| 能力项 | Futu 平台支持 | 实现路径 | 关键证据 |
|--------|---------------|----------|----------|
| 多标的驱动（≤50） | ✅ 原生 | `trigger_symbols()` 内多次 `declare_trig_symbol()` | 第 14858–14860、14890 行 |
| 跨 symbol 调用同一指标 | ✅ 原生 | 内建指标形参均含 `symbol=Contract('US.XXXX')`，可在 `handle_data` 内对运行标的池循环调用 | 形参示例第 33 / 153 / 273 行等 |
| 横截面排序选股 | ⚠️ 业务层 | 平台无内建排序 API，需自行遍历运行标的池→收集指标值→`sorted()`选 top-N | 见 §3.3 |
| 多 symbol 同时下单 | ✅ 原生 | 下单 API 形参 `symbol=Contract(...)`，可在同一 `handle_data` 内连续下单不同 symbol | `place_market` 第 6039 行；`place_limit` 第 5983 行 |
| 多市场（US+HK）混合回测 | ⚠️ 受限 | API 上兼容（symbol 字符串前缀区分市场），但需关注币种、时段、`session_type` 三重对齐；建议在阶段 ② 内仅做单市场 | `Contract` 说明第 14285–14290 行 |
| 麦语言注册→多 symbol 复用 | ✅ 原生 | 一次 `register_indicator` 注册后，`get_MyLang_indicator` 可对任意 symbol 调用 | 注册流程第 14914–14916 行附近章节；通用接口形参第 453 行 |
| 资金/购买力查询 | ✅ 原生 | `cash` / `total_cash` / `net_asset` / `cash_buying_power` / `max_qty_to_buy_on_cash` | 第 10489 / 10529 / 10328 / 10985 / 11276 行 |
| 持仓查询（按 symbol） | ✅ 原生 | `position_holding_qty(symbol)` | 第 11565 行 |
| 撤单（按 symbol / orderid / 全部） | ✅ 原生 | `cancel_order_by_symbol` / `cancel_order_by_orderid` / `cancel_order_all` | 第 6515 / 6550 / 6584 行 |
| 平仓 | ✅ 原生 | `close_positions(symbol, qty=...)` | 第 6826 行 |

> 总结：**阶段 ② 固定股票池**与**阶段 ③ 横截面选股**在 Futu 平台均可落地；阶段 ③ 需要业务层补齐"横截面排序"逻辑；多市场混合留给阶段 ③+。

## 2. 关键事实清单

### 2.1 多标的驱动模型

**事实 A：策略主入口由 `handle_data()` 承担，触发方式有 4 种**（[futu_quant.md](/projects/vnpy/tmp/futu_quant.md) 第 14914–14916 行）：

> 每次收到触发信号，会运行一次 handle_data() 函数。建议将策略的主要逻辑，写在 handle_data() 函数中。
> handle_data() 目前会响应这 4 类触发信号：每根 K 线运行一次，每 Tick 运行一次、每 N 秒运行一次、定时运行。

**事实 B：运行标的（驱动标的）通过 `declare_trig_symbol()` 声明**（第 14886–14888 行）：

```text
def trigger_symbols(self):
    self.运行标的1 = declare_trig_symbol()
    self.运行标的2 = declare_trig_symbol()
```

**事实 C：单策略最多 50 个运行标的**（第 14890 行）：

> 每个策略中最多可创建 50 个运行标的。实盘运行和历史回测中，可指定具体标的为运行标的。

**事实 D：运行标的的行情推送会驱动策略循环运行**（第 14891 行附近）：

> 运行标的的行情推送，可以驱动策略循环运行。例如：将策略的运行条件设为"苹果（AAPL）每个 tick 运行一次"。

**推论**：阶段 ② 固定股票池场景下，把"运行标的池"声明为 N（≤50）个 symbol，每根 K 线触发时 `handle_data` 会被运行标的的行情驱动，业务层在 `handle_data` 内统一处理这 N 个 symbol 的信号即可。

### 2.2 跨 symbol 调用同一指标

**事实 E：所有内建指标 API 形参均显式接收 `symbol=Contract(...)`**（举证如下）：

| 指标 | 形参示例 | 行号 |
|------|----------|------|
| MA | `ma(symbol=Contract("US.AAPL"), ...)` | 第 33 行 |
| EMA | `ema(symbol=Contract("US.AAPL"), ...)` | 第 153 行 |
| MACD | `is_macd_golden_cross(symbol=Contract("US.AAPL"), ...)` | 第 1099 行 |
| RSI | `is_rsi_golden_cross(symbol=Contract("US.AAPL"), ...)` | 第 2883 行 |
| ATR | `atr_atr(symbol=Contract("US.AAPL"), ...)` | 第 1663 行 |
| BOLL | `boll_mid(symbol=Contract("US.AAPL"), ...)` | 第 5054 行 |

**推论**：在 `handle_data` 内对一个 symbol 列表 `for sym in self.symbol_list:` 逐个调用同一指标完全可行，不需要为每个 symbol 单独声明指标。

### 2.3 麦语言自定义指标在多标的下的复用

**事实 F：麦语言指标只需注册一次，调用时按 symbol 传参**（第 14914–14916 行附近章节）：

> 步骤 1：在 custom_indicator() 函数中，使用 register_indicator() 接口编写并注册该指标。
> 步骤 2：需要获取指标值时，使用 get_MyLang_indicator() 接口进行请求。

通用接口形参（第 453 行）：

```text
get_MyLang_indicator(indicator_name, variable_name, symbol, params, bar_type=BarType.K_60M, select=2, session_type=THType.ALL)
```

**推论**：DMI/ADX、AROON、VMACD 等麦语言指标可在 `custom_indicator()` 中**注册一次**，在 `handle_data` 内对每个运行标的传不同 `symbol` 参数复用，不会产生 N 倍注册成本。

## 3. 多标的下下单 / 持仓查询 / 资金竞争

### 3.1 下单 API（按 symbol 维度）

| API | 行号 | 多标的可用性 |
|-----|------|---------------|
| `place_limit` | 5983 | ✅ 形参含 `symbol`，可对不同 symbol 串行下单 |
| `place_market` | 6039 | ✅ 同上 |
| `place_stop_limit` / `place_stop` | 6096 / 6153 | ✅ |
| `place_limit_if_touched` / `place_market_if_touched` | 6209 / 6267 | ✅ |
| `place_trailing_stop_limit` / `place_trailing_stop` | 6324 / 6391 | ✅ |
| `cancel_order_by_symbol` | 6515 | ✅ 直接按 symbol 撤单 |
| `cancel_order_by_orderid` | 6550 | ✅ 按 orderid 撤单 |
| `cancel_order_all` | 6584 | ⚠️ 全策略撤单，多标的策略需谨慎使用 |
| `cancel_and_liquidate` | 6656 | ⚠️ 撤单+平仓，破坏性操作 |
| `close_positions` | 6826 | ✅ 按 symbol 平仓 |

### 3.2 持仓查询（按 symbol 维度）

| API | 行号 | 用途 |
|-----|------|------|
| `position_holding_qty(symbol)` | 11565 | 查询指定 symbol 当前持仓数量 |

### 3.3 资金 / 购买力 API（账户维度，多标的共享）

| API | 行号 | 维度 | 用途 |
|-----|------|------|------|
| `net_asset(currency)` | 10328 | 账户级 | 总净资产 |
| `total_cash(currency)` | 10489 | 账户级 | 现金 |
| `cash(currency)` | 10529 | 账户级 | 可用现金 |
| `cash_buying_power` | 10985 | 账户级 | 现金购买力 |
| `max_qty_to_buy_on_cash(symbol, ...)` | 11276 | symbol 级 | 该 symbol 最大可买数量 |
| `max_qty_to_buy_on_margin(symbol, ...)` | 11234 | symbol 级 | 融资最大可买数量 |
| `max_qty_to_sell(symbol)` | 11318 | symbol 级 | 最大可卖数量 |
| `max_qty_to_sell_short(symbol)` | 11397 | symbol 级 | 最大可卖空数量 |

**关键推论 — 多标的资金竞争**：

1. `cash` / `cash_buying_power` 是**账户共享资源**，N 个 symbol 共用一份现金池；
2. `max_qty_to_buy_on_cash(symbol)` 在每个 symbol 上独立计算，但**它假设把全部可用现金都用于该 symbol**；
3. **业务层必须自行做"预算分配"**：在 `handle_data` 内先按选中的下单 symbol 列表平均/加权分配预算，再调用 `place_market/place_limit`，避免出现"前面的 symbol 把资金用光，后面的 symbol 下单失败"。

### 3.4 横截面排序的业务层实现要点

平台无内建排序 API，业务层模式：

```text
# 仅作设计示意，非可执行代码
1. 收集：for sym in pool: score[sym] = calc_signal(sym)
2. 排序：top_n = sorted(score.items(), key=..., reverse=True)[:N]
3. 风控：剔除已持仓、剔除流动性不足、剔除波动率过滤未通过
4. 预算：budget_per = available_cash / len(top_n)
5. 下单：for sym, _ in top_n: place_limit(symbol=sym, qty=...)
```

## 4. 回测能力对比（Futu 平台 vs 本地 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py)）

| 维度 | Futu 平台回测 | 本地 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) |
|------|---------------|------------------------------------------------------------------------|
| 数据源 | Futu 历史行情（与实盘同源） | 本地 K 线（来自 [run_futu_data_pull.py](/projects/vnpy/tmp/run_futu_data_pull.py) 的离线数据） |
| 多标的支持 | ✅ ≤50（同实盘） | ✅ 取决于本地实现（vnpy 框架本身支持多 symbol） |
| 内建指标可用性 | ✅ 全部 | ❌ 需自行实现或借用 ta-lib/pandas |
| 麦语言指标 | ✅ `register_indicator` 路径 | ❌ 不支持（必须改写为 Python） |
| 撮合细节 | 黑盒（平台默认规则） | ✅ 可控（vnpy 撮合参数可见） |
| 数据完整性 | 平台保证 | ⚠️ 取决于离线拉取的覆盖范围 |
| 用途定位 | 主要：信号正确性、平台一致性 | 主要：复现 / 极端场景 / 回归测试 |

**双轨建议**：阶段 ② 起以 Futu 平台回测为主（贴合实盘），本地回测作"对账影子"用于回归与极端场景；不建议把麦语言指标作为本地与平台的共享口径（移植成本高）。

## 5. 限制清单与对需求 3 方案的阻塞判定

### 5.1 已确认的硬限制

| 限制 | 来源 | 对方案的影响 |
|------|------|-------------|
| 单策略最多 50 个运行标的 | 第 14890 行 | ✅ 不阻塞：阶段 ② 取 10–30 个，阶段 ③ 取 30–50 个均在限内 |
| 平台无横截面排序 API | 平台文档无相关条目 | ⚠️ 业务层补齐（成本低） |
| 全策略撤单 `cancel_order_all` 跨 symbol | 第 6584 行 | ⚠️ 多标的下需改为 `cancel_order_by_symbol` 精确撤单 |
| 多市场币种/时段对齐 | `Contract`/`THType` 章节 | ⚠️ 阶段 ② 仅做单市场（US），阶段 ③+ 再考虑混合 |

### 5.2 待确认 / 需要后续验证的事项

> 以下事项**未在本研究范围**，需在落地时通过最小验证脚本确认（落地前需走"实际执行前确认规则"）：

1. **运行标的 N 增大对 `handle_data` 单次执行耗时的影响** — 需在 Futu 平台用 1 / 10 / 30 标的做对比测试。
2. **同一根 K 线内对 N 个 symbol 串行下单的延迟** — 影响实盘成交质量。
3. **平台回测引擎对 50 个运行标的的回测耗时** — 影响参数搜索可行性。

### 5.3 对需求 3 方案的阻塞判定

**判定结论：不存在硬阻塞**。

- 阶段 ② 固定股票池（10–30 标的）：✅ Futu 平台原生支持，可直接落地。
- 阶段 ③ 横截面排序选股（30–50 标的）：✅ 平台能力够用，需业务层补齐排序与预算分配；§5.2 的三项性能事项需要在落地前做最小验证。
