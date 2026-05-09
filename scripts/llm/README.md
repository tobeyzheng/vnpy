# LLM 模块

基于大语言模型的股票分析与操作建议生成工具，集成KnotAgent提供智能投资建议。

## 功能特性

- 📊 **多维度分析**: 整合价格、技术指标、资金流、衍生品数据
- 🤖 **智能建议**: 调用KnotAgent生成专业的操作建议
- ⚡ **实时异动检测**: 集成技术面、资金面、衍生品异动检测
- 🎯 **灵活配置**: 支持多种时间框架和分析维度
- 📈 **市场上下文**: 结合大盘形势进行综合分析

## 安装依赖

```bash
pip install -r requirements.txt
```

## 快速开始

### 基本用法

```bash
# 分析单个股票
python analyze_stock_with_llm.py US.TSLA

# 指定时间框架
python analyze_stock_with_llm.py HK.00700 --timeframe 1h

# 包含技术分析
python analyze_stock_with_llm.py US.AAPL --include-technical

# 详细输出模式
python analyze_stock_with_llm.py US.NVDA --verbose
```

### 异动检测

```bash
# 检测技术面异动
python anomaly_detector.py US.TSLA --technical

# 检测所有类型异动
python anomaly_detector.py HK.00700 --all

# 输出为提示词格式
python anomaly_detector.py US.AAPL --all --format
```

## 参数说明

### analyze_stock_with_llm.py

| 参数 | 缩写 | 说明 | 默认值 |
|------|------|------|--------|
| `symbol` | - | 股票代码 (US.TSLA, HK.00700) | 必填 |
| `--timeframe` | `-t` | 时间框架 (1m,5m,15m,30m,1h,4h,1d,1w) | 1d |
| `--include-technical` | - | 包含技术分析 | false |
| `--include-capital` | - | 包含资金流分析 | false |
| `--include-derivatives` | - | 包含衍生品分析 | false |
| `--verbose` | `-v` | 详细输出模式 | false |

### anomaly_detector.py

| 参数 | 缩写 | 说明 |
|------|------|------|
| `symbol` | - | 股票代码 |
| `--technical` | `-t` | 检测技术面异动 |
| `--capital` | `-c` | 检测资金面异动 |
| `--derivatives` | `-d` | 检测衍生品异动 |
| `--all` | `-a` | 检测所有类型异动 |
| `--format` | `-f` | 输出为提示词格式 |

## 集成示例

### 在交易策略中使用

```python
from scripts.llm.analyze_stock_with_llm import get_market_context, generate_analysis_prompt
from scripts.llm.anomaly_detector import detect_anomalies, format_anomalies_for_prompt

# 获取市场上下文
context = get_market_context("US.TSLA", "1d")

# 检测异动
anomalies = detect_anomalies("US.TSLA", ['technical', 'capital'])

# 生成分析提示词
prompt = generate_analysis_prompt(context, anomalies)
```

### 定时分析任务

可以配置cron job定期运行分析：

```bash
# 每天开盘前分析重点股票
0 8 * * 1-5 python /path/to/analyze_stock_with_llm.py US.TSLA
0 8 * * 1-5 python /path/to/analyze_stock_with_llm.py US.AAPL

# 盘中监控异动
*/30 * * * 1-5 python /path/to/anomaly_detector.py US.NVDA --all
```

## 配置说明

编辑 `config.py` 文件可以调整LLM模块的配置：

```python
# KnotAgent配置
knot_agent_enabled = True  # 是否启用KnotAgent
knot_agent_timeout = 30    # 超时时间(秒)
knot_agent_max_tokens = 2000  # 最大token数

# 分析配置
default_timeframe = "1d"   # 默认时间框架
include_technical = True   # 默认包含技术分析
include_capital = False    # 默认不包含资金流分析
```

## 注意事项

1. **数据依赖**: 需要配置正确的行情数据源
2. **KnotAgent配置**: 确保KnotAgent服务正常运行
3. **权限设置**: 异动检测需要相应的API权限
4. **网络连接**: 需要稳定的网络连接获取实时数据

## 故障排除

### 常见问题

1. **导入错误**: 检查Python路径和依赖安装
2. **KnotAgent连接失败**: 检查KnotAgent服务状态
3. **数据获取失败**: 验证行情数据源配置
4. **异动检测失败**: 检查相关技能模块配置

### 日志查看

```bash
# 查看详细日志
export PYTHONPATH=/projects/vnpy:$PYTHONPATH
python analyze_stock_with_llm.py US.TSLA --verbose
```

## 开发指南

### 添加新的分析维度

1. 在 `config.py` 中添加配置项
2. 在 `analyze_stock_with_llm.py` 中扩展 `get_market_context` 函数
3. 更新提示词模板以包含新的分析维度

### 自定义提示词模板

编辑 `config.py` 中的 `AnalysisPromptTemplates` 类来自定义分析提示词。

## 许可证

MIT License