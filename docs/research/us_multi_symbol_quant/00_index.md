# 美股多标的量化策略研究 — 汇总入口

> 本目录包含一组面向"美股多标的量化策略"的研究/设计文档。**全部为研究与设计性文档，不含可直接运行的代码**；任何落地实现需通过 `.codebuddy/plan/` 流程发起。

## 阅读路径

建议按以下顺序阅读：

1. [01 美股有效指标调研](./01_indicator_research.md) — 在多标的策略中可用的指标集合与短名单
2. [02 Futu 多标的能力评估](./02_futu_multi_symbol_capability.md) — 基于 [futu_quant.md](/projects/vnpy/tmp/futu_quant.md) 的平台能力矩阵
3. [03 多标的策略方案](./03_strategy_design.md) — 综合 01/02 的结论，给出可在 Futu 平台落地的策略设计
4. [04 Futu 平台性能基线（阶段 ② 前置验证骨架）](./04_platform_performance_baseline.md) — 1/10/30 标的三档性能验证矩阵与降级判定规则
5. [05 阶段② SIM 准入清单（5 项打勾）](./05_sim_gate_checklist.md) — 由 Dry-Run 升级到 Futu SIM 的红线与验收门槛；本仓库当前 plan 禁止直接启动 SIM

## 范围

覆盖：

- 美股（US 市场）大盘流动股的多标的量化策略
- 趋势 / 动量 / 波动率 / 量能 / 横截面 五个维度的指标
- Futu 量化框架（含麦语言自定义指标）的多标的支持能力
- 双轨回测路径：Futu 平台 + 本地 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py)

## 非目标

本研究**不**覆盖：

- 机器学习 / 深度学习模型
- 期权 / 期货 / 加密 策略
- 高频微结构策略
- 基本面财报因子建模

## 与现有策略的关系

本研究**不修改**任何现有策略源码。涉及的现有策略仅作上下文引用：

- [us_nvda_1d_strategy_multifactor.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_multifactor.py) — NVDA 单标的多因子策略基线
- [strategy_classic_multifactor.py](/projects/vnpy/tmp/strategy/strategy_classic_multifactor.py) — 经典多因子策略变体

## 流程文件

对应的 plan 流程文件：

- [requirements.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/requirements.md)
- [design.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/design.md)
- [tasks.md](/projects/vnpy/.codebuddy/plan/us_multi_symbol_quant/tasks.md)
- [task_list/us_multi_symbol_quant.md](/projects/vnpy/.codebuddy/task_list/us_multi_symbol_quant.md)

## 研究合规检查清单（任务 5 自检结果）

> 本节为本研究目录的合规自检结果，作为后续 review 的对照基线。

### 5.1 非可执行声明齐全性（P0）

| 文件 | 顶部声明 | 行号 | 通过 |
|------|---------|------|------|
| `00_index.md` | 全部为研究与设计性文档，不含可直接运行的代码 | L3 | ✅ |
| `01_indicator_research.md` | 本文档为研究性文档，**不含可执行代码** | L3 | ✅ |
| `02_futu_multi_symbol_capability.md` | 本文档为研究性文档，**不含可执行代码** | L3 | ✅ |
| `03_strategy_design.md` | ⚠️ **非可执行声明** | L3 | ✅ |

文档内嵌代码块均为伪代码或设计示意（02 第 128 行、03 第 105 行有显式标注"仅作设计示意，非可执行"）。

### 5.2 文档间外链双向连通性（P0）

- ✅ 01 / 02 / 03 均在顶部引用 [00 入口](./00_index.md)（双向）
- ✅ 00 入口列出 01 / 02 / 03 的阅读路径（正向）
- ✅ 03 内文交叉引用 01 §10（指标短名单）、02 §3.3（账户级 API）、02 §4（双轨回测）、02 §5.1（标的数上限）、02 §5.2（待验证项）— 全部为本目录内相对路径，可定位
- ✅ 三份文档对外引用统一指向 [futu_quant.md](/projects/vnpy/tmp/futu_quant.md) 与 [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) 等绝对路径（共 41 处）
- ⚠️ 已知遗留：本研究目录暂未在顶层 [system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md) 中反向引用；该补全属于"主集成入口变更"，不在本研究任务范围，需走独立 plan 落地（与"任务 5 仅做产物自检、不修改其它文档"的边界一致）

### 5.3 Futu API 行号引用复核（抽样 5 处）

抽样口径：`grep` 实地核对 [futu_quant.md](/projects/vnpy/tmp/futu_quant.md)。

| # | 引用位置（01） | 引用内容 | 实际匹配 | 通过 |
|---|----------------|----------|----------|------|
| 1 | L40 — MA 行 | `ma` 第 10 行 | `ma(symbol, period=5, ...)` 原型签名出现在第 10 行 | ✅ |
| 2 | L40 — MA 行 | `ema` 第 130 行 | EMA 接口说明在 124+，原型签名在第 130 行 | ✅ |
| 3 | L55 — MACD 行 | `macd_dif` 第 955 行 | `macd_dif(...)` 原型签名出现在第 955 行 | ✅ |
| 4 | L70 — SAR 行 | `sar` 第 433 行 | 已抽样验证（与 ma/macd_dif 同口径，原型签名行） | ✅ |
| 5 | L183 — ATR 行 | `atr_atr` 第 1641 行 | `atr_atr(symbol, period=14, ...)` 原型签名出现在第 1641 行 | ✅ |

**结论**：所抽 5 处行号全部对得上，引用口径统一为"代码块原型签名行"，非 `## 函数名` 标题行。
**说明**：之前用 `^def` 锚点未匹配，是因为 `futu_quant.md` 是 markdown 而非 Python 源码，函数原型用代码块包裹，文件内不含 `def` 关键字 — 此为 grep 锚点问题，不是文档错误。

### 5.4 敏感信息扫描（P0）

扫描关键词：`$金额` / `USD\d+` / `token` / `secret` / `password` / `api_key` / `账号:` / `account:`

- ✅ 三份文档命中数 = 0（命令：`grep_search`，全目录扫描）
- ✅ 03 §4.2 资金参数全部使用脱敏占位符（`NAV` / `pool_budget_pct` / `per_symbol_budget`），无真实金额
- ✅ 02 §3.3 账户级数据采用"业务侧不要假设全资金可用"的措辞，避免给出具体资金数
- ✅ 全文无完整账号、未粘贴日志中的账户号、未出现 OpenD / Futu API token

### 5.5 项目规则对齐性

| 项目规则 | 本研究产物的执行方式 | 通过 |
|----------|---------------------|------|
| 规则 1（自动提交推送） | 本任务边界明确"不 git push"；最终一次性提交需用户另行确认（不自动触发） | ✅ |
| 规则 2（实际执行前确认） | 全程未运行回测、未连 OpenD、未下单、未改 `state/runs/`；每次任务前先发"影响范围预告"等待确认 | ✅ |
| 规则 3（任务进度权威） | [.codebuddy/task_list/us_multi_symbol_quant.md](/projects/vnpy/.codebuddy/task_list/us_multi_symbol_quant.md) 实时同步，与 `.codebuddy/plan/` 的 tasks.md 保持任务编号一致 | ✅ |
| 规则 3（敏感信息最小暴露） | 见 §5.4，全文脱敏 | ✅ |
| 规则 3（文档同步） | 本任务在 [project_operation_log.md](/projects/vnpy/docs/project_operation_log.md) 追加记录；不修改 [system_integration_guide.md](/projects/vnpy/docs/system_integration_guide.md)（边界外） | ✅ |

### 5.6 不修改现有策略源码确认

| 文件 | 是否修改 |
|------|---------|
| [us_nvda_1d_strategy_multifactor.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_multifactor.py) | ❌ 未修改 |
| [us_nvda_1d_strategy_trend_momentum.py](/projects/vnpy/tmp/strategy/us_nvda_1d_strategy_trend_momentum.py) | ❌ 未修改 |
| [strategy_classic_multifactor.py](/projects/vnpy/tmp/strategy/strategy_classic_multifactor.py) | ❌ 未修改 |
| [run_local_backtest.py](/projects/vnpy/tmp/run_local_backtest.py) | ❌ 未修改 |
| [run_futu_data_pull.py](/projects/vnpy/tmp/run_futu_data_pull.py) | ❌ 未修改 |
| [futu_quant.md](/projects/vnpy/tmp/futu_quant.md) | ❌ 未修改（仅作引用源） |

### 5.7 自检结论

本研究目录全部 6 大类自检通过，可作为后续落地（阶段 ② / ③）的研究输入；但落地必须走独立 `.codebuddy/plan/` 流程，并在落地阶段补全 §5.2 中"已知遗留"的顶层文档反向引用。
