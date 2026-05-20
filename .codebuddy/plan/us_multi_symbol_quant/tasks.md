# 实施计划

> **总边界**：本计划仅产出研究/设计 markdown 文档；**不修改任何策略源码、不运行回测/下单脚本、不执行 git 推送**（除非用户在执行前明确确认）。每条任务严格围绕 [requirements.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/requirements.md) 的某个 AC，且仅创建/修改下列两个目录中的文件：
> - `docs/research/us_multi_symbol_quant/`（研究/方案交付物）
> - `docs/project_operation_log.md`（仅追加一条变更摘要）
> - `.codebuddy/task_list/us_multi_symbol_quant.md`（进度同步）

- [ ] 1. 初始化研究文档骨架与汇总入口
  - 在 `docs/research/us_multi_symbol_quant/` 下新建 `00_index.md`、`01_indicator_research.md`、`02_futu_multi_symbol_capability.md`、`03_strategy_design.md` 四个空骨架文件，仅包含一级标题与 TOC 占位
  - 在 `00_index.md` 中写明阅读顺序（指标 → 平台 → 方案）、边界与非目标（直接对齐 `requirements.md` 需求 5）
  - 4 份文档之间用相对路径互相链接
  - 不写任何具体内容（具体内容由后续任务填充），目的在于先固定骨架与链接关系，避免后续多文档间链接错位
  - _需求: 4.1, 4.2_

- [ ] 2. 撰写美股有效指标调研报告 `01_indicator_research.md`
- [ ] 2.1 五维度章节框架与指标条目表模板
  - 按 design.md 组件 1 给出的"维度划分"建立 5 个一级章节：趋势 / 动量 / 波动率 / 量能 / 横截面
  - 在每个一级章节顶部插入"指标条目表模板"说明，统一字段：name / dimension / params_daily / params_hourly / regime_fit / partners / pitfalls / futu_api / futu_ref_line / self_impl_cost
  - _需求: 1.1, 1.2_

- [ ] 2.2 填充趋势 / 动量 / 波动率 / 量能 四类指标条目
  - 每个条目均按模板给出"计算口径 + 参数 + 适用状态 + 组合伙伴 + 陷阱"
  - 重点指标：MA、EMA、MACD、SAR、ADX/DMI、RSI、KDJ、CCI、ROC、AROON、ATR、BOLL、HV、OBV、VWAP、VMACD
  - **每条 Futu API 名/行号在写入前必须经 `grep_search` 在 [futu_quant.md](/projects/vnpy/tmp/futu_quant.md) 二次校验**（禁止凭记忆填写），未命中的指标在 `futu_api` 字段填"麦语言自实现"并给出 `self_impl_cost`
  - _需求: 1.1, 1.2, 1.3, 1.4_

- [ ] 2.3 横截面/选股因子（简介性章节）
  - 简要介绍 12-1 动量、Beta、低波因子、规模/价值/质量因子，标注"基本面/财报因子非本轮目标"
  - 标注横截面因子在 Futu 平台的实现路径（在 `handle_data` 中遍历 trig 标的自行排序），并在条目表中将 `futu_api` 标注为"自实现（业务层）"
  - _需求: 1.1, 1.2, 1.4, 5.1_

- [ ] 2.4 输出 8–15 个核心指标"短名单"
  - 按"日线优先"和"小时线优先"两栏列出
  - 每个上榜指标给一句话推荐理由 + 与短名单内其他指标的搭配关系
  - _需求: 1.5_

- [ ] 3. 撰写 Futu 多标的能力评估 `02_futu_multi_symbol_capability.md`
- [ ] 3.1 构建能力矩阵主表
  - 按 design.md 组件 2 的能力矩阵结构填表：行=能力项，列=Futu 内建/麦语言/外部补齐，单元格 ✅/⚠️/❌ + 简注
  - 至少覆盖：多标的驱动（≤50）、跨 symbol 调用同一指标、横截面排序选股、多 symbol 同时下单、多市场混合回测、麦语言注册→多 symbol 复用
  - _需求: 2.1, 2.7_

- [ ] 3.2 关键事实清单（带 futu_quant.md 行号）
  - 单策略最多 50 个 trig 标的（行号引用）
  - `handle_data` 由所有 trig 行情驱动（行号引用）
  - 内建指标 API 形参均含 `symbol=Contract('US.XXXX')`（举例：`ma`、`rsi`、`atr_atr`、`boll_upper/mid/lower`、`vwap`、`obv` 等，每条带行号）
  - 麦语言通过 `register_indicator + get_MyLang_indicator(symbol=...)` 多 symbol 复用（行号引用）
  - **每条事实写入前必须用 `grep_search` 在 `futu_quant.md` 中二次确认**
  - _需求: 2.1, 2.2, 2.3_

- [ ] 3.3 多标的下下单/持仓查询/资金竞争分析
  - 列出 `place_limit`、`close_positions`、`position_holding_qty`、`net_asset`、`cash` 在多标的下的 symbol 维度参数
  - 描述同一根 K 线对多 symbol 同时下单的资金竞争与订单幂等风险，给出建议（先按"组合本金锁定"切片，再按 symbol 顺序顺次下单）
  - _需求: 2.4_

- [ ] 3.4 回测能力对比（Futu vs 本地）
  - 描述 Futu 平台回测对多标的的现状、bar 时间对齐、跨市场（US+HK）混合回测的可行性
  - 与本地 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) 路径做能力差异表（撮合精度、滑点、数据源、跨市场支持、可视化）
  - _需求: 2.5_

- [ ] 3.5 限制清单与对需求 3 方案的阻塞判定
  - 把 3.1–3.4 中所有 ⚠️/❌ 项汇总成"限制清单"
  - 逐条标注是否阻塞需求 3 中的某条规则；若阻塞，必须在 `03_strategy_design.md` 中对应规则给出降级方案
  - _需求: 2.6_

- [ ] 4. 撰写多标的策略方案 `03_strategy_design.md`
- [ ] 4.1 顶部"非可执行声明"与目标/约束
  - 在文档顶部加显著声明：本文为研究/设计文档，不含可直接运行代码；落地需按 plan 流程发起
  - 给出可量化目标（年化、Sharpe、最大回撤、单标的暴露），数值留待回测校准
  - _需求: 3.1, 5.2, 5.3_

- [ ] 4.2 标的池设计
  - 给出 5–20 只大盘流动美股候选清单（含用户已有 NVDA），说明为何 ≤50（贴合 Futu 上限）
  - 提供"半固定池"维护机制（季度调整 / 流动性阈值）
  - _需求: 3.2_

- [ ] 4.3 信号体系（4 因子 + 横截面排序）
  - 趋势：MA20/MA60 多头排列、MACD 金叉
  - 动量：RSI 从超卖回升、KDJ 低位金叉
  - 波动率：ATR 区间过滤、BOLL 中轨支撑
  - 量能：VWAP/OBV 同向、量比 ≥ 1.2
  - 横截面打分：各因子 0–100 分加权，日线收盘后选 top-N，给伪代码示意
  - _需求: 3.3_

- [ ] 4.4 仓位与资金管理（份制扩展到组合）
  - 单标的最大 5 份（沿用 NVDA 经验）
  - 组合层面 = top-N × 1 份首仓
  - 单标的首仓基于"组合本金锁定值（base_capital）"切片，避免随浮盈漂移；与 [us_nvda_1d_strategy_multifactor.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_multifactor.py) 的修复思路一致
  - 现金缓冲与最大组合暴露
  - _需求: 3.4_

- [ ] 4.5 风控规则（≥4 条）与可行性标注
  - 单标的止损 5%
  - 组合层面回撤熔断 ≥ 12%
  - ATR 极端过滤
  - 相关性过滤（top-N 中两两 corr > 0.85 仅留分高者）
  - 每条规则标注 Futu 内建 / 麦语言 / 自实现 + 是否被 3.5 限制清单阻塞 + 降级方案
  - _需求: 3.5, 3.8_

- [ ] 4.6 回测与评估方法
  - **双轨**：① Futu 平台回测，② 本地 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) 交叉验证
  - 评估指标：年化、Sharpe、Sortino、最大回撤、Calmar、胜率、PF、平均持仓天数、与 SPY/QQQ 基准对齐
  - 给出"两侧结果偏差容忍范围"的判定标准（沿用之前 NVDA Futu vs 本地对齐讨论的经验）
  - _需求: 3.6_

- [ ] 4.7 三阶段上线路径
  - 阶段 ①：单标的多因子（NVDA 基线，已有）
  - 阶段 ②：固定股票池多标的，相同规则跑 N 个 symbol
  - 阶段 ③：横截面排序选股，每根 K 线动态选 top-N
  - 与现有策略关系声明：不修改任何 [tmp/strategy/](file:///projects/vnpy/tmp/strategy) 下文件
  - _需求: 3.7, 3.9_

- [ ] 5. 自检与流程合规
- [ ] 5.1 完备性 + 事实一致性自检
  - 用一张表对照 `requirements.md` 中每条 AC ↔ 在 `00/01/02/03` 文档中的章节或表格条目
  - 抽样 ≥10 条 Futu API 名/行号 → 用 `grep_search` 在 `futu_quant.md` 中二次确认
  - _需求: 1.1, 1.2, 1.3, 2.1, 2.2, 4.1_

- [ ] 5.2 文档可读性 + 敏感信息自检
  - 4 份 markdown 之间链接全部可点击（相对路径）
  - 全文不出现真实账号、完整资金、密钥；涉及资金示例使用脱敏占位符或区间
  - _需求: 4.4_

- [ ] 5.3 同步 `docs/project_operation_log.md`
  - 在 `docs/project_operation_log.md` 末尾追加一条变更摘要：日期、变更范围（仅文档）、关键文件、影响摘要（不改代码、不触发回测/下单/远端推送）
  - _需求: 4.3_

- [ ] 5.4 同步 `.codebuddy/task_list/us_multi_symbol_quant.md`
  - 创建对应任务进度文件，列出本计划所有任务节点与状态
  - 标注当前执行焦点；后续状态变更以该文件为权威来源
  - _需求: 4.1_

- [ ] 6. 交付前确认门（不进行任何破坏性动作）
  - 明确告知用户：本任务全程只产出文档；**不会**执行回测脚本、不会连接 OpenD、不会下单、不会 `git add/commit/push`
  - 若用户在交付后另行要求 git 提交/推送，则按"自动提交与推送规则"+"实际执行前确认规则"，先给出影响范围说明并等待"确认提交推送"后再执行
  - _需求: 4.5, 5.2, 5.3_
