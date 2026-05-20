# 需求文档

## 引言

本需求面向"美股多标的量化策略"研究与设计任务，目标是产出一份可落地的研究文档，覆盖三个层面：

1. **指标调研**：调研美股量化领域被实证验证、长期被主流机构与量化社区使用的有效技术/量价/波动率/横截面指标，给出适用场景、参数惯例、常见陷阱与组合用法。
2. **平台能力评估**：基于 [futu_quant.md](/projects/vnpy/tmp/futu_quant.md) 文档，系统性评估 Futu 量化框架（含 `declare_trig_symbol`、`declare_strategy_type`、内建指标 API、麦语言自定义指标、回测/实盘运行机制）对**多标的（multi-symbol）量化策略**的支持范围与边界，识别可行做法与受限点。
3. **策略方案设计**：在前两步结论之上，给出一套可在 Futu 平台落地、专注美股多标的的量化策略方案，包含选股池、信号、仓位、风控、回测验证步骤，以及与现有 `us_nvda_1d_strategy_multifactor.py` 等单标的策略的关系与升级路径。

**重要约束**：本阶段仅产出研究/设计文档，不修改任何已有策略代码，不触发任何 SIM/REAL 交易动作；后续若需落地实现，将通过单独的 task-item 与执行流程进行，并遵循"实际执行前确认规则"。

## 需求

### 需求 1 — 美股有效指标调研报告

**用户故事：** 作为策略开发者，我希望获得一份"美股量化领域常用且经过实证有效"的指标清单与说明，以便在设计多标的策略时能在合理候选集中做加减，不必从零罗列上百个技术指标。

#### 验收标准

1. WHEN 用户阅读调研报告 THEN 文档 SHALL 至少覆盖以下五个维度：趋势（如 MA/EMA、MACD、ADX/DMI、SAR、Ichimoku）、动量（如 RSI、KDJ/Stochastic、ROC、Williams %R、CCI）、波动率（如 ATR、BOLL/Keltner、HV）、量能（如 OBV、VWAP、A/D Line、Volume Ratio）、横截面/选股（如 12-1 动量、Beta、低波因子、规模/价值/质量因子简介）。
2. WHEN 报告列出某个指标 THEN 该条目 SHALL 至少包含：定义/计算口径、典型参数（含日线与小时线常用值）、适用市场状态（趋势/震荡/事件驱动）、常见组合伙伴（与哪些指标搭配能降低假信号）、已知陷阱（前视/过拟合/横盘失效等）。
3. WHEN 报告给出指标推荐 THEN 文档 SHALL 标注每个指标在 Futu 平台是否有"内建 API"或必须走"麦语言自定义指标"，并标记其在 [futu_quant.md](/projects/vnpy/tmp/futu_quant.md) 中的章节位置或函数名（如 `macd_dif`、`atr_atr`、`get_MyLang_indicator(...)`）。
4. IF 某指标在 Futu 框架下不被支持或需自行实现 THEN 文档 SHALL 明确给出"自实现成本评级（低/中/高）"与替代方案。
5. WHEN 报告完成 THEN 文档 SHALL 给出一个"短名单"（建议 8–15 个），即在美股日线/小时线、单标的与多标的场景下推荐优先采用的核心指标集。

### 需求 2 — Futu 平台多标的支持能力评估

**用户故事：** 作为策略开发者，我希望系统性了解 Futu 量化框架对"多标的策略"的支持程度与边界，以便决定方案在 Futu 内做到什么粒度、哪些环节需要外部数据/外部回测兜底。

#### 验收标准

1. WHEN 评估文档完成 THEN 文档 SHALL 基于 [futu_quant.md](/projects/vnpy/tmp/futu_quant.md) 明确以下事实：单策略最多可声明的运行标的数量（文档载明上限 50）、`declare_trig_symbol()` 的调度模型（每个标的的行情推送驱动 `handle_data` 运行）、是否支持在同一次 `handle_data` 中跨标的访问 K 线与指标。
2. WHEN 评估"指标调用是否可跨标的" THEN 文档 SHALL 验证并说明：内建指标 API（如 `ma()`、`macd_dif()`、`rsi`、`atr_atr`、`boll`）的 `symbol` 参数是否允许传入任意 `Contract('US.XXXX')`，从而在多标的策略中对不同 symbol 调用同一指标函数。
3. WHEN 评估"自定义指标在多标的下的可用性" THEN 文档 SHALL 明确 `register_indicator` + `get_MyLang_indicator(symbol=...)` 在多标的下的注册一次/多 symbol 复用模型。
4. WHEN 评估"下单与持仓查询" THEN 文档 SHALL 明确 `place_limit`、`close_positions`、`position_holding_qty`、`net_asset`、`cash` 等接口在多标的下的 symbol 维度参数与并发约束（包含已知风险：同一根 K 线对多个 symbol 同时下单、资金竞争、订单幂等）。
5. WHEN 评估"回测引擎" THEN 文档 SHALL 说明 Futu 回测对多标的的支持现状，包括：是否对所有运行标的统一驱动、各 symbol 的 bar 时间是否对齐、跨市场（如 US + HK）混合回测的可行性，以及与本地 `run_local_backtest.py` 路径的能力差异。
6. IF Futu 对某些多标的能力存在限制 THEN 文档 SHALL 明确列出限制条目，并标注"是否阻塞需求 3 中的方案落地"。
7. WHEN 评估完成 THEN 文档 SHALL 给出一个"能力矩阵"表（行：能力项；列：Futu 内建/麦语言/外部补齐；单元格：✅/⚠️/❌ + 简注）。

### 需求 3 — 美股多标的量化策略方案

**用户故事：** 作为策略开发者，我希望基于上面两步的结论得到一份"可在 Futu 上跑起来的"美股多标的量化策略方案，以便后续按方案分阶段落地，而不是反复推翻设计。

#### 验收标准

1. WHEN 方案文档完成 THEN 文档 SHALL 至少包含以下章节：标的池设计、信号体系、仓位与资金管理、风控规则、回测与评估方法、上线路径。
2. WHEN 方案描述"标的池" THEN 文档 SHALL 给出一个固定或半固定的美股池（建议大盘流动性股 + 用户已有持仓 NVDA 等），数量控制在 5–20 个之间，并说明为何不超过 50（贴合 Futu 上限）。
3. WHEN 方案描述"信号体系" THEN 文档 SHALL 给出"趋势 + 动量 + 波动率 + 量能"的多因子组合范式，并以伪代码或公式形式列出每个因子的入场/加仓/离场规则；至少包含一个"横截面排序选股"模块（在多个标的中按因子打分挑选 top-N）。
4. WHEN 方案描述"仓位与资金管理" THEN 文档 SHALL 明确：单标的最大仓位上限、组合层面最大暴露、加仓节奏（参考已有 `us_nvda_1d_strategy_multifactor.py` 的份制思路）、组合层面的现金缓冲。
5. WHEN 方案描述"风控规则" THEN 文档 SHALL 给出至少 4 条规则：单标的止损、组合层面回撤熔断、波动率过滤（避免极端 ATR 周期开仓）、相关性过滤（避免高相关 symbol 同向重仓），并标注每条规则的实现可行性（Futu 内建 / 自实现）。
6. WHEN 方案描述"回测与评估" THEN 文档 SHALL 给出回测指标清单（年化、Sharpe、最大回撤、胜率、PF、单笔期望、与 SPY/QQQ 基准对比），并明确"先 Futu 平台回测、再本地回测交叉验证"的双轨流程，与已有 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) 的衔接方式。
7. WHEN 方案描述"上线路径" THEN 文档 SHALL 划分至少 3 个阶段：① 单标的多因子（已有 NVDA 策略基线）→ ② 固定股票池多标的（同一套规则跑 N 个 symbol）→ ③ 横截面排序选股（每根 K 线动态选 top-N）。
8. IF 方案中的某条规则需要 Futu 当前不支持的能力 THEN 文档 SHALL 标注降级方案（如：用麦语言近似、用外部本地回测验证后再上 Futu 实盘）。
9. WHEN 方案完成 THEN 文档 SHALL 严格不修改任何现有策略源码（包括 `tmp/strategy/` 下文件），所有产出仅为 markdown 设计文档。

### 需求 4 — 文档结构、产物位置与流程合规

**用户故事：** 作为协作者，我希望本次产物在仓库内有清晰、可检索、易回溯的存放路径与同步记录，以便后续继续推进。

#### 验收标准

1. WHEN 产出最终交付 THEN 文档 SHALL 由以下几个 markdown 组成且互相链接：① 指标调研报告、② Futu 多标的能力评估、③ 多标的策略方案、④ 一份汇总入口文档。
2. WHEN 选择存放路径 THEN 文档 SHALL 优先存放于 `docs/` 下专门子目录（建议 `docs/research/us_multi_symbol_quant/`），以遵循"框架/策略文档放 docs/"的项目惯例；plan 与 task-item 文件保留在 `.codebuddy/plan/us_multi_symbol_quant/` 下。
3. WHEN 涉及策略与运行入口的影响 THEN 文档 SHALL 在产出后同步在 `docs/project_operation_log.md` 中追加一条变更摘要（日期、变更范围、关键文件、影响摘要）。
4. WHEN 涉及输出敏感信息 THEN 文档 SHALL 不出现真实账号、完整资金、密钥等敏感字段；涉及资金示例时使用脱敏占位符或区间。
5. IF 后续需要执行真实代码改动、回测运行、Git 提交推送 THEN 流程 SHALL 严格遵守"实际执行前确认规则"，先向用户说明影响范围并等待明确确认，再执行。

### 需求 5 — 边界与非目标

**用户故事：** 作为利益相关方，我希望明确本次任务的边界，避免范围蔓延。

#### 验收标准

1. WHEN 用户阅读本需求 THEN 文档 SHALL 明确以下"非目标"：不做机器学习/深度学习模型、不做期权/期货/加密策略、不做高频微结构策略、不做基本面财报因子建模。
2. WHEN 用户阅读本需求 THEN 文档 SHALL 明确"本轮只交付研究 + 设计文档"，不交付可运行策略源码与回测结果。
3. IF 用户后续要求把方案中的某一阶段落地为代码 THEN 该工作 SHALL 通过新一轮 plan/task 流程发起，不在本轮范围内完成。
