# iter7 — pyramid unlock (`max_slices` shim removal)

## TL;DR

| 版本 | 总收益 | 年化 | MDD | trades | win/loss | final_nav |
| --- | --- | --- | --- | --- | --- | --- |
| v3 baseline (shim, max_slices=1, **= 历史 v3 = iter5**) | +320.49% | 33.40% | 23.92% | 82 | 17 / 22 | 420,488.71 |
| **v3 unlocked (max_slices=3)** | **+456.77%** | **41.13%** | **27.57%** | **93** | **28 / 26** | **556,774.33** |

Δ Total Return: **+136.29 pp**  ·  Δ Annualised: **+7.73 pp**  ·  Δ MDD: **+3.65 pp**

## 背景

`global_variables()` 顶部 pyramid 段定义 `self.max_slices = 3`，但底部的"Compatibility shims"段把它**默默覆盖**回 `1`：

```python
# 顶部（设计本意）
self.max_slices = show_variable(3, GlobalType.INT)
self.pyramid_atr_step = show_variable(1.0, GlobalType.FLOAT)
self.pyramid_size_pct = show_variable(0.5, GlobalType.FLOAT)
...
# 底部（shim — 历史兼容代码遗留）
self.max_slices = show_variable(1, GlobalType.INT)   # ← 把 3 压成 1
```

实际运行时 `int(self.max_slices) == 1`，于是 `_enter_position` 里的金字塔分支：

```python
if (atr_val0 > 0 and last_entry > 0 and slices_used < slices_max
        and price >= last_entry + step * atr_val0):
    is_pyramid = True
```

`slices_used < 1` 在第一次进场后就永远 False —— **金字塔加仓从未真正发生过**。

## 修复

删除 shim 段 `self.max_slices = 1` 那一行，让顶部 `max_slices = 3` 生效。

## 证据（5 年回测 / pool_config_fixed.yaml 6 只 / 100k / fee=0.0003）

- `v3_baseline_slices1/summary.json` — shim 还在（max_slices=1）
- `v3_max_slices_3/summary.json` — shim 已删除（max_slices=3）

两次跑使用同一份策略文件，唯一差异就是 shim 那一行；池/区间/初始资金/费率全部一致。

## 解读

- **收益跳升 136 pp**：金字塔加仓让 NVDA / AVGO / TSM 等强趋势标的在 +1·ATR 后加 0.5×slice，再 +1·ATR 后再加 0.5×slice，最大可 3 片，总曝光从 1×slice 变 ~2×slice。
- **trades 82 → 93**：多出的 11 笔大部分是金字塔加仓单（`tag=加仓`）+ 对应的清仓单。
- **win / loss 17/22 → 28/26**：胜率没本质变化（金字塔单本身就是顺势加，盈亏归到母仓）；交易计数主要被加仓拆分。
- **MDD +3.65 pp**：加仓后单标的曝光更高，2022 熊市的回撤也被放大；regime_flat + disaster_stop 把它压在 27.57%（仍低于 30%）。
- **目标达成**：用户目标"5 年 ≥200%"，本次 **+456.77%**，超目标 2.28×。

## 风险与下一步

1. 27.57% MDD 处于"还能接受"边界。下一步若要继续拉收益，应优先考虑：
   - 降相关性：池里 NVDA/AVGO/TSM 三只半导体高度相关，可加 1~2 只非半导体强趋势股。
   - 加 portfolio-level dd_cut（iter6 失败的教训：阈值太严会锁死，需要 30%+ 才不误伤）。
2. 当前金字塔规则仍是固定 0.5×slice、1·ATR 步长；若要 Turtle 原版的 0.5/0.5/0.5/0.5（4 片），需进一步拉 `max_slices` 并测试。
3. 实盘前必须再次用 `--respect-live-submit` + `LIVE_SUBMIT=False` 跑一遍 alert-only 路径，确认加仓 alert 文案正确（含 `tag=加仓`）。

## 边界声明

- 本次 2 次回测均走本地 vnpy SQLite，**未连接 OpenD / Futu / 任何远端**。
- `force_live_submit=True` 仅在 backtest 引擎内翻转策略 `LIVE_SUBMIT` 让 `place_limit` 进入内存队列；**未提交任何真实订单**。
- 策略源文件最终态：max_slices=3 生效（shim 已删除）。
