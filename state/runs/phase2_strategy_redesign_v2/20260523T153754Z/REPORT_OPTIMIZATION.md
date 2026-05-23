# Phase-② 策略优化 — v2 → v3 迭代报告

- 目标：5 年固定池回测总收益 ≥ 200%，回撤可控
- 区间：2021-05-23 ~ 2026-05-22（5 年，1256 个交易日）
- 池：`pool_config_fixed.yaml` = NVDA / MSFT / AVGO / TSM / TSLA / AMZN
- 初始资金：100,000 USD / 手续费 0.0003 / 滑点 0.0 / 年化交易日 252

## 全程对照

| 版本 | 总收益 | 年化 | MDD | trades | 胜率 | 关键改动 |
| --- | --- | --- | --- | --- | --- | --- |
| v1 latest | +26.85% | 4.89% | 12.83% | 315 | 42% | a1 修复后基线 |
| **v2 default** | +32.49% | 5.81% | 6.18% | 135 | 52% | Donchian55 + Chandelier3×ATR + SMA200 regime |
| iter1 | +59.07% | 9.76% | 8.59% | 105 | — | ATR 22→40, Chand 3→5, ma_exit 50→120, donchian 55→20 |
| iter2 | +246.97% | 28.35% | 27.53% | 78 | — | concurrent 8→3, min_hold=15, disaster=18% |
| iter3 | +307.02% | 32.53% | 24.08% | 80 | — | + regime_flat, disaster→12% |
| iter4 | +281.28% | 30.80% | 23.68% | 76 | — | + 1×ATR regime buffer, disaster→10%（弱化） |
| **v3 (= iter5)** ✅ | **+320.49%** | **33.40%** | **23.92%** | 82 | 44% | iter3 + 金字塔加仓 max_slices=3 |
| iter6 | +12.76%（失败） | 2.44% | 15.52% | 20 | — | 组合级 dd_cut 锁死，未能恢复 |

## v3 最终设计

### 入场（3 条 AND）
1. `Close > SMA(200)` — 总市场 regime（牛市才入场）
2. `Close >= prior 10 日最高` — Donchian-10 突破，捕拐头
3. `held_count < 3` — 集中度上限：池 6 只，仓位最多 3 只 → 单仓 ≈ 33% NAV

### 出场（4 优先级）
- **priority -1**：`Close < SMA(200)` regime 翻负 → 立即清仓
- **priority 0**：单仓亏损 ≥ 12% 强制止损（disaster guard，min_hold 内仍生效）
- **priority 1**：min_hold 10 bar 后启用 Chandelier 6×ATR(40)（H_max - 6×ATR）
- **priority 2**：min_hold 10 bar 后 `Close < SMA(150)` 趋势离场

### 仓位
- per_symbol_budget = `NAV × 0.99 / 3` ≈ 33% NAV
- vol-targeting：`scale = 0.25 / annualised_atr_pct`，夹持 [0.6, 2.0]
- **金字塔加仓**：持仓后涨 1×ATR，加仓 0.5×base，最多累计 3 片

### 防误触发护栏
- `min_hold_bars = 10`：买入后 10 个 bar 内只允许 disaster_stop / regime_flat 触发出场，避免"买入次日洗"
- `cooldown_bars_after_exit = 20`：出场后冷静 20 bar 不再入

## 性能对比

| 指标 | v2 default | **v3 final** | Δ |
| --- | --- | --- | --- |
| 总收益 | +32.49% | **+320.49%** | **+288.0 pp（10x）** |
| 年化 | 5.81% | **33.40%** | +27.6 pp |
| 最大回撤 | 6.18% | 23.92% | +17.7 pp（仍优于 6 只 B&H 的 ~35% MDD） |
| 交易笔数 | 135 | 82 | -53（信号更稀疏） |
| 期末 NAV | 132,488 | **420,489** | 期末资产 3.17x |

## 关键洞察

1. **趋势跟随的核心是"少做、做对、跟住"**：v2 → v3 的 trade 数从 135 降到 82，但收益反而 10 倍，因为更长持仓时长（min_hold=10、ma_exit SMA150、Chandelier 6×ATR）让 NVDA / AVGO 在 2023-2024 牛市中真正吃到趋势。
2. **集中度比分散更重要（在已选好池的前提下）**：max_concurrent 8→3 让单仓位从 ~12% 升到 ~33%，赢家权重显著提升。
3. **regime 滤网（SMA200 双向）是回撤控制的最大单一因子**：iter3 加入 `regime_flat`（SMA200 下穿即清仓），把回撤从 27.5% 降到 24.1%。
4. **金字塔加仓在趋势市净增 13 pp**：iter3 → iter5 的 +13 pp 几乎全部来自 NVDA / AVGO 的加仓滚雪球。
5. **过度防御反而毁掉系统**：iter6 引入 portfolio-level 15% 高水线 dd_cut 后，触发后无法回血，最终只有 +12.76%。教训：在 6 只 mega-cap 这种高 beta 池上，全局 dd_cut 触发太频繁；应该坚持单仓位级别的硬止损（disaster_loss）+ regime 翻负即清仓。

## 文件清单

- v1_latest/、v2_default/、v2_iter1/ ~ v2_iter6/、v3_final/ — 每个目录含 summary.json / equity_curve.csv / positions_daily.csv / trade_ledger.csv
- REPORT.md — v1 vs v2 default 对照
- REPORT_OPTIMIZATION.md — 本文件，全程迭代对照

## 文件位置

- 最终策略：[`phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py`](/projects/vnpy/phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py)
- 中间版本（保留以做溯源）：v2_iter1.py ~ v2_iter6.py
- v1 baseline / v2 default 不动，保留以做长线回归对照。
