# 01 美股有效指标调研

> 本文档为研究性文档，**不含可执行代码**。所有 Futu API 名与行号均已通过 `grep_search` 在 [futu_quant.md](/projects/vnpy/tmp/futu_quant.md) 二次校验。
>
> 关联：[00 汇总入口](./00_index.md) ｜ [02 平台能力评估](./02_futu_multi_symbol_capability.md) ｜ [03 策略方案](./03_strategy_design.md)

## 1. 摘要

本报告面向"美股大盘流动股 + 多标的轮动"场景，从 **趋势 / 动量 / 波动率 / 量能 / 横截面** 五个维度梳理常用有效指标，并明确每个指标在 Futu 量化平台的可用性（内建 API / 麦语言自实现 / 业务层自实现）。最终给出 8–15 个核心指标的"短名单"作为 [03 策略方案](./03_strategy_design.md) 的输入。

研究结论先行：

- **Futu 内建一线指标覆盖度高**：MA、EMA、MACD、SAR、RSI、KDJ、CCI、ROC、ATR、BOLL、HV、OBV、VWAP 均直接支持多标的（API 形参均含 `symbol=Contract('US.XXXX')`）。
- **DMI/ADX、AROON、VMACD、Williams %R 需麦语言自实现**：通过 `register_indicator + get_MyLang_indicator` 注册一次后可对多 symbol 复用。
- **横截面/排序类因子需业务层自实现**：在 `handle_data` 内对运行标的池遍历，自行计算每标的指标值并排序。

## 2. 调研口径与方法

- **覆盖周期**：日线为主、小时线为辅（贴合现有 NVDA 日线策略基线）。
- **市场范围**：美股大盘流动股；不含基本面财报因子、不含期权/衍生品。
- **筛选标准**：①经典实证有效；②可在 Futu 平台 ≤50 个运行标的下高效计算；③与现有策略组合无重大冲突。
- **数据源**：所有 Futu API 名与行号均严格引自 [futu_quant.md](/projects/vnpy/tmp/futu_quant.md)。

## 3. 趋势类指标

> 适用市场状态：✅ 趋势行情（强方向性）；⚠️ 震荡行情容易反复假突破。

### 3.1 MA / EMA — 移动平均

| 字段 | 内容 |
|------|------|
| name | MA / EMA（Moving Average / Exponential MA） |
| dimension | 趋势 |
| params_daily | MA20/MA60/MA120；EMA12/EMA26 |
| params_hourly | MA20/MA60；EMA12/EMA26 |
| regime_fit | ✅ 趋势 / ⚠️ 震荡 |
| partners | MACD（共用 12/26 参数）、量能确认 |
| pitfalls | 滞后；横盘期间频繁金叉/死叉 |
| futu_api | `ma()` / `ema()` / `is_ma_bullish_alignment()` / `is_ema_bullish_alignment()` |
| futu_ref_line | `ma` 第 10 行；`ema` 第 130 行；`is_ma_bullish_alignment` 第 72 行；`is_ema_bullish_alignment` 第 191 行 |
| self_impl_cost | 低（直接调用） |

### 3.2 MACD — 平滑异同移动平均

| 字段 | 内容 |
|------|------|
| name | MACD |
| dimension | 趋势（含动量） |
| params_daily | (12, 26, 9) — 经典；(5, 13, 5) — 短周期 |
| params_hourly | (12, 26, 9) |
| regime_fit | ✅ 趋势 / ⚠️ 震荡（频繁假交叉） |
| partners | MA 多头排列、量能放大 |
| pitfalls | 滞后；横盘高频假信号；零轴穿越 vs 信号线交叉口径需统一 |
| futu_api | `macd_dif()` / `macd_dea()` / `macd_macd()` / `is_macd_golden_cross()` / `is_macd_death_cross()` |
| futu_ref_line | `macd_dif` 第 955 行；`macd_dea` 第 995 行；`macd_macd` 第 1028 行；`is_macd_golden_cross` 第 1075 行；`is_macd_death_cross` 第 1115 行 |
| self_impl_cost | 低 |

### 3.3 SAR — 抛物线转向

| 字段 | 内容 |
|------|------|
| name | SAR |
| dimension | 趋势（止损位） |
| params_daily | step=0.02, max=0.2 |
| params_hourly | step=0.02, max=0.2 |
| regime_fit | ✅ 强趋势 / ❌ 震荡（频繁翻转） |
| partners | MA、ADX |
| pitfalls | 震荡市频繁翻转，必须配 ADX 过滤 |
| futu_api | `sar()` / `is_sar_up_trend()` / `is_sar_bullish_reversal()` |
| futu_ref_line | `sar` 第 433 行；`is_sar_up_trend` 第 249 行；`is_sar_bullish_reversal` 第 353 行 |
| self_impl_cost | 低 |

### 3.4 ADX / DMI — 平均趋向

| 字段 | 内容 |
|------|------|
| name | ADX / DMI |
| dimension | 趋势强度 |
| params_daily | period=14；ADX > 25 视为趋势市 |
| params_hourly | period=14 |
| regime_fit | ✅ 用于"是否处于趋势"的开关 |
| partners | MACD/SAR（让 ADX 作过滤器） |
| pitfalls | ADX 滞后明显，仅用作过滤而非择时 |
| futu_api | 麦语言自实现，通过 `get_MyLang_indicator()` 调用 |
| futu_ref_line | DMI 章节第 1276 行；麦语言通用接口 `get_MyLang_indicator` 形参第 453 行 |
| self_impl_cost | 中（需写 ~10 行麦语言并 `register_indicator`） |

## 4. 动量类指标

> 适用市场状态：⚠️ 趋势中"超买"易顶背离；✅ 震荡中超买/超卖反转。

### 4.1 RSI — 相对强弱指数

| 字段 | 内容 |
|------|------|
| name | RSI |
| dimension | 动量 |
| params_daily | period=14；超卖 < 30、超买 > 70 |
| params_hourly | period=12 |
| regime_fit | ✅ 震荡 / ⚠️ 强趋势中长期处于极端区不发生反转 |
| partners | 价格底背离、KDJ |
| pitfalls | 强趋势中超卖继续跌（"接飞刀"）；建议用"从超卖回升"代替"在超卖" |
| futu_api | `rsi()` / `is_rsi_golden_cross()` / `is_rsi_death_cross()` / `is_rsi_top_divergence()` / `is_rsi_bottom_divergence()` |
| futu_ref_line | `rsi` 第 3007 行；`is_rsi_golden_cross` 第 2860 行；`is_rsi_death_cross` 第 2899 行；`is_rsi_top_divergence` 第 2938 行；`is_rsi_bottom_divergence` 第 2976 行 |
| self_impl_cost | 低 |

### 4.2 KDJ — 随机指标

| 字段 | 内容 |
|------|------|
| name | KDJ |
| dimension | 动量 |
| params_daily | (9, 3, 3) |
| params_hourly | (9, 3, 3) |
| regime_fit | ✅ 震荡 / ⚠️ 强趋势 |
| partners | RSI、价格形态 |
| pitfalls | 短周期高频假信号；J 值容易突破 0–100 区间 |
| futu_api | `kdj_k()` / `kdj_d()` / `kdj_j()` / `is_kdj_golden_cross()` |
| futu_ref_line | `kdj_k` 第 2588 行；`kdj_d` 第 2628 行；`kdj_j` 第 2668 行；`is_kdj_golden_cross` 第 2435 行 |
| self_impl_cost | 低 |

### 4.3 CCI — 顺势指标

| 字段 | 内容 |
|------|------|
| name | CCI |
| dimension | 动量 |
| params_daily | period=20；±100 为多空分界，±200 极端 |
| params_hourly | period=14–20 |
| regime_fit | ✅ 强趋势中适合作动量过滤；⚠️ 震荡中频繁假突破 |
| partners | MA、ATR |
| pitfalls | 极端市场可冲到 ±400，需做参数稳定性测试 |
| futu_api | Futu 内建（CCI 章节） |
| futu_ref_line | CCI 章节第 2710 行 |
| self_impl_cost | 低 |

### 4.4 ROC — 变动率

| 字段 | 内容 |
|------|------|
| name | ROC（Rate of Change） |
| dimension | 动量 |
| params_daily | period=12 / 25 |
| params_hourly | period=12 |
| regime_fit | ✅ 横截面动量打分（12-1 月动量经典） |
| partners | 横截面排序、MA |
| pitfalls | 单标的择时弱；更适合多标的横截面对比 |
| futu_api | Futu 内建（ROC 章节） |
| futu_ref_line | ROC 章节第 3376 行 |
| self_impl_cost | 低 |

### 4.5 AROON — 阿隆指标

| 字段 | 内容 |
|------|------|
| name | AROON |
| dimension | 动量（趋势识别） |
| params_daily | period=25；Aroon Up/Down ≥ 70 视为强势 |
| params_hourly | period=14–25 |
| regime_fit | ✅ 趋势识别；与 ADX 互补 |
| partners | ADX、价格突破 |
| pitfalls | 对周期参数敏感 |
| futu_api | 麦语言自实现 |
| futu_ref_line | AROON 章节第 2050 行；`get_MyLang_indicator` 形参第 453 行 |
| self_impl_cost | 中 |

## 5. 波动率类指标

> 适用市场状态：用于"过滤" — 极端波动期信号噪音大；与仓位管理强相关。

### 5.1 ATR — 真实波动幅度

| 字段 | 内容 |
|------|------|
| name | ATR |
| dimension | 波动率 |
| params_daily | period=14 |
| params_hourly | period=14 |
| regime_fit | ✅ 趋势 / ✅ 震荡（皆可作仓位/止损因子） |
| partners | 仓位管理、动态止损 |
| pitfalls | 单独使用无方向信息；适合作过滤器/仓位因子 |
| futu_api | `atr_tr()` / `atr_atr()` |
| futu_ref_line | `atr_tr` 第 1603 行；`atr_atr` 第 1641 行 |
| self_impl_cost | 低 |

### 5.2 BOLL — 布林带

| 字段 | 内容 |
|------|------|
| name | BOLL |
| dimension | 波动率 + 区间 |
| params_daily | period=20, deviation=2 |
| params_hourly | period=20, deviation=2 |
| regime_fit | ✅ 震荡（区间反转）；✅ 趋势（带宽收缩→扩张突破） |
| partners | 量能、ATR |
| pitfalls | 强趋势沿轨而行不回归；不能仅看"触上下轨" |
| futu_api | `boll_upper()` / `boll_mid()` / `boll_lower()` / `is_boll_cross_above_upper()` 等 |
| futu_ref_line | `boll_upper` 第 4992 行；`boll_mid` 第 5031 行；`boll_lower` 第 5070 行；BOLL 章节第 4827 行 |
| self_impl_cost | 低 |

### 5.3 HV — 历史波动率

| 字段 | 内容 |
|------|------|
| name | HV（Historical Volatility） |
| dimension | 波动率 |
| params_daily | period=20 / 30 |
| params_hourly | period=20 |
| regime_fit | ✅ 用于波动率过滤、横截面低波因子 |
| partners | ATR、仓位管理 |
| pitfalls | 滞后；与隐含波动率（IV）口径不同 |
| futu_api | `historical_volatility()` / `historical_volatility_30d()` |
| futu_ref_line | `historical_volatility` 第 913 行；`historical_volatility_30d` 第 7707 行 |
| self_impl_cost | 低 |

## 6. 量能类指标

> 适用市场状态：作为价格信号的"确认器" — 无量则信号弱。

### 6.1 OBV — 能量潮

| 字段 | 内容 |
|------|------|
| name | OBV |
| dimension | 量能（累积资金流） |
| params_daily | 无参数（直接累加） |
| params_hourly | 无参数 |
| regime_fit | ✅ 价量背离识别；✅ 趋势确认 |
| partners | 价格趋势、MACD |
| pitfalls | 美股盘前盘后量参与口径需明确（用 RTH 还是 ALL） |
| futu_api | Futu 内建（OBV 章节） |
| futu_ref_line | OBV 章节第 4216 行 |
| self_impl_cost | 低 |

### 6.2 VWAP — 成交量加权均价

| 字段 | 内容 |
|------|------|
| name | VWAP |
| dimension | 量能（机构成本基准） |
| params_daily | 单日 VWAP |
| params_hourly | 滚动 VWAP |
| regime_fit | ✅ 日内交易锚点；✅ 多标的相对强弱（价 vs VWAP） |
| partners | OBV、量比 |
| pitfalls | 跨日 VWAP 需自定义；session_type 选择影响很大 |
| futu_api | `vwap()` |
| futu_ref_line | VWAP 章节第 3987 行；`vwap` 第 3989 行 |
| self_impl_cost | 低 |

### 6.3 VMACD — 量能 MACD

| 字段 | 内容 |
|------|------|
| name | VMACD |
| dimension | 量能（量能版 MACD） |
| params_daily | (12, 26, 9) |
| params_hourly | (12, 26, 9) |
| regime_fit | ✅ 量价共振确认 |
| partners | 价格 MACD（双 MACD 同向） |
| pitfalls | 与价格 MACD 高相关，避免重复使用 |
| futu_api | 麦语言自实现 |
| futu_ref_line | VMACD 章节第 1368 行；`get_MyLang_indicator` 形参第 453 行 |
| self_impl_cost | 中 |

## 7. 横截面 / 选股因子（简介性）

横截面因子用于"在多个标的间排序选股"，与单标的择时不同。Futu 平台无内建排序 API，需在 `handle_data` 内对运行标的池遍历后自行排序（业务层自实现）。

| 因子 | 描述 | 实现路径 |
|------|------|----------|
| 12-1 动量 | 过去 12 个月收益（剔除最近 1 个月反转） | 业务层：用 `ma`/`roc` + 自管理历史 |
| 短期反转 | 过去 1 周/1 月负相关 | 业务层 |
| 低波因子 | 取 HV 排名最低的 N 个 | 业务层 + `historical_volatility` |
| Beta | 与 SPY 的回归系数 | 业务层（需引入指数序列） |
| 流动性 | 成交额排名 | 业务层（用 vol/turnover） |
| 趋势强度 | ADX 排名 | 业务层 + 麦语言 ADX |

> **基本面 / 财报因子（PE / PB / ROE / 营收增速 / 现金流）**：本研究**非目标**，参见 [00_index.md 非目标](./00_index.md)。

## 8. 短名单（核心指标）

> 选取标准：①Futu 直接可用或自实现成本低；②实证有效；③彼此低相关。

### 8.1 日线优先（首推 — 对齐现有 NVDA 日线策略）

| 排名 | 指标 | 维度 | 推荐理由 | 搭配 |
|------|------|------|----------|------|
| 1 | MA(20/60) + 多头排列 | 趋势 | 简单稳定、Futu 直接给"多头排列"判定 | MACD、量能 |
| 2 | MACD(12,26,9) | 趋势/动量 | 经典共振指标，金叉/死叉/零轴穿越 | MA、OBV |
| 3 | RSI(14) + 从超卖回升 | 动量 | 避免接飞刀，对齐策略已有改进经验 | 价格底背离 |
| 4 | ATR(14) | 波动率 | 用于动态仓位/止损/极端过滤 | 仓位管理 |
| 5 | BOLL(20,2) — 中轨 | 波动率/趋势 | 中轨支撑、带宽收缩→扩张作突破信号 | 量能 |
| 6 | OBV | 量能 | 价量背离识别，确认信号有效性 | MACD |
| 7 | VWAP | 量能 | 机构成本基准、多标的相对强弱 | 量比 |
| 8 | HV(20) | 波动率 | 横截面低波因子、波动率过滤 | ATR |
| 9 | ADX(14) — 麦语言 | 趋势强度 | 作"是否趋势市"开关，过滤 SAR/MACD 假信号 | SAR、MACD |
| 10 | ROC(12) | 横截面动量 | 多标的轮动选股的核心因子 | 横截面排序 |

### 8.2 小时线优先（备用 — 提升信号频率）

| 排名 | 指标 | 维度 | 推荐理由 |
|------|------|------|----------|
| 1 | EMA(12/26) | 趋势 | EMA 对小时级反应更快 |
| 2 | MACD(12,26,9) | 趋势/动量 | 同日线 |
| 3 | RSI(12) | 动量 | 短周期超买超卖 |
| 4 | KDJ(9,3,3) | 动量 | 小时级超买超卖反转 |
| 5 | BOLL(20,2) | 波动率 | 日内区间交易 |
| 6 | VWAP | 量能 | 日内锚点 |
| 7 | ATR(14) | 波动率 | 日内仓位/止损 |

## 9. 附录：Futu 内建指标 API 速查（行号引用）

| 类别 | 指标 | API | futu_quant.md 行号 |
|------|------|-----|--------------------|
| 趋势 | MA | `ma` | 10 |
| 趋势 | EMA | `ema` | 130 |
| 趋势 | SAR | `sar` / `is_sar_up_trend` | 433 / 249 |
| 趋势 | MACD | `macd_dif` / `macd_dea` / `macd_macd` | 955 / 995 / 1028 |
| 趋势 | MACD 信号 | `is_macd_golden_cross` / `is_macd_death_cross` | 1075 / 1115 |
| 趋势 | DMI/ADX | 麦语言 (DMI 章节) | 1276 |
| 动量 | RSI | `rsi` | 3007 |
| 动量 | RSI 信号 | `is_rsi_golden_cross` / `is_rsi_death_cross` | 2860 / 2899 |
| 动量 | RSI 背离 | `is_rsi_top_divergence` / `is_rsi_bottom_divergence` | 2938 / 2976 |
| 动量 | KDJ | `kdj_k` / `kdj_d` / `kdj_j` | 2588 / 2628 / 2668 |
| 动量 | KDJ 信号 | `is_kdj_golden_cross` | 2435 |
| 动量 | CCI | (CCI 章节) | 2710 |
| 动量 | ROC | (ROC 章节) | 3376 |
| 动量 | AROON | 麦语言 (AROON 章节) | 2050 |
| 波动率 | ATR | `atr_tr` / `atr_atr` | 1603 / 1641 |
| 波动率 | BOLL | `boll_upper` / `boll_mid` / `boll_lower` | 4992 / 5031 / 5070 |
| 波动率 | HV | `historical_volatility` / `historical_volatility_30d` | 913 / 7707 |
| 量能 | OBV | (OBV 章节) | 4216 |
| 量能 | VWAP | `vwap` | 3989 |
| 量能 | VMACD | 麦语言 (VMACD 章节) | 1368 |
| 麦语言通用 | 注册/调用 | `register_indicator` + `get_MyLang_indicator` | 453（API 形参示例） |
