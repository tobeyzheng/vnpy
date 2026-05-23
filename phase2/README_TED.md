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
python3 phase2/test_ted_strategy.py
```

### 2. 运行完整发现流程
```bash
cd /projects/vnpy
python3 phase2/run_ted_discovery.py
```

### 3. 查看结果
- **报告文件**：`/projects/vnpy/state/runs/ted_aggressive_pool_report.json`
- **日志文件**：`/projects/vnpy/log/ted_discovery.log`

## 📊 策略架构

### 双通道召回机制

#### 1. Knot四维研究召回
- **技术面**：突破形态、趋势强度
- **基本面**：业绩增长、估值合理
- **资金流**：机构参与、资金流入
- **事件驱动**：催化事件、新闻强化

#### 2. 市场数据召回
- 使用 `momentum_cta` 预设
- 结合市场快照和动态评分
- 确保技术面确认

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

## 📈 发现信号重点

### 价格结构信号
- 收盘价 > 20日线 > 60日线 > 120日线
- 距离52周高点不超过8%-12%
- 近20日涨幅12%-45%
- 突破后3-10天不大幅回撤

### 成交与资金信号
- 绝对成交额大 (ADV高)
- 近5-10日成交额明显高于20日均值
- 放量上涨、缩量回踩

### 事件催化信号
- 财报beat + raise
- 管理层上调全年指引
- 行业价格上行
- 大客户订单/AI基础设施扩张

### 相对强弱信号
- 跑赢大盘 (SPY/QQQ)
- 跑赢同板块ETF
- 跑赢同产业链同类公司

## ⚠️ 风险控制

### 衰竭风险识别
- 单日涨幅超过50%
- 换手率超过20%
- 距离10日线过远
- 过去20日涨幅已70%+但无新催化

### 结构性风险识别
- 市值小于10亿
- 流动性差
- 纯概念无业绩支撑
- 小票/妖股

## 🔧 配置说明

### 主要配置文件
- `phase2/ted_config.yaml` - 策略参数配置
- `phase2/trend_explosion_discovery.py` - 核心策略逻辑
- `phase2/run_ted_discovery.py` - 执行入口脚本

### 关键参数调整

#### AEOS权重调整
```yaml
aeos_scoring:
  weights:
    breakout_trend: 0.30      # 提高趋势权重
    relative_strength: 0.20   # 相对强弱权重
    # ... 其他权重
```

#### 过滤条件调整
```yaml
candidate_filters:
  market_cap_min: 5000000000    # 调整最小市值
  change_pct_min: 2.0           # 调整最小涨幅
```

## 📋 使用流程

### 日常执行流程
1. **数据准备**：确保有最新的市场数据
2. **运行发现**：执行 `python3 phase2/run_ted_discovery.py`
3. **结果分析**：查看生成的标池报告
4. **策略调整**：根据需要调整配置参数

### 最佳实践
- **执行时间**：建议在开盘前(09:00)或收盘后(16:00)执行
- **执行频率**：每日执行一次
- **结果验证**：结合历史表现验证策略效果

## 📊 输出示例

### 报告结构
```json
{
  "schema_version": "ted_v1",
  "generated_at": "2026-05-24T07:44:15",
  "strategy": "trend_explosion_discovery",
  "pool_summary": {
    "total_candidates": 12,
    "core_pool_count": 4,
    "watch_pool_count": 5,
    "reserve_pool_count": 3
  },
  "core_pool": [
    {
      "symbol": "US.NVDA",
      "name": "NVIDIA Corporation",
      "aeos_score": 0.82,
      "price": 950.0,
      "change_pct": 12.5,
      "market_cap": 2500000000000,
      "turnover": 50000000000
    }
  ]
}
```

## 🔍 策略验证

### 历史验证样本
- **成功案例**：MU、WDC、NVDA、PLTR、SMCI
- **失败案例**：过度追高、情绪尾声标的

### 验证标准
- 能否在主升浪早期进入候选Top 20
- 能否进入进攻池Top 8-12
- 是否是"早中期发现"而非高位追入
- 假强势票能否被有效过滤

## 🛠️ 故障排除

### 常见问题

#### 1. Knot连接失败
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

## 📞 技术支持

如有问题，请检查：
1. 日志文件中的错误信息
2. 配置文件参数是否正确
3. 数据文件是否存在且格式正确

## 🔄 版本历史

- **v1.0** (2026-05-24): 初始版本发布
  - 双通道召回机制
  - AEOS评分系统
  - 三层次标池分类
  - 完整的风险控制

---

**注意**：本策略为研究工具，不构成投资建议。实际交易前请进行充分测试和风险评估。