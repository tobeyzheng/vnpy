# TED进攻型标池发现流水线使用说明

## 概述

Trend Explosion Discovery (TED) 是一套专门用于发现类似闪迪、美光等强趋势暴涨标的的进攻型标池发现策略。方案C采用三阶段流水线设计，确保高质量候选标的发现。

## 方案C：三阶段流水线

### 第一阶段：Knot四维研究召回
- **目标**：通过Knot LLM从技术面、基本面、资金流、事件驱动四个维度发现强趋势候选
- **入口脚本**：`run_knot_4dim_research.py`
- **输出**：`/projects/vnpy/log/YYYYMMDDHH/knot_4dim_us.json`

### 第二阶段：候选准备召回
- **目标**：结合Knot种子和市场数据，生成动态候选池
- **入口脚本**：`run_candidate_preparation.py`
- **策略**：`knot_first` + `momentum_cta`
- **输出**：候选报告和动态候选文件

### 第三阶段：TED进攻型标池发现
- **目标**：基于AEOS评分系统，生成最终进攻型标池
- **入口脚本**：`run_ted_discovery.py`
- **输出**：进攻型标池报告

## 快速开始

### 方式1：完整流水线（推荐）
```bash
cd /projects/vnpy/phase2
python3 run_ted_full_pipeline.py
```

### 方式2：分步执行
```bash
# 步骤1：Knot四维研究
cd /projects/vnpy/phase2
python3 run_knot_4dim_research.py

# 步骤2：候选准备
python3 run_candidate_preparation.py

# 步骤3：TED发现
python3 run_ted_discovery.py
```

### 方式3：独立运行TED发现
```bash
# 仅运行TED发现（需要已有候选数据）
cd /projects/vnpy/phase2
python3 run_ted_discovery.py
```

## 入口脚本详解

### 1. run_knot_4dim_research.py
专门用于Knot四维研究召回的独立脚本。

**功能特点：**
- 调用`/projects/vnpy/scripts/quant_workflow/run_knot_4dim_picks_us.py`
- 自动创建日期目录保存结果
- 检查Knot脚本存在性
- 输出详细日志

**输出文件：**
- `/projects/vnpy/log/YYYYMMDDHH/knot_4dim_us.json`
- `/projects/vnpy/log/knot_4dim_research.log`

### 2. run_candidate_preparation.py
专门用于候选准备召回的独立脚本。

**功能特点：**
- 调用`/projects/vnpy/scripts/quant_workflow/run_prepare_candidate_inputs.py`
- 使用进攻型参数：`knot_first` + `momentum_cta`
- 自动检查Knot四维研究结果
- 支持降级到`score_first`策略

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
- `/projects/vnpy/state/runs/candidate_inputs.prepare.report.us.json`
- `/projects/vnpy/state/runs/candidate_inputs.dynamic.us.json`
- `/projects/vnpy/log/candidate_preparation.log`

### 3. run_ted_discovery.py
TED进攻型标池发现主脚本。

**功能特点：**
- 集成Knot四维研究和候选准备
- 基于AEOS评分系统筛选标的
- 生成核心进攻池、观察池、备用池
- 输出详细发现报告

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
- `/projects/vnpy/state/runs/ted_aggressive_pool_report.json`
- `/projects/vnpy/log/ted_discovery.log`

### 4. run_ted_full_pipeline.py
完整流水线脚本，自动执行三个阶段。

**功能特点：**
- 按顺序执行三个阶段
- 错误处理和状态检查
- 进度显示和耗时统计
- 统一的日志管理

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
- 写本地产物到`state/runs/`目录

### 数据依赖
- TED发现需要先运行Knot四维研究和候选准备
- 候选准备会自动检测最新的Knot结果作为种子
- 如果没有Knot结果，会降级到`score_first`策略

### 性能优化
- 完整流水线约需5-15分钟
- 可单独运行某个阶段进行调试
- 结果文件会自动覆盖，建议定期备份重要结果

## 故障排除

### 常见问题
1. **Knot脚本不存在**：检查`/projects/vnpy/scripts/quant_workflow/`目录
2. **候选准备失败**：检查Futu连接状态和参数配置
3. **TED发现异常**：检查候选数据文件是否存在

### 调试模式
```bash
# 启用详细日志
cd /projects/vnpy/phase2
python3 -v run_ted_discovery.py
```

## 版本历史

- **v1.0** (2026-05-24): 初始版本，支持方案C三阶段流水线
- **功能**：Knot四维研究、候选准备、TED发现、完整流水线

## 联系方式

如有问题，请检查相关日志文件或联系项目维护者。