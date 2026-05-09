# Futu 异动检测技能脚本

本目录包含用于检测股票异动的技能脚本，通过 Futu OpenD API 提供专业的异动分析能力。

## 可用技能

### 1. 资金流异动检测 (Capital Anomaly)
- **脚本**: `handle_capital_anomaly.py`
- **功能**: 检测资金分布、买卖经纪商、资金流向、卖空数量和比例等异常变化
- **使用场景**: 主力行为分析、大单小单分歧、资金流向异常

### 2. 衍生品异动检测 (Derivatives Anomaly)
- **脚本**: `handle_derivatives_anomaly.py`
- **功能**: 检测牛熊证街货比例、期权大单、隐含波动率等衍生品异常
- **使用场景**: 期权异常成交、聪明钱动向、波动率溢价分析

### 3. 技术面异动检测 (Technical Anomaly)
- **脚本**: `handle_technical_anomaly.py`
- **功能**: 检测K线形态和技术指标事件（MACD、RSI、KDJ、BOLL等）
- **使用场景**: 技术信号识别、形态突破、超买超卖分析

## 使用方法

### 基本调用格式
```bash
python3 scripts/skills/handle_capital_anomaly.py <股票代码> --time-range <天数> --json
```

### 示例
```bash
# 检测特斯拉最近7天资金异动
python3 scripts/skills/handle_capital_anomaly.py US.TSLA --time-range 7 --json

# 检测腾讯技术面异动，指定分析维度
python3 scripts/skills/handle_technical_anomaly.py HK.00700 --time-range 5 --analysis-dimensions macd rsi --json

# 检测英伟达衍生品异动
python3 scripts/skills/handle_derivatives_anomaly.py US.NVDA --time-range 3 --json
```

### 参数说明
- `股票代码`: 标准市场前缀符号（US.TSLA、HK.00700、SH.600519等）
- `--time-range`: 分析时间窗口（自然天数，默认7天）
- `--analysis-dimensions`: 指定分析维度（可选）
- `--json`: 输出JSON格式结果

## 注意事项

1. 需要先启动 Futu OpenD 服务
2. 确保网络连接正常
3. 股票代码必须使用标准格式
4. 支持中英文输出（通过 --language-id 参数）