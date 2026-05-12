---
name: westock-tool
description: 条件选股/策略选股/标签选股工具 - 当用户需要按条件筛选股票、使用预置策略选股或按标签分类查看股票时使用。条件选股支持按价格、市盈率、市净率、ROE、涨跌幅、成交量、市值、资金流向等指标筛选，覆盖沪深/港股/美股；策略选股提供50+预置策略（基本面/大师策略/技术面/资金面），一键获取策略信号股票；标签选股提供90+预定义分类标签（股东属性/风险/估值/财务特征/价格市值/新股次新股等），按标签快速获取股票列表。策略选股和标签选股仅支持A股。注意：本工具只做"选股筛选"，查询个股详情请用 westock-data；查询"新股/次新股"标签分类股票请用本工具的 label 命令
---

# WeStock Tool

**作用**：提供三种选股方式——条件选股（自定义表达式筛选）、策略选股（50+预置策略一键获取信号股票）和标签选股（90+预定义分类标签快速获取股票列表）。

> **工具分工说明**：
> - **westock-tool**（本工具）：**筛选/选股** — "找出满足条件的股票列表"、"用MACD金叉策略选股"、"央企有哪些"、"破净股有哪些"、"新股有哪些"、"次新股有哪些"
> - **westock-data**：**查询个股详情** — "查某只股票的行情/K线/财务/资金等数据"、"查新股日历（ipo）"
> - **westock-portfolio**：**用户自选股查询** — "查看我的自选股列表"（当用户说"我的自选股"、"自选股有哪些"时使用）
>
> ⚠️ **命令路由原则**：
> - **`strategy` 命令**：当用户提到**策略名称**（如"MACD金叉策略"、"巴菲特策略"、"格雷厄姆策略"）时使用，返回该策略信号股票列表
> - **`filter --preset` 命令**：当用户需要**预设条件快速筛选**（如"低PE股"、"高股息股"、"MACD金叉信号"）时使用，属于 filter 的快捷方式
> - **`label` 命令**：当用户提到**分类标签**（如"央企"、"国企"、"ST股"、"新股"、"次新股"、"破净股"、"高ROE"）时使用
> - **`filter` 表达式**：当用户描述**自定义条件**（如"PE<20且ROE>15"）时使用

**数据源**：腾讯自选股选股数据接口 | **条件选股**：A股、港股、美股 | **策略选股**：仅A股 | **标签选股**：仅A股

---

## 已知限制速查

| 限制项 | 说明 |
|--------|------|
| 市场覆盖 | 沪深A股、港股、美股；**不支持北交所** |
| 市值单位差异 | 沪深 `TotalMV` 单位为**元**，港股/美股为**亿元**，构建条件时注意换算 |
| 港股/美股字段名 | 估值字段名与沪深不同（见常用字段速查），**切勿混用** |
| PE/PB 负值 | 亏损股 PE/PB 为负，筛选时必须排除负值，如 `PE_TTM > 0` |
| 港股特殊字段 | `PsTtm`/`PcfTtm` 仅选股查询支持，快照查询返回 0 |
| 预设函数市场 | 港股必须加 `--market hk`，美股必须加 `--market us` |
| 策略选股市场 | `strategy` 命令**仅支持 A 股**，不支持港股/美股 |
| 标签选股市场 | `label` 命令**仅支持 A 股**，不支持港股/美股 |

### ⚠️ 常见错误

| 错误写法 | 正确写法 | 说明 |
|---------|---------|------|
| `PE_TTM < 20 & ROE_TTM > 15` | `intersect([PE_TTM > 0, PE_TTM < 20, ROETTM > 15])` | 多条件AND必须用`intersect()`，不支持`&`/`&&` |
| `PE_TTM < 20 AND ROETTM > 15` | `intersect([PE_TTM > 0, PE_TTM < 20, ROETTM > 15])` | 同上，不支持`AND`关键字 |

---

## 条件选股（filter）

```bash
# 基本用法
westock-tool filter "ClosePrice >= 100"
westock-tool filter "ClosePrice >= 100" --date 2026-03-12
westock-tool filter "ClosePrice >= 100" --date 2026-03-12 --limit 20

# AND 组合条件
westock-tool filter "intersect([PE_TTM > 0, PE_TTM < 20, ROETTM > 15])"

# OR 组合条件
westock-tool filter "union([ChangePCT > 5, Chg5D > 10])"

# 指定排序（按 ROE 降序）
westock-tool filter "intersect([PE_TTM > 0, PE_TTM < 15, ROETTM > 15])" --date 2026-03-12 --limit 20 --orderby ROETTM:desc

# 港股选股（--market hk）
westock-tool filter "intersect([PeTTM > 0, PeTTM < 10, DivTTM > 5])" --market hk

# 美股选股（--market us）
westock-tool filter "intersect([PeTTM > 0, PeTTM < 30, TotalMV > 1000])" --market us

# 按板块筛选（--universe，板块代码通过 westock-data search <关键词> sector 获取）
westock-tool filter "intersect([PE_TTM > 0, PE_TTM < 20])" --date 2026-03-12 --limit 20 --universe 11010001

# 输出原始 JSON
westock-tool filter "ClosePrice >= 100" --raw
```

**参数说明**：

| 参数 | 是否必选 | 说明 |
|------|---------|------|
| 表达式 | ✅ | 选股表达式，详见下方「表达式语法」 |
| `--date` | 可选 | `YYYY-MM-DD`，默认今天 |
| `--limit` | 可选 | 最大返回数量，默认 20 |
| `--orderby` | 可选 | 排序，格式 `字段:asc` 或 `字段:desc`，默认 `desc` |
| `--market` | 可选 | `hk`=港股，`us`=美股，不指定默认沪深 |
| `--universe` | 可选 | 概念板块代码，限定选股范围；通过 `westock-data search <关键词> sector` 获取板块代码 |
| `--raw` | 可选 | 输出原始 JSON 而非 Markdown 表格 |

**返回格式**：

输出 Markdown 表格，每行一只股票，列含股票代码、名称、收盘价、涨跌幅及表达式涉及的指标字段：

```
共 156 只符合条件，当前显示 20 只

| code | name | ClosePrice | ChangePCT | PE_TTM | ROETTM |
|------|------|------------|-----------|--------|--------|
| sh600519 | 贵州茅台 | 1690.00 | 1.25 | 28.50 | 32.15 |
| sz000001 | 平安银行 | 12.50 | -0.32 | 5.80 | 16.20 |
```

### 预设选股函数

> ⚠️ **`filter --preset` 与 `strategy` 的区别**：
> - `filter --preset` 是 **filter 命令的快捷方式**，用预设条件表达式筛选，输出含指标列的 Markdown 表格
> - `strategy` 是**独立的策略选股命令**，从预置策略列表中获取信号股票，输出仅含 code/name 的表格
> - 两者语义等价但**命令路径不同**：如 MACD 金叉，用 `filter --preset MACDGoldenCross` 或 `strategy macd_golden` 均可，但不可混用参数格式

常见选股场景可直接使用预设函数，无需手写表达式：

**CLI 调用方式**：

```bash
# 使用预设函数（--preset）
westock-tool filter --preset MACDGoldenCross --date 2026-03-24 --limit 30
westock-tool filter --preset LowPE --date 2026-03-12 --limit 20
westock-tool filter --preset HighDividend --date 2026-03-12 --limit 20 --market hk

# 省略日期或数量（使用默认值）
westock-tool filter --preset BullishMA                        # 今天，默认20条
westock-tool filter --preset BullishMA --date 2026-03-28      # 指定日期，默认20条
westock-tool filter --preset BullishMA --date 2026-03-28 --limit 3 # 指定日期和数量

# 查看所有可用预设函数名称列表
westock-tool filter --list-presets
```

> `--list-presets` 返回纯文本函数名列表（每行一个函数名），不含参数说明。

**参数说明**：

| 参数 | 是否必选 | 说明 |
|------|---------|------|
| `--preset` | 可选 | 预设函数名（见下表） |
| `--date` | 可选 | `YYYY-MM-DD`，默认今天 |
| `--limit` | 可选 | 最大返回数量，默认 20 |
| `--market` | 可选 | `hk`=港股，`us`=美股 |
| `--universe` | 可选 | 概念板块代码 |

> 所有参数均为命名参数，顺序不限。

> ⚠️ **预设函数的参数均为内置默认值，不支持通过 CLI 传入自定义参数。** 如需自定义条件，请使用 `filter` 表达式语法手写条件。

#### 估值分析类

| 函数名 | 说明 | 内置默认值 |
|--------|------|------|
| `LowPE` | 低PE筛选 | `maxPE`=20 |
| `LowPB` | 破净股筛选(PB<1) | `maxPB`=1 |
| `HighDividend` | 高股息筛选 | `minDividend`=3% |
| `ValuationPercentile` | 估值百分位低位 | `maxPercentile`=30 |
| `PEG` | PEG策略(PEG<1) | `maxPEG`=1, `minGrowth`=20% |

#### 技术指标类

| 函数名 | 说明 | 内置默认值 |
|--------|------|------|
| `BullishMA` | 均线多头排列 | - |
| `MACDGoldenCross` | MACD金叉 | - |
| `KDJOversold` | KDJ超卖 | `maxJ`=20 |
| `RSIOversold` | RSI超卖 | `maxRSI`=30 |
| `BollingerBreakout` | 布林带突破上轨 | - |
| `NineTurnGreen9` | 神奇九转绿9信号 | - |

#### 财务分析类

| 函数名 | 说明 | 内置默认值 |
|--------|------|------|
| `HighROE` | 高ROE筛选 | `minROE`=15% |
| `HighGrowth` | 高成长筛选 | `minRevenueGrowth`=20%, `minProfitGrowth`=30% |
| `LowDebt` | 低负债筛选 | `maxDebtRatio`=50% |
| `PositiveCashFlow` | 正现金流筛选 | - |

#### 资金流向类

| 函数名 | 说明 | 内置默认值 |
|--------|------|------|
| `MainInflow` | 主力资金流入 | `minInflow`=1亿 |
| `SustainedInflow` | 主力持续流入(5/10/20日) | - |
| `HighShortRatio` | 高卖空比例 | `minShortRatio`=10% |

#### 机构评级类（港股/美股）

| 函数名 | 说明 | 内置默认值 |
|--------|------|------|
| `HighRating` | 高机构评级 | `minBuyRating`=5 |
| `TargetPriceUpside` | 目标价上行空间 | `minUpside`=20% |

#### 组合策略类

| 函数名 | 说明 | 内置默认值 |
|--------|------|------|
| `HighDividendLowValuation` | 高股息+低估值 | `minDividend`, `maxPE`, `maxPB` |
| `WhiteHorseGrowth` | 白马成长(高ROE+稳定增长) | - |
| `Turnaround` | 困境反转 | `minTurnaround`=50% |
| `SmallCapValue` | 小盘价值(20-100亿市值) | - |
| `TechFundamentalCombo` | 技术面+基本面组合 | - |

### 表达式语法

| 语法 | 说明 | 示例 |
|------|------|------|
| `字段 比较符 值` | 单条件 | `ClosePrice >= 100` |
| `intersect([条件1, 条件2, ...])` | **AND 组合（必须使用）** | `intersect([ClosePrice >= 100, PE_TTM < 20])` |
| `union([条件1, 条件2, ...])` | OR 组合 | `union([ChangePCT > 5, Chg5D > 10])` |

> **⚠️ 重要**：多条件 AND 组合**必须使用 `intersect([...])`**，不支持 `&`、`&&`、`AND` 等符号或关键字。

---

## 策略选股（strategy）

```bash
# 查看所有可用策略
westock-tool strategy --list

# 单个策略查询（默认今天）
westock-tool strategy macd_golden
westock-tool strategy macd_golden --date 2026-04-10

# 多个策略同时查询（按 code 分别返回结果）
westock-tool strategy high_dividend,pb_roe --date 2026-04-10

# 限制返回数量
westock-tool strategy buffet --date 2026-04-10 --limit 10

# 分页查询（从第 20 条开始取 10 条）
westock-tool strategy major_force --limit 10 --offset 20

# 日期区间查询（仅支持单个策略，⚠️ 查多天趋势时必须用 --start/--end，不要用 --date 多次调用）
westock-tool strategy macd_golden --start 2026-04-01 --end 2026-04-10
```

**参数说明**：

| 参数 | 是否必选 | 说明 |
|------|---------|------|
| 策略名称 | ✅ | 策略代码（短名或自定义 listcode），多个用逗号分隔 |
| `--date` | 可选 | `YYYY-MM-DD`，默认今天 |
| `--start` | 可选 | 区间查询起始日期 `YYYY-MM-DD`（需配合 `--end`，**查多天趋势时必须用此参数**） |
| `--end` | 可选 | 区间查询结束日期 `YYYY-MM-DD`（需配合 `--start`） |
| `--limit` | 可选 | 返回数量，默认 20 |
| `--offset` | 可选 | 偏移量，默认 0（用于翻页） |
| `--list` | 可选 | 列出所有可用策略 |

> **自定义 code**：除了内置短名，也支持直接传入任意 listcode 字符串。新增的策略 code 即使不在内置列表中也可直接使用，无需更新工具代码。可通过 `--list` 获取最新可用策略列表。

**返回格式**：

```
策略: MACD金叉 (macd_golden) | 日期: 2026-04-10 | 共 35 只，显示 20 只

| code | name |
|------|------|
| sh600519 | 贵州茅台 |
| sz000001 | 平安银行 |
```

**可用策略概览**（7 大类 50+ 策略，通过 `--list` 获取完整列表）：

| 分类 | 数量 | 代表性策略 |
|------|------|----------|
| 基本面 | 6 | `high_dividend`(高股息)、`pb_roe`(高盈利价值)、`profit_preannounce`(业绩预增) |
| 大师策略 | 6 | `buffet`(价值龙头)、`graham`(格雷厄姆)、`fisher`(费雪) |
| 综合 | 3 | `golden_strategy`(策略金股)、`increase_holding`(高管增持) |
| K线形态 | 11 | `morning_star`(早晨之星)、`red_three_solider`(红三兵)、`over_drop_rebound`(超跌反弹) |
| 指标信号 | 14 | `macd_golden`(MACD金叉)、`kdj_golden`(KDJ金叉)、`rsi_oversold`(RSI超卖) |
| 均线/布林 | 6 | `ma_long`(均线多头发散)、`boll_bt_upper`(布林带上轨突破) |
| 资金面 | 4 | `major_force`(主力抢筹)、`institution_chasing`(机构接盘) |

**完整策略列表参见 [references/ai_usage_guide.md](./references/ai_usage_guide.md)**

---

## 标签选股（label）

> ⚠️ **何时使用 `label`**：当用户问"央企有哪些"、"ST股有哪些"、"新股有哪些"、"次新股"、"破净股"、"高ROE股"等**分类标签**问题时，使用 `label` 命令

通过预定义标签分类快速获取符合条件的股票列表，无需手写表达式。标签涵盖股东属性、风险标签、估值水平、财务结构、财务排名、财务特征、上市时间、价格与市值等 11 大类 90+ 个标签。

```bash
# 查看所有可用标签
westock-tool label --list

# 按分组查看标签
westock-tool label --list 股东属性
westock-tool label --list 财务特征

# 单个标签查询（默认今天）
westock-tool label shareholder_central_state
westock-tool label shareholder_central_state --date 2026-04-10

# 多个标签同时查询（按 code 分别返回结果，非取交集）
westock-tool label valuation_lowpb,fin_high_roettm --date 2026-04-10

# 限制返回数量
westock-tool label risk_st --date 2026-04-10 --limit 50

# 分页查询（从第 20 条开始取 10 条）
westock-tool label marketcap_up10000 --limit 10 --offset 20

# 日期区间查询（仅支持单个标签，⚠️ 查多天趋势时必须用 --start/--end，不要用 --date 多次调用）
westock-tool label shareholder_central_state --start 2026-04-01 --end 2026-04-10
```

**参数说明**：

| 参数 | 是否必选 | 说明 |
|------|---------|------|
| 标签名称 | ✅ | 标签代码（短名或自定义 listcode），多个用逗号分隔 |
| `--date` | 可选 | `YYYY-MM-DD`，默认今天 |
| `--start` | 可选 | 区间查询起始日期 `YYYY-MM-DD`（需配合 `--end`，**查多天趋势时必须用此参数**） |
| `--end` | 可选 | 区间查询结束日期 `YYYY-MM-DD`（需配合 `--start`） |
| `--limit` | 可选 | 返回数量，默认 20 |
| `--offset` | 可选 | 偏移量，默认 0（用于翻页） |
| `--list` | 可选 | 列出所有可用标签，可接分组名筛选 |

> **多标签查询**：传入多个标签时，每个标签**分别返回**各自的股票列表，不做交集。如需交集，可在获取结果后自行比对。

> **自定义 code**：除了内置短名，也支持直接传入任意 listcode 字符串。新增的标签 code 即使不在内置列表中也可直接使用，无需更新工具代码。可通过 `--list` 获取最新可用标签列表。

**返回格式**：

```
标签: 央企公司 (shareholder_central_state) | 日期: 2026-04-10 | 共 128 只，显示 20 只

| code | name |
|------|------|
| sh601398 | 工商银行 |
| sh601857 | 中国石油 |
```

**可用标签概览**（11 大类 90+ 标签，通过 `--list` 获取完整列表）：

| 分类 | 数量 | 代表性标签 |
|------|------|----------|
| 股东属性 | 11 | `shareholder_central_state`(央企)、`shareholder_local_state`(国企)、`shareholder_hkfund`(陆股通) |
| 风险标签 | 4 | `risk_st`(ST股)、`risk_broken_ipo`(破发股) |
| 估值水平 | 6 | `valuation_lowpb`(破净股)、`valuation_negpe`(亏损股) |
| 资产结构 | 8 | `fin_asset_high_cash`(多现金)、`fin_asset_high_fixedasset`(多固定资产) |
| 负债结构 | 3 | `fin_liability_high_shortdebt`(多短债) |
| 利润结构 | 10 | `fin_profit_high_cost_dev`(多研发费用) |
| 现金流结构 | 7 | `fin_cash_high_cfo`(多经营现金)、`fin_cash_neg_cfo`(负经营现金) |
| 财务排名 | 28 | `fin_profitablity_high_rk_roettm`(ROE高排序)、`fin_growth_high_rk_rev_growth`(营收增速高排序) |
| 财务特征 | 12 | `fin_high_roettm`(高ROE)、`fin_forecast_inc`(业绩预增)、`fin_healthy_growth`(降本增效) |
| 上市时间 | 5 | `listeddate_5days`(新股5日内)、`listeddate_3mons`(近端次新) |
| 价格与市值 | 14 | `price_up1000`(千元股)、`marketcap_super_big`(超大盘)、`marketcap_super_small`(超小盘) |

**完整标签列表参见 [references/ai_usage_guide.md](./references/ai_usage_guide.md)**

---

## 使用规范

- ✅ 使用 `westock-tool` CLI 命令执行选股查询，命令输出 Markdown 表格，AI 直接从表格读取数据
- ✅ 查询结果应转为表格或可读格式展示，禁止直接输出原始 JSON
- ❌ 不创建临时脚本文件，不将数据分析逻辑写成独立脚本
- ⚠️ **港股必须指定 `--market hk`，美股必须指定 `--market us`**
- ⚠️ 筛选 PE/PB 时排除负值（亏损股），如 `intersect([PE_TTM > 0, PE_TTM < 20])`
- ⚠️ 沪深和港股/美股的估值字段名不同，切勿混用
- ⚠️ **市值单位**：沪深 `TotalMV` 单位为"元"，港股/美股为"亿元"，构建条件时注意换算

---

## 股票代码格式

| 市场 | 格式 | 示例 |
|------|------|------|
| 沪市/科创板 | sh + 6位数字 | `sh600519`、`sh688981` |
| 深市 | sz + 6位数字 | `sz000001` |
| 北交所 | bj + 6位数字 | `bj430047` |
| 港股 | hk + 5位数字 | `hk00700` |
| 美股 | us + 代码 | `usAAPL` |

---

## 常用字段速查

> ⚠️ 沪深 `TotalMV` 单位为"元"，港股/美股为"亿元"

| 类别 | 沪深(HS) | 港股(HK) | 美股(US) |
|------|----------|----------|----------|
| 市盈率TTM | PE_TTM | PeTTM | PeTTM |
| 市净率 | PB | PbLF | PbLF |
| 股息率TTM | DividendRatioTTM | DivTTM | DivTTM |
| 市销率TTM | PS_TTM | PsTTM ⚠️ | - |
| 市现率TTM | PCF_TTM | PcfTTM ⚠️ | - |
| 收盘价 | ClosePrice | ClosePrice | ClosePrice |
| 涨跌幅 | ChangePCT | ChangePCT | ChangePCT |
| 总市值 | TotalMV (元) | TotalMV (亿元) | TotalMV (亿元) |
| 换手率 | TurnoverRate | TurnoverRate | TurnoverRate |
| ROE(TTM) | ROETTM | RoeWeighted | ROE |
| 主力净流入 | MainNetFlow | MainNetFlow | - |

> ⚠️ 港股 PsTTM/PcfTTM 仅选股查询支持，快照查询返回 0

**完整字段速查表（含行情、财务、技术指标等全部字段）参见 [references/fields-guide.md](./references/fields-guide.md)**

**详细返回格式、分析模板参见 [references/ai_usage_guide.md](./references/ai_usage_guide.md)**

---

## 常见场景速查

```
【命令选择原则】
- 当用户提到具体策略名称（如MACD金叉、巴菲特、KDJ等）→ 使用 strategy 命令
- 当用户提到分类标签（如央企、国企、ST股、新股、次新股、破净股、高ROE等）→ 使用 label 命令
- 当用户描述自定义条件（如PE<20且ROE>15）→ 使用 filter 表达式
- 当用户提到预设函数名称（如低估值、高股息等）→ 使用 filter --preset
- ⚠️ 查询"新股"/"次新股"等标签分类 → 用 label listeddate_5days/listeddate_3mons
- ⚠️ 查询多天区间趋势 → 用 --start/--end 参数，不要用 --date 多次调用

【策略选股场景】
MACD金叉策略：strategy macd_golden
巴菲特价值投资：strategy buffet
格雷厄姆策略：strategy graham
早晨之星形态：strategy morning_star
查看所有策略：strategy --list

【标签选股场景】
查看所有标签：label --list
央企股票：label shareholder_central_state
国企股票：label shareholder_local_state
ST股：label risk_st
破净股：label valuation_lowpb
高ROE：label fin_high_roettm
陆股通持股：label shareholder_hkfund
新股（5日内）：label listeddate_5days
业绩预增：label fin_forecast_inc

【条件选股场景】
价格筛选：filter "intersect([ClosePrice >= 50, ClosePrice <= 200])"
低估值蓝筹：filter "intersect([PE_TTM > 0, PE_TTM < 15, ROETTM > 15])" --orderby ROETTM:desc
技术面选股：filter "intersect([MA_5 > MA_10, MA_10 > MA_20, MA_20 > MA_60])"
主力流入：filter "MainNetFlow > 100000000" --orderby MainNetFlow:desc
港股高股息：filter "intersect([PeTTM > 0, PeTTM < 10, DivTTM > 5])" --market hk
按板块筛选：westock-data search 半导体 sector → 获取板块代码 → filter ... --universe <代码>

【预设函数场景】（功能与策略/标签类似，但使用方式不同）
MACD金叉：filter --preset MACDGoldenCross
高股息：filter --preset HighDividend
低PE：filter --preset LowPE
高ROE：filter --preset HighROE
破净股：filter --preset LowPB
主力资金流入：filter --preset MainInflow

【联动分析】
filter "intersect([PE_TTM > 0, PE_TTM < 15, ROETTM > 15])" --date 2026-03-31 --limit 5 --orderby ROETTM:desc
         → 取结果中的股票代码（如 sh600519,sh000858）
         → westock-data quote sh600519,sh000858    # 查实时行情
         → westock-data finance sh600519,sh000858 lrb 4  # 查近4期利润表对比
```

---

## 重要声明

> ⚠️ **重要声明**：
>
> 1. 本技能仅提供客观市场数据的筛选与展示服务，所有返回数据均来源于公开市场信息，不含任何主观分析、投资评级或交易建议。
> 2. 本技能不构成证券投资咨询服务，使用本技能获取的数据不应作为投资决策的唯一依据。
> 3. 数据可能存在延迟，具体延迟时间因数据类型和市场而异，请以交易所官方数据为准。
> 4. 投资有风险，决策需谨慎。如需专业投资建议，请咨询持牌证券投资顾问机构。

**数据来源**：腾讯自选股数据接口

**数据更新频率**：

| 数据类型 | 更新频率 |
|----------|----------|
| 条件选股 | 每日收盘后更新 |
| 策略选股 | 每日收盘后更新 |
| 标签选股 | 跟随财报/公告等基础数据更新 |

---

## 附录：环境安装

**环境要求**：Node.js >= v18（脚本为单文件打包，无需 npm install）

> 本文件（SKILL.md）所在目录即为技能根目录，脚本路径为 `scripts/index.js`。

**运行方式**：
```bash
node <SKILL.md所在目录>/scripts/index.js filter "ClosePrice >= 100"
```
