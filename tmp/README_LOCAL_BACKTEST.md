# vn.py 本地回测解决方案

## 📋 项目概述

本项目提供了一个完整的本地回测解决方案，允许你在不连接Futu的情况下，使用vn.py框架进行策略回测和参数优化。

## 🎯 核心目标

- **本地化回测**: 使用vn.py框架进行回测，无需Futu连接
- **策略适配**: 将Futu风格策略转换为vn.py兼容格式
- **数据管理**: 自动下载和管理回测所需的历史数据
- **参数优化**: 支持策略参数优化和性能评估

## 📁 项目结构

```
tmp/
├── local_backtest_solution.py    # 主回测解决方案
├── strategy_adapter.py           # 策略适配器
├── simple_data_downloader.py     # 简化版数据下载器
├── strategy/                    # 策略文件目录
│   ├── strategy_simple_multifactor.py
│   └── strategy_classic_multifactor.py
└── README_LOCAL_BACKTEST.md     # 本说明文档
```

## 🚀 快速开始

### 1. 环境检查

首先检查vnpy_tushare是否已安装：

```bash
pip list | grep vnpy-tushare
```

### 2. 下载数据

使用数据下载器获取回测所需的历史数据：

```python
from simple_data_downloader import SimpleDataDownloader

# 创建下载器
downloader = SimpleDataDownloader()

# 下载A股数据
results = downloader.download_a_shares()

# 下载港股数据
results = downloader.download_hk_shares()

# 下载美股数据
results = downloader.download_us_shares()
```

### 3. 策略适配

将Futu风格策略转换为vn.py格式：

```python
from strategy_adapter import FutuToVnpyAdapter

# 创建适配器
adapter = FutuToVnpyAdapter()

# 列出可用策略
strategies = adapter.list_available_strategies()

# 转换策略
strategy = adapter.convert_strategy("strategy_simple_multifactor.py")
```

### 4. 运行回测

使用本地回测解决方案进行回测：

```python
from local_backtest_solution import LocalBacktestSolution

# 创建回测解决方案
solution = LocalBacktestSolution()

# 运行回测
result = solution.run_backtest(
    strategy_file="./tmp/strategy/strategy_simple_multifactor.py",
    symbol="000300",
    exchange="SSE",
    capital=1000000
)

# 生成报告
report = solution.create_backtest_report(result)
print(report)
```

### 5. 参数优化

```python
# 参数优化
param_space = {
    "fast_window": [5, 10, 15],
    "slow_window": [20, 30, 40],
    "rsi_window": [10, 14, 21]
}

result = solution.optimize_parameters(
    strategy_file="./tmp/strategy/strategy_simple_multifactor.py",
    symbol="000300",
    param_space=param_space
)
```

## 📊 支持的市场

### A股市场
- 上证指数 (000001.SSE)
- 沪深300 (000300.SSE)
- 中证500 (000905.SSE)
- 深证成指 (399001.SZSE)
- 创业板指 (399006.SZSE)

### 港股市场
- 腾讯控股 (00700.HKEX)
- 中国移动 (00941.HKEX)
- 友邦保险 (01299.HKEX)

### 美股市场
- 苹果 (AAPL.NASDAQ)
- 微软 (MSFT.NASDAQ)
- 谷歌 (GOOGL.NASDAQ)

## 🔧 配置说明

### 数据服务配置

确保 `vt_setting.json` 中配置了正确的数据服务：

```json
{
    "datafeed.name": "tushare",
    "datafeed.username": "your_username",
    "datafeed.password": "your_token"
}
```

### 策略文件要求

策略文件需要满足以下要求：

1. **类名**: 继承自vn.py的AlphaStrategy
2. **方法**: 实现on_init、on_bar等方法
3. **参数**: 通过类属性定义策略参数
4. **信号**: 通过buy/sell方法生成交易信号

## 📈 回测指标

回测结果包含以下关键指标：

- **总收益率**: 策略总收益百分比
- **年化收益率**: 年化后的收益率
- **最大回撤**: 最大亏损幅度
- **Sharpe比率**: 风险调整后收益
- **胜率**: 盈利交易比例
- **盈亏比**: 平均盈利/平均亏损
- **交易次数**: 总交易次数

## 🔍 常见问题

### Q: 数据下载失败怎么办？
A: 检查tushare token配置是否正确，网络连接是否正常。

### Q: 策略转换失败怎么办？
A: 确保策略文件符合vn.py策略格式要求，检查是否有语法错误。

### Q: 回测结果不理想怎么办？
A: 尝试调整策略参数，使用参数优化功能寻找最优参数组合。

### Q: 如何添加新的策略？
A: 将策略文件放入tmp/strategy目录，确保符合vn.py策略格式。

## 📚 进阶功能

### 自定义数据源

可以扩展数据下载器支持其他数据源：

```python
class CustomDataDownloader(SimpleDataDownloader):
    def download_custom_data(self, source: str):
        # 实现自定义数据下载逻辑
        pass
```

### 多策略组合

支持多个策略的组合回测：

```python
strategies = [
    ("strategy1.py", {"param1": value1}),
    ("strategy2.py", {"param2": value2})
]

results = solution.run_multiple_backtests(strategies)
```

### 实时监控

可以扩展为实时策略监控系统：

```python
class LiveMonitor:
    def monitor_strategy(self, strategy_file: str):
        # 实时监控策略表现
        pass
```

## 🤝 贡献指南

欢迎提交Issue和Pull Request来改进本项目。

## 📄 许可证

本项目基于MIT许可证开源。

## 📞 技术支持

如有问题，请提交Issue或联系项目维护者。

---

**注意**: 本解决方案仅供学习和研究使用，不构成投资建议。投资有风险，决策需谨慎。