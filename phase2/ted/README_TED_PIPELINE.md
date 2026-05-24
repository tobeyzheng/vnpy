# TED进攻型标池发现流水线使用说明

## 概述

Trend Explosion Discovery (TED) 是一套专门用于发现类似闪迪、美光等强趋势暴涨标的的进攻型标池发现策略。方案C采用三阶段流水线设计，确保高质量候选标的发现。当前主目录为 `phase2/ted/`。

## 方案C：三阶段流水线

### 第一阶段：Knot四维研究召回
- **目标**：通过Knot LLM从技术面、基本面、资金流、事件驱动四个维度发现强趋势候选
- **入口脚本**：`phase2/ted/run_knot_4dim_research.py`
- **轻量验证**：支持 `--validate-only`，仅校验脚本可用性，不触发远端研究
- **输出**：`/projects/vnpy/state/runs/ted/YYYYMMDDTHHMMSS/stage1_knot_4dim/knot_4dim_us.json`

### 第二阶段：候选准备召回
- **目标**：结合Knot种子和市场数据，生成动态候选池
- **入口脚本**：`phase2/ted/run_candidate_preparation.py`
- **策略**：`knot_first` + `momentum_cta`
- **输出**：归档候选报告和动态候选文件

### 第三阶段：TED进攻型标池发现
- **目标**：基于AEOS评分系统，生成最终进攻型标池
- **入口脚本**：`phase2/ted/run_ted_discovery.py`
- **输出**：进攻型标池报告和 `pool_config.yaml` 导出文件

## 快速开始

### 方式1：完整流水线（推荐）
```bash
cd /projects/vnpy
python3 phase2/ted/run_ted_full_pipeline.py
```

### 方式2：先做Knot轻量验证
```bash
cd /projects/vnpy
python3 phase2/ted/run_knot_4dim_research.py --validate-only
```

### 方式3：分步执行
```bash
cd /projects/vnpy
python3 phase2/ted/run_knot_4dim_research.py
python3 phase2/ted/run_candidate_preparation.py
python3 phase2/ted/run_ted_discovery.py
```

### 方式4：显式指定同一批次结果目录
```bash
cd /projects/vnpy
python3 phase2/ted/run_knot_4dim_research.py --run-dir /projects/vnpy/state/runs/ted/20260524T083000
python3 phase2/ted/run_candidate_preparation.py --run-dir /projects/vnpy/state/runs/ted/20260524T083000
python3 phase2/ted/run_ted_discovery.py --run-dir /projects/vnpy/state/runs/ted/20260524T083000
```

## 入口脚本详解

### 1. `phase2/ted/run_knot_4dim_research.py`
专门用于Knot四维研究召回的独立脚本。

**功能特点：**
- 调用 `/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_us.py`
- 自动创建 `TED` 专属日期目录保存结果
- 检查Knot脚本存在性
- 支持 `--validate-only` 轻量校验模式
- 兼容底层 `dimensions` 结果结构

**输出文件：**
- `/projects/vnpy/state/runs/ted/YYYYMMDDTHHMMSS/stage1_knot_4dim/knot_4dim_us.json`
- `/projects/vnpy/log/knot_4dim_research.log`

### 2. `phase2/ted/run_candidate_preparation.py`
专门用于候选准备召回的独立脚本。

**功能特点：**
- 调用 `/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py`
- 使用进攻型参数：`knot_first` + `momentum_cta`
- 自动检查同批次 `TED` 第一阶段结果
- 在保留 `state/runs/` 权威产物的同时，归档一份 `TED` 独立快照

**参数配置：**
```python
--market us
--strategy knot_first
--top-n 24
--knot-target-count 36
--universe-preset momentum_cta
--include-market-data
```

**输出文件：**
- `/projects/vnpy/state/runs/ted/YYYYMMDDTHHMMSS/stage2_candidate_preparation/candidate_inputs.prepare.report.us.json`
- `/projects/vnpy/state/runs/ted/YYYYMMDDTHHMMSS/stage2_candidate_preparation/candidate_inputs.dynamic.us.json`
- `/projects/vnpy/state/runs/candidate_inputs.prepare.report.us.json`（兼容/权威原始路径）
- `/projects/vnpy/state/runs/candidate_inputs.dynamic.us.json`（兼容/权威原始路径）
- `/projects/vnpy/log/candidate_preparation.log`

### 3. `phase2/ted/run_ted_discovery.py`
TED进攻型标池发现主脚本。

**功能特点：**
- 基于AEOS评分系统筛选标的
- 生成核心进攻池、观察池、备用池
- 输出详细发现报告
- 额外导出一份兼容 `phase2` 标的池加载器的 `pool_config.yaml`
- 从 `phase2.ted` 子包导入核心模块

**AEOS评分权重：**
- 30% Breakout / Trend
- 20% Relative Strength
- 15% Flow / Participation
- 15% Event Freshness
- 10% Theme Leadership
- 10% Liquidity Quality
- -10% Exhaustion Risk
- -10% Structural Risk

**输出文件：**
- `/projects/vnpy/state/runs/ted/YYYYMMDDTHHMMSS/stage3_ted_discovery/ted_aggressive_pool_report.json`
- `/projects/vnpy/state/runs/ted/YYYYMMDDTHHMMSS/stage3_ted_discovery/pool_config.yaml`
- `/projects/vnpy/state/runs/ted_aggressive_pool_report.json`（兼容镜像）
- `/projects/vnpy/state/runs/ted_pool_config.yaml`（兼容镜像）
- `/projects/vnpy/log/ted_discovery.log`

### 4. `phase2/ted/run_ted_full_pipeline.py`
完整流水线脚本，自动执行三个阶段。

**功能特点：**
- 按顺序执行三个阶段
- 同一轮流水线共享同一个 `TED` 日期目录
- 错误处理和状态检查
- 统一的日志管理
- 调用 `phase2/ted/` 下的新入口脚本

## TED结果目录结构

```text
/projects/vnpy/state/runs/ted/YYYYMMDDTHHMMSS/
├── manifest.json
├── stage1_knot_4dim/
│   └── knot_4dim_us.json
├── stage2_candidate_preparation/
│   ├── candidate_inputs.prepare.report.us.json
│   └── candidate_inputs.dynamic.us.json
└── stage3_ted_discovery/
    ├── ted_aggressive_pool_report.json
    └── pool_config.yaml
```

## 输出报告结构

### TED进攻型标池报告
```json
{
  "generated_at": "2026-05-24T07:44:15",
  "strategy": "Trend Explosion Discovery v1",
  "pool_summary": {
    "total_candidates": 45,
    "core_pool_count": 8,
    "watch_pool_count": 12,
    "reserve_pool_count": 10
  },
  "core_pool": [
    {
      "symbol": "NVDA",
      "name": "NVIDIA Corporation",
      "aeos_score": 0.892,
      "change_pct": 15.3,
      "market_cap": 2500000000000,
      "turnover": 45000000
    }
  ],
  "watch_pool": [...],
  "reserve_pool": [...]
}
```

### pool_config 导出示例
```yaml
version: 1
currency: USD
max_pool_size: 20

defaults:
  market: US
  liquidity_min_adv60_usd: 50000000
  price_min: 5.0
  price_max: 800.0
  atr_pct_max: 0.08
  earnings_freeze_days: 2

symbols:
  - symbol: DELL
    market: US
    market_cap_bucket: large
    sector: Technology
```

## 日志文件

- `knot_4dim_research.log` - Knot四维研究日志
- `candidate_preparation.log` - 候选准备日志
- `ted_discovery.log` - TED发现日志
- `ted_full_pipeline.log` - 完整流水线日志

## 注意事项

### 执行前确认
根据项目规则 [[memory:u5ntfvz3]]，以下操作需要用户明确确认：
- 连接远端Knot/LLM服务
- 批量候选生成
- 写本地产物到 `state/runs/` 目录

### 可直接执行的轻量验证
根据项目约定 [[memory:nsddgfvd]]，以下验证可以直接执行：
- `python3 phase2/ted/run_knot_4dim_research.py --validate-only`
- `python3 -m py_compile ...`
- 只读/不落关键产物的导入和语法检查

## 故障排除

### 常见问题
1. **Knot脚本不存在**：检查`/projects/vnpy/scripts/quant_workflow/`目录
2. **候选准备失败**：检查Futu连接状态和参数配置
3. **TED发现异常**：检查候选数据文件是否存在

### 调试模式
```bash
cd /projects/vnpy
python3 -v phase2/ted/run_ted_discovery.py
```

## 版本历史

- **v1.2** (2026-05-24): TED结果统一归档到 `state/runs/ted/YYYYMMDDTHHMMSS/`，新增分阶段结果快照与 `pool_config.yaml` 导出
- **v1.1** (2026-05-24): 重构到 `phase2/ted/` 独立目录，新增 Knot 轻量验证入口
- **v1.0** (2026-05-24): 初始版本，支持方案C三阶段流水线

## 联系方式

如有问题，请检查相关日志文件或联系项目维护者。