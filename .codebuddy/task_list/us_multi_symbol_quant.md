# us_multi_symbol_quant — 任务进度

> 本文件为本计划的**权威完成进度记录**（依据"项目规则 3"）。计划骨架与任务编号详见 [tasks.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/tasks.md)。

## 当前执行焦点

- **阶段**：✅ **全部任务（1–6）已完成** — 等待用户决定是否提交推送
- **总边界**：本计划全程仅产出研究/设计 markdown 文档；未改代码、未跑回测、未下单、未 git push（push 需用户单独确认）
- **下一步**：用户回复"确认提交推送"后，按规则 1 执行 `git add -A` + 简洁中文 commit + `git push`；或回复"暂不推送"则保留本地变更

## 任务进度

- [x] 1. 初始化研究文档骨架与汇总入口
  - 已创建 `docs/research/us_multi_symbol_quant/` 下 4 份骨架文件
- [x] 2. 撰写美股有效指标调研报告 `01_indicator_research.md`
  - [x] 2.1 五维度章节框架与指标条目表模板
  - [x] 2.2 填充趋势 / 动量 / 波动率 / 量能 四类指标条目（15 条 API 全部 grep 校验通过）
  - [x] 2.3 横截面/选股因子（简介性章节）
  - [x] 2.4 输出 8–15 个核心指标"短名单"（日线 10 条 + 小时线 7 条）
- [x] 3. 撰写 Futu 多标的能力评估 `02_futu_multi_symbol_capability.md`
  - [x] 3.1 构建能力矩阵主表（含 50 标的限制、跨 symbol 复用、麦语言复用）
  - [x] 3.2 关键事实清单（带 futu_quant.md 行号 14890 / 14914-14916 / 453 等）
  - [x] 3.3 多标的下下单/持仓查询/资金竞争分析（含预算分配与横截面业务层实现要点）
  - [x] 3.4 回测能力对比（Futu vs 本地）
  - [x] 3.5 限制清单与对需求 3 方案的阻塞判定：不存在硬阻塞
- [x] 4. 撰写多标的的策略方案 `03_strategy_design.md`
  - [x] 4.1 顶部“非可执行声明” + 目标与约束（含脱敏资金占位符 NAV）
  - [x] 4.2 标的的池设计（来源、过滤规则、更新频率）
  - [x] 4.3 信号体系（4 因子 + 横截面排序）
  - [x] 4.4 仓位与资金管理（份制扩展到组合 + 预算分配伪代码）
  - [x] 4.5 风控规则（×12 条：单标 4 + 组合 5 + 系统 3）
  - [x] 4.6 回测与评估方法（双轨 + 6 个评估维度 + 防过拟合）
  - [x] 4.7 三阶段上线路径（M1–M11 里程碑 + 退出门槛）
- [x] 5. 自检与流程合规
  - [x] 5.1 完备性 + 事实一致性自检（非可执行声明齐全、双向连通性、Futu API 行号抽检 5 处全部通过）
  - [x] 5.2 文档可读性 + 敏感信息自检（全文脱敏扫描命中数 = 0）
  - [x] 5.3 同步 [docs/project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)（已追加 2026-05-20 一条）
  - [x] 5.4 同步本进度文件（本轮）
- [x] 6. 交付前确认门（未进行任何破坏性动作）
  - [x] 6.1 交付物清单核对（4 研究文档 + 3 plan 文件 + 1 task_list = 8 份，共 1439 行）
  - [x] 6.2 风险与已知遗留汇总（5 项，全部已记录到 03 §9.3 / 02 §5.2 / 00 §5.2）
  - [x] 6.3 落地路径建议（阶段 ②/③ 走独立 plan）
  - [x] 6.4 提交推送前确认门（待用户明示）

## 产物清单

研究/方案交付物：

- [00_index.md](/projects/vnpy/docs/research/us_multi_symbol_quant/00_index.md) — ✅ 已完成（含 §5 研究合规检查清单）
- [01_indicator_research.md](/projects/vnpy/docs/research/us_multi_symbol_quant/01_indicator_research.md) — ✅ 已完成
- [02_futu_multi_symbol_capability.md](/projects/vnpy/docs/research/us_multi_symbol_quant/02_futu_multi_symbol_capability.md) — ✅ 已完成
- [03_strategy_design.md](/projects/vnpy/docs/research/us_multi_symbol_quant/03_strategy_design.md) — ✅ 已完成

plan 流程文件：

- [requirements.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/requirements.md) — 77 行
- [design.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/design.md) — 215 行
- [tasks.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/tasks.md) — 136 行

文档同步点：

- [docs/project_operation_log.md](/projects/vnpy/docs/project_operation_log.md) — 已追加 2026-05-20 一条研究产物记录

## 交付汇总（任务 6 输出）

### 交付物体量

| 文件 | 行数 | 状态 |
|------|------|------|
| `00_index.md` | 117 | ✅ 含 §5 合规检查清单 |
| `01_indicator_research.md` | 335 | ✅ 15 条 API 行号 grep 校验 |
| `02_futu_multi_symbol_capability.md` | 174 | ✅ 含限制清单与不阻塞判定 |
| `03_strategy_design.md` | 332 | ✅ 含 4 因子 / 12 风控 / 双轨回测 / 三阶段上线 |
| `requirements.md` | 77 | ✅ |
| `design.md` | 215 | ✅ |
| `tasks.md` | 136 | ✅ |
| `task_list/us_multi_symbol_quant.md`（本文件） | — | ✅ |
| **合计** | **1439 行**（不含本文件） | ✅ |

### 风险与已知遗留（待落地阶段验证）

| # | 风险项 | 来源章节 | 处置 |
|---|--------|---------|------|
| R1 | 50 标的 `handle_data` 单次执行耗时未实测 | 02 §5.2 第 1 项 / 03 §9.3 | 阶段 ② 启动前用 10 标的预演实测 |
| R2 | 多 symbol 串行下单延迟未测 | 02 §5.2 第 2 项 / 03 §9.3 | 阶段 ② SIM 跑量校验 |
| R3 | Futu 平台回测引擎对 50 标的耗时未测 | 02 §5.2 第 3 项 / 03 §9.3 | 阶段 ① 末期对照本地双轨 |
| R4 | 横截面排序需业务层自实现（无原生 API） | 02 §3.3 / 03 §3.2 | 阶段 ③ 用最小可行版本 |
| R5 | 顶层 `system_integration_guide.md` 暂未反向引用本研究目录 | 00 §5.2 已知遗留 | 落地阶段独立 plan 补全（属主集成入口变更，超出本计划边界） |

### 落地路径建议

本计划仅交付**研究/设计文档**，不对应任何运行入口。后续若决定落地，建议路径：

1. **阶段 ①（M1–M3）**：保持现有 NVDA 单标的策略不变，用 03 §6 的本地双轨在 5–10 标的池上做参数搜索 — **走独立新 plan**，不复用本计划；
2. **阶段 ②（M4–M7）**：扩到 20–30 标的固定池，按 03 §4.2 实现预算分配业务层 — **走独立新 plan**，并在该 plan 中补全 R5 顶层文档反向引用；
3. **阶段 ③（M8–M11）**：扩到 50 标的横截面排序 — **走独立新 plan**，验证 R1/R2/R3 性能项后再启动。

**重要**：以上 3 个阶段均**不得直接修改**现有 [us_nvda_1d_strategy_multifactor.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_multifactor.py) 等单标的策略源码（与 03 §8 的边界一致），新功能均通过新建 multi-symbol 入口实现。

### 提交推送状态

- **本计划改动文件清单**（本轮所有任务累计）：
  - 4 份研究文档：[00_index.md](/projects/vnpy/docs/research/us_multi_symbol_quant/00_index.md)、[01_indicator_research.md](/projects/vnpy/docs/research/us_multi_symbol_quant/01_indicator_research.md)、[02_futu_multi_symbol_capability.md](/projects/vnpy/docs/research/us_multi_symbol_quant/02_futu_multi_symbol_capability.md)、[03_strategy_design.md](/projects/vnpy/docs/research/us_multi_symbol_quant/03_strategy_design.md)
  - 3 份 plan 文件：[requirements.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/requirements.md)、[design.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/design.md)、[tasks.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/tasks.md)
  - 1 份 task_list（本文件）
  - 1 份文档同步：[project_operation_log.md](/projects/vnpy/docs/project_operation_log.md)
- **未改动**：任何代码（含策略 / runner / scripts / services / tests）、`state/runs/`、`docs/system_integration_guide.md`、`docs/adaptive_quant_engine_design.md`
- **未执行**：任何回测、SIM/REAL 下单、KnotAgent 调用、OpenD 连接、定时任务启动
- **下一步**：等待用户回复 `确认提交推送` 后执行 `git add -A` + 简洁中文 commit + `git push`；或回复 `暂不推送` 则保留本地变更不做远端操作。
