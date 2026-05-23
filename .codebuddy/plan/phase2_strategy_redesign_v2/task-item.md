# Phase-② 策略重构 v2 — 任务清单

| ID | 任务 | 关键交付 |
| --- | --- | --- |
| 1 | 写 v2 策略文件 | `phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v2.py` |
| 2 | py_compile + `--check` 自检 | stdout 含 `[futumd-strategy-v2] LIVE_SUBMIT=False` |
| 3 | adapter 兼容性冒烟 | `load_futumd_strategy` 能加载 v2，`handle_data` 无异常 |
| 4 | 现有 phase2 测试套件全绿 | `pytest phase2/strategy/tests/` 119 项通过 |
| 5 | 5 年区间真实回测 v1 default | `state/runs/phase2_strategy_redesign_v2/<ts>/v1_default/` |
| 6 | 5 年区间真实回测 v2 default | `state/runs/phase2_strategy_redesign_v2/<ts>/v2_default/` |
| 7 | 写对照 REPORT.md | 总/年化/MDD/胜率/B&H gap |
| 8 | 同步 docs | system_integration_guide / adaptive_quant_engine_design / project_operation_log |
| 9 | git add + 中文 commit + push | 远端同步 |
