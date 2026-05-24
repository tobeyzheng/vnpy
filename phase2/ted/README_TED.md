# Trend Explosion Discovery (TED) 进攻型标池发现策略

## 📋 策略概述

**Trend Explosion Discovery (TED)** 是一个专门用于发现类似闪迪、美光等强趋势暴涨标的的进攻型标池发现策略。该策略不改动现有交易逻辑，专注于上游的标池发现与筛选。

### 🎯 核心目标
- **早期识别**：在主升浪刚开始的1-3周内识别强趋势标的
- **进攻导向**：专注于高攻击性、高成长潜力的标的
- **风险控制**：避免追高和情绪尾声标的
- **行业分散**：覆盖AI基础设施、半导体、数据中心等高增长主题

## 🚀 快速开始

### 1. 测试策略功能
```bash
cd /projects/vnpy
python3 phase2/ted/test_ted_strategy.py
```

### 2. 运行完整发现流程
```bash
cd /projects/vnpy
python3 phase2/ted/run_ted_discovery.py
```

### 3. 轻量验证 Knot
```bash
cd /projects/vnpy
python3 phase2/ted/run_knot_4dim_research.py --validate-only
```

### 4. 查看结果
- **TED归档目录**：`/projects/vnpy/state/runs/ted/YYYYMMDDTHHMMSS/`
- **最终报告**：`/projects/vnpy/state/runs/ted/YYYYMMDDTHHMMSS/stage3_ted_discovery/ted_aggressive_pool_report.json`
- **pool_config导出**：`/projects/vnpy/state/runs/ted/YYYYMMDDTHHMMSS/stage3_ted_discovery/pool_config.yaml`
- **兼容镜像**：`/projects/vnpy/state/runs/ted_aggressive_pool_report.json`、`/projects/vnpy/state/runs/ted_pool_config.yaml`
- **日志文件**：`/projects/vnpy/log/ted_discovery.log`

## 📊 策略架构

### 双通道召回机制

#### 1. Knot四维研究召回
- **技术面**：突破形态、趋势强度
- **基本面**：业绩增长、估值合理
- **资金流**：机构参与、资金流入
- **事件驱动**：催化事件、新闻强化
- **归档位置**：`stage1_knot_4dim/knot_4dim_us.json`

#### 2. 市场数据召回
- 使用 `momentum_cta` 预设
- 结合市场快照和动态评分
- 确保技术面确认
- **归档位置**：`stage2_candidate_preparation/candidate_inputs.dynamic.us.json`

### AEOS评分系统 (Aggressive Explosion Opportunity Score)

#### 评分权重
- **30%** 突破趋势 (Breakout/Trend)
- **20%** 相对强弱 (Relative Strength)
- **15%** 资金参与 (Flow/Participation)
- **15%** 事件新鲜度 (Event Freshness)
- **10%** 主题领导力 (Theme Leadership)
- **10%** 流动性质量 (Liquidity Quality)

#### 风险调整
- **-10%** 衰竭风险 (Exhaustion Risk)
- **-10%** 结构性风险 (Structural Risk)

## 🎯 标池分类

### 🔥 核心进攻池 (4-6只)
- **标准**：AEOS评分 ≥ 0.70
- **特点**：趋势强劲、资金参与度高、主题明确
- **示例**：NVDA、SMCI、MU等

### 👀 观察补位池 (4-6只)
- **标准**：AEOS评分 0.60-0.70
- **特点**：潜力标的、需要进一步观察确认
- **示例**：AAPL、MSFT、AMD等

### 💼 主题备用池 (2-4只)
- **标准**：AEOS评分 0.50-0.60
- **特点**：主题相关、可作为备选或对冲
- **示例**：GOOGL、AMZN、META等

## 🔧 配置说明

### 主要配置文件
- `phase2/ted/ted_config.yaml` - 策略参数配置
- `phase2/ted/trend_explosion_discovery.py` - 核心策略逻辑
- `phase2/ted/run_ted_discovery.py` - 执行入口脚本
- `phase2/ted/run_knot_4dim_research.py` - Knot研究入口脚本
- `state/runs/ted/YYYYMMDDTHHMMSS/stage3_ted_discovery/pool_config.yaml` - 导出的 phase2 兼容标的池配置

## 📋 使用流程

### 日常执行流程
1. **轻量验证**：先执行 `python3 phase2/ted/run_knot_4dim_research.py --validate-only`
2. **数据准备**：确保有最新的市场数据
3. **运行发现**：执行 `python3 phase2/ted/run_ted_discovery.py`
4. **结果分析**：查看 `TED` 日期目录下三阶段结果和最终标池报告
5. **策略调整**：根据需要调整配置参数

### 最佳实践
- **执行时间**：建议在开盘前(09:00)或收盘后(16:00)执行
- **执行频率**：每日执行一次
- **结果验证**：结合历史表现验证策略效果
- **后续复用**：下游如需直接消费标的池，可读取同批次 `pool_config.yaml`

## 🛠️ 故障排除

### 常见问题

#### 1. Knot连接失败
- 先执行 `python3 phase2/ted/run_knot_4dim_research.py --validate-only`
- 检查网络连接
- 验证Knot服务状态
- 查看日志文件获取详细错误信息

#### 2. 数据加载失败
- 检查数据文件路径
- 验证文件权限
- 确认数据格式正确

#### 3. 评分异常
- 检查配置参数
- 验证输入数据完整性
- 查看详细日志信息

### 日志分析
查看 `/projects/vnpy/log/ted_discovery.log` 获取详细执行信息。

## 🔄 版本历史

- **v1.2** (2026-05-24): 新增 `TED` 专属日期结果目录、分阶段归档和 `pool_config.yaml` 导出
- **v1.1** (2026-05-24): 重构到 `phase2/ted/` 独立目录，新增 Knot 轻量验证入口
- **v1.0** (2026-05-24): 初始版本发布

---

**注意**：本策略为研究工具，不构成投资建议。实际交易前请进行充分测试和风险评估。