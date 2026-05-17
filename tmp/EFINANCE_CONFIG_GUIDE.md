# Efinance 数据服务配置指南

## 📋 简介

Efinance是由个人打造的用于获取股票、基金、期货数据的免费开源Python库，支持A股、港股、美股等多市场数据。

## 🔧 安装配置

### 1. 安装Efinance包

```bash
pip install efinance
```

### 2. 配置vn.py使用Efinance数据服务

#### 方法一：修改vt_setting.json文件

编辑 `/projects/vnpy/vt_setting.json` 文件：

```json
{
  "datafeed.name": "efinance",
  "datafeed.username": "efinance_user",
  "datafeed.password": "efinance_token",
  "font.family": "微软雅黑",
  "font.size": 12,
  "log.active": true,
  "log.level": "INFO",
  "log.console": true,
  "log.file": true,
  "database.timezone": "Asia/Shanghai",
  "database.name": "sqlite",
  "database.database": "database.db"
}
```

**注意：Efinance不需要Token，但vn.py框架要求填写username和password字段，可以任意填写。**

#### 方法二：临时使用（推荐）

直接在脚本中导入Efinance数据服务：

```python
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from vnpy_efinance.efinance_datafeed import EfinanceDatafeed

# 创建数据服务实例
datafeed = EfinanceDatafeed()
datafeed.init()
```

## 📊 Efinance数据服务特点

### 支持的数据类型
- **A股数据**: 股票行情、财务数据、指数数据
- **港股数据**: 港股行情、港股通数据  
- **美股数据**: 美股行情
- **基金数据**: 基金净值、基金持仓
- **期货数据**: 国内期货行情

### 数据频率支持
- 日线数据 (DAILY)
- 周线数据 (WEEKLY)
- 月线数据 (MONTHLY)
- 分钟数据 (MINUTE)
- 小时数据 (HOUR)

### 数据质量
- 完全免费，无需Token
- 数据来源多样，更新及时
- 支持多市场数据获取
- 接口简单易用

## 🚀 快速开始

### 基本使用示例

```python
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import HistoryRequest
from vnpy_efinance.efinance_datafeed import EfinanceDatafeed

# 创建数据服务实例
datafeed = EfinanceDatafeed()
datafeed.init()

# 获取A股历史数据
req = HistoryRequest(
    symbol="000001",
    exchange=Exchange.SSE,
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 10),
    interval=Interval.DAILY
)

bars = datafeed.query_bar_history(req)
print(f"获取到 {len(bars)} 条数据")
```

### 多市场数据获取示例

```python
# A股数据
req_a = HistoryRequest(
    symbol="000001",
    exchange=Exchange.SSE,
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 10),
    interval=Interval.DAILY
)

# 港股数据
req_hk = HistoryRequest(
    symbol="00700",
    exchange=Exchange.HKEX,
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 10),
    interval=Interval.DAILY
)

# 美股数据
req_us = HistoryRequest(
    symbol="AAPL",
    exchange=Exchange.NASDAQ,
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 10),
    interval=Interval.DAILY
)
```

## 📈 与其他数据源对比

| 特性 | Efinance | Tushare | Baostock | OpenBB |
|------|----------|---------|----------|---------|
| **费用** | ✅ 完全免费 | ⚠️ 免费+付费 | ✅ 完全免费 | ✅ 免费 |
| **A股数据** | ✅ 全面 | ✅ 全面 | ✅ 全面 | ✅ 全面 |
| **多市场** | ✅ A+H+美股 | ✅ A+H+美股 | ❌ 仅A股 | ✅ 全球 |
| **数据质量** | ⚠️ 一般 | ✅ 较好 | ✅ 官方 | ⚠️ 依赖源 |
| **接口简洁** | ✅ 优秀 | ✅ 良好 | ✅ 良好 | ❌ 复杂 |
| **稳定性** | ⚠️ 一般 | ✅ 较好 | ✅ 稳定 | ⚠️ 一般 |

## 🔄 集成到vn.py框架

### 1. 临时集成（推荐）

在策略脚本中直接导入：

```python
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from vnpy_efinance.efinance_datafeed import EfinanceDatafeed

class MyStrategy(CtaTemplate):
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        
        # 使用Efinance数据服务
        self.datafeed = EfinanceDatafeed()
        self.datafeed.init()
```

### 2. 永久集成

将vnpy_efinance目录移动到vn.py的模块目录：

```bash
mv /projects/vnpy/tmp/vnpy_efinance /projects/vnpy/vnpy_efinance
```

然后修改vt_setting.json配置：

```json
{
  "datafeed.name": "efinance",
  "datafeed.username": "efinance_user",
  "datafeed.password": "efinance_token"
}
```

## ⚠️ 注意事项

1. **数据质量**: Efinance是个人项目，数据质量可能不如官方数据源稳定
2. **限流风险**: 遇到限流时需要等待或使用备用数据源
3. **权限限制**: 部分数据（如分钟数据）可能需要高级权限
4. **稳定性**: 建议在生产环境搭配备用数据源使用

## ✅ 验证配置

运行测试脚本验证配置：

```bash
cd /projects/vnpy
python3 tmp/test_efinance_datafeed.py
```

如果看到"✅ Efinance数据服务初始化成功"，说明配置成功！

## 📞 技术支持

### Efinance官方支持
- GitHub: https://github.com/Micro-sheep/efinance
- 文档: https://efinance.readthedocs.io
- PyPI: https://pypi.org/project/efinance/

### vn.py社区支持
- GitHub: https://github.com/vnpy/vnpy
- 文档: https://www.vnpy.com
- 论坛: https://www.vnpy.com/forum