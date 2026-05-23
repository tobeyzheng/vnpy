# Phase-② 策略重构 v2 — A/B 回测报告

- 时间戳：20260523T153754Z
- 区间：2021-05-23 ~ 2026-05-22（5 年，1256 个交易日）
- 池：`pool_config_fixed.yaml` = NVDA / MSFT / AVGO / TSM / TSLA / AMZN（6 只锁定）
- 初始资金：100,000 USD
- 手续费：0.0003 / 滑点：0.0 / 年化交易日：252
- 引擎：`phase2.runners.run_phase2_multi_backtest`（force_live_submit=True，仅写 in-memory 队列）

## 关键指标对照

| 指标 | v1_latest（含 a1 修复） | v2_default（趋势跟随重构） | Δ |
| --- | --- | --- | --- |
| total_return_pct | 26.8495 | **32.4884** | **+5.64 pp** |
| annualised_return_pct | 4.8875 | **5.8068** | +0.92 pp |
| max_drawdown_pct | 12.8314 | **6.1755** | **-6.66 pp（回撤减半）** |
| trade_count | 315 | 135 | -180 |
| win_count / loss_count | 66 / 91 | 34 / 31 | win-rate 42% → **52%** |
| final_nav | 126,849.52 | **132,488.39** | +5,638.87 |
| final_cash | 101,372.62 | 89,807.48 | -11,565（资金部署率提高） |

## v2 设计亮点（vs v1）

| 模块 | v1 | v2 |
| --- | --- | --- |
| 入场闸门 | 5 条 AND（MA / RSI / vol_ratio / ATR-cap / concurrent） | 3 条 AND（Regime SMA200 / Long SMA100 / Donchian55 突破） |
| 总市场滤网 | 无 | Close > SMA(200)，熊市禁入 |
| 止损 | 固定 5% | Chandelier `max_since_entry - 3·ATR(22)` |
| 止盈 | 固定 10% + trailing 5% | 删除（仅 chandelier + 趋势离场） |
| 趋势离场 | fast-slow 死叉 | Close < SMA(50) |
| 预算公式 | `NAV × 0.8 / 5 × 0.1` ≈ 1.6%/笔 | `NAV × 0.95 / 6` ≈ 15.8%/笔（无双重缩放） |
| vol-targeting | 无 | `target_vol=0.15`，scale ∈ [0.4, 1.5] |
| 加仓 | min_add_interval/loss 链 | 单仓位无加仓（让趋势跑） |

## 解读

1. **回撤减半** 是趋势跟随系统在 mega-cap 池上的典型优势：Chandelier 跟随
   止损让止损价跟着 ATR 自适应放宽，叠加 Donchian 入场只在新高出现时
   买入，使得策略避开了 v1 在 2022 熊市的多次抓刀。
2. **交易数 -57%、胜率 +10pp** 印证 v2 的信号更挑剔：v1 用 RSI / vol_ratio
   作为硬入场闸门导致大量"中等质量"信号通过；v2 把入场标准从"指标 OK"
   收紧为"价格创出新高 + 处于上升 regime"。
3. **总收益 +5.64pp 但仍远低于 B&H** —— 6 只龙头池 5 年 B&H 在 +200%~+300%
   区间。这是趋势跟随系统的固有限制（牺牲收益换稳定）；如需追求更高
   收益，需要换 cross-sectional momentum 或允许杠杆，属另一计划范畴。
4. v1_latest 总收益 26.85% 与历史 `fixed_pool_5y_a1` (26.85%) 完全一致 →
   回测可重复，环境无污染。

## 文件清单

- v1_latest/{summary.json, equity_curve.csv, positions_daily.csv, trade_ledger.csv}
- v2_default/{summary.json, equity_curve.csv, positions_daily.csv, trade_ledger.csv}
- REPORT.md（本文件）

## 后续可能动作（非本计划范围）

- 让优化器 self_optimize 跑 v2 search_space（需新增搜索空间 YAML）。
- 调试 chandelier_k / donchian_in 网格寻找更优默认。
- 引入 cross-sectional momentum 排序，在 6 只内做横截面选股。
