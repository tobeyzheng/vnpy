# phase2 strategy redesign v2 → v3 — 任务进度

> 本文件是 `.codebuddy/plan/phase2_strategy_redesign_v2/` 的权威完成进度记录。

## 当前状态

- 阶段：**iter7 已完成 — 删除 max_slices shim 后金字塔真正生效，v3 最终回测 +456.77% / 5 年；待用户确认 git push**
- 测试：phase2/strategy/tests 共 119 个 pytest 用例 100% 通过；策略文件 `--check` 通过
- 最终入口：`phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py`（max_slices=3 生效）
- 历史对照报告：`state/runs/phase2_strategy_redesign_v2/20260523T153754Z/REPORT_OPTIMIZATION.md`
- iter7 对照报告：`state/runs/phase2_strategy_redesign_v2/20260523T161713Z/REPORT_iter7_pyramid_unlock.md`

## 5 年回测对照（pool_config_fixed.yaml 6 只 / 100k / fee=0.0003）

| 版本 | 总收益 | 年化 | MDD | trades | 备注 |
| --- | --- | --- | --- | --- | --- |
| v1 latest | +26.85% | 4.89% | 12.83% | 315 | a1 修复后基线 |
| v2 default | +32.49% | 5.81% | 6.18% | 135 | 初版趋势跟随 |
| iter1 | +59.07% | 9.76% | 8.59% | 105 | 放呼吸 |
| iter2 | +246.97% | 28.35% | 27.53% | 78 | 集中度+宽限期 |
| iter3 | +307.02% | 32.53% | 24.08% | 80 | + regime_flat |
| iter4 | +281.28% | 30.80% | 23.68% | 76 | 弱化 |
| v3 baseline (shim, max_slices=1, = 历史 iter5) | +320.49% | 33.40% | 23.92% | 82 | 金字塔被 shim 锁死 |
| iter6 | +12.76% | 2.44% | 15.52% | 20 | 失败：组合 dd_cut 锁死 |
| **v3 unlocked (= iter7, max_slices=3)** | **+456.77%** | **41.13%** | **27.57%** | **93** | **金字塔真正生效 ✅** |

## 任务进度映射

| ID | 任务 | 状态 | 关键交付 |
| --- | --- | --- | --- |
| 1 | 写 v2 策略文件 | ✅ | 489 行 |
| 2 | py_compile + `--check` | ✅ | LIVE_SUBMIT=False ships |
| 3 | adapter 兼容性冒烟 | ✅ | load_futumd_strategy 加载成功 |
| 4 | phase2 测试套件 119/119 | ✅ | 全绿 |
| 5 | 5 年回测 v1 latest | ✅ | +26.85% 与历史 a1 一致（可重复） |
| 6 | 5 年回测 v2 default | ✅ | +32.49% |
| 7 | REPORT.md 对照 | ✅ | 已生成 |
| 8 | 同步 docs | ✅ | system_integration_guide / project_operation_log / adaptive_quant_engine_design |
| 9 | git add + push | ⏳ | 等待用户确认 |
| 10 | 优化迭代 iter1~iter6 | ✅ | 6 个文件保留作归因证据 |
| 11 | v3 最终版（=iter5） | ✅ | +320.49% / MDD 23.92% / 82 trades，超 200% 目标 |
| 12 | REPORT_OPTIMIZATION.md | ✅ | 全程对照写入产物目录 |
| 13 | iter7 — 删除 max_slices shim | ✅ | 金字塔真正生效，+456.77% / MDD 27.57% / 93 trades |
| 14 | REPORT_iter7_pyramid_unlock.md | ✅ | 双档对照（baseline_slices1 vs max_slices_3） |

## 隔离与安全约束（运行期不变量）

- v3 / 全部 iter 文件：单文件、stdlib only、零本地 import、私有 helper、无磁盘 IO。
- LIVE_SUBMIT 仍硬开关 False；引擎 force_live_submit 仅写 in-memory 队列。
- 本轮 7 次回测全部为本地 vnpy 数据库读取，无任何远端连接、无任何真实订单。
