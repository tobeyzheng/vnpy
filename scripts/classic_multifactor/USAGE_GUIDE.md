# Classic MultiFactor 入口脚本使用指南

## 概述

Classic MultiFactor 系统提供两种主要的交易模式：
- **分钟级交易** (`run_intraday_loop.py`) - 日内高频交易
- **天级别交易** (`run_daily_rebalance.py`) - 每日调仓交易

## 分钟级交易 (日内高频)

### 基本用法
```bash
# 美股分钟级交易示例
python3 scripts/classic_multifactor/run_intraday_loop.py \
    --config configs/classic_multifactor/intraday_example.json \
    --session-start 09:30 \
    --session-end 16:00 \
    --session-tz America/New_York
```

### 美股专用配置
```bash
# 美股NVDA分钟级交易
python3 scripts/classic_multifactor/run_intraday_loop.py \
    --config configs/classic_multifactor/us_nvda_intraday.json \
    --session-start 09:30 \
    --session-end 16:00 \
    --session-tz America/New_York \
    --futu-market US \
    --futu-env 模拟
```

### 关键参数说明
- `--session-start`: 交易开始时间 (HH:MM)
- `--session-end`: 交易结束时间 (HH:MM)
- `--session-tz`: 时区 (美股使用 `America/New_York`)
- `--futu-market`: 市场代码 (美股为 `US`)
- `--futu-env`: 交易环境 (`模拟` 或 `真实`)

## 天级别交易 (每日调仓)

### 基本用法
```bash
# 美股天级别调仓示例
python3 scripts/classic_multifactor/run_daily_rebalance.py \
    --config configs/classic_multifactor/daily_example.json \
    --rebalance-time 16:05 \
    --session-tz America/New_York
```

### 美股多标的组合调仓
```bash
# 美股多因子组合调仓
python3 scripts/classic_multifactor/run_daily_rebalance.py \
    --config configs/classic_multifactor/us_portfolio_daily.json \
    --rebalance-time 16:05 \
    --session-tz America/New_York \
    --futu-market US \
    --max-bars 1
```

### 关键参数说明
- `--rebalance-time`: 调仓时间 (HH:MM)
- `--max-bars`: 处理的日线数量 (默认1条)
- `--target-positions`: 多标的权重配置

## 美股交易特殊配置

### 1. 美股标的符号格式
```json
{
    "symbol": "NVDA.US",
    "setting": {
        "capital": 100000,
        "max_position_pct": 0.1
    }
}
```

**注意**: 系统会自动将 `NVDA.US` 转换为 Futu OMS 格式 `NVDA.SMART`

### 2. 美股时区配置
- **时区**: `America/New_York`
- **交易时间**: 09:30 - 16:00 (美东时间)
- **调仓时间**: 建议 16:05 (收盘后5分钟)

### 3. Futu网关配置
```bash
# 环境变量配置
export FUTU_HOST=127.0.0.1
export FUTU_PORT=11111
export FUTU_MARKET=US
export FUTU_ENV=模拟
```

## 配置文件结构

### 分钟级交易配置示例
```json
{
    "loop_mode": "intraday",
    "symbol": "AAPL.US",
    "setting": {
        "capital": 50000,
        "max_position_pct": 0.05,
        "entry_cooldown_minutes": 30,
        "min_hold_minutes": 60,
        "no_new_entry_after": "15:30"
    }
}
```

### 天级别交易配置示例
```json
{
    "loop_mode": "daily",
    "symbol": "AAPL.US",
    "rebalance_time": "16:05",
    "target_positions": {
        "AAPL.US": 0.4,
        "MSFT.US": 0.3,
        "GOOGL.US": 0.3
    },
    "setting": {
        "capital": 100000,
        "max_daily_turnover": 0.2,
        "daily_new_pct_limit": 0.1
    }
}
```

## 风险控制参数

### 通用风控参数
```bash
# 单标的最大持仓比例
--max-single-position-pct 0.1

# 每日新增持仓比例限制
--max-daily-new-position-pct 0.05

# 市场总敞口限制
--max-market-exposure-pct 0.3

# 最大回撤限制
--max-drawdown-pct 0.1
```

### 分钟级特有风控
```bash
# 最大日内交易次数
--max-intraday-trades 10

# 入场冷却时间(分钟)
--entry-cooldown-minutes 30

# 最小持仓时间(分钟)
--min-hold-minutes 60
```

## 实盘交易启用

### 安全开关要求
实盘交易需要同时满足以下条件：

```bash
# 1. 命令行参数
--live-submit

# 2. 环境变量 (必须全部设置为YES)
export VNPY_LIVE_CONFIG=YES
export VNPY_LIVE_SUBMIT=YES
export VNPY_LIVE_APPROVED=YES

# 3. Futu真实账户配置
export FUTU_ENV=真实
export FUTU_TRADE_PASSWORD=your_password
```

### 实盘交易示例
```bash
# 美股实盘分钟级交易 (谨慎使用!)
python3 scripts/classic_multifactor/run_intraday_loop.py \
    --config configs/classic_multifactor/us_live_intraday.json \
    --session-start 09:30 \
    --session-end 16:00 \
    --session-tz America/New_York \
    --futu-market US \
    --futu-env 真实 \
    --live-submit \
    --max-single-position-pct 0.05 \
    --max-drawdown-pct 0.08
```

## 监控和日志

### 启用无侵入式监控
```bash
# 启用Web监控界面
python3 scripts/classic_multifactor/run_daily_rebalance.py \
    --config configs/classic_multifactor/daily_example.json \
    --rebalance-time 16:05 \
    --enable-monitor \
    --monitor-host 127.0.0.1 \
    --monitor-port 8000
```

访问监控界面: `file:///projects/vnpy/scripts/classic_multifactor/web_interface.html`

### 日志和报告
- **事件日志**: `state/runs/dry_run/events.jsonl`、`state/runs/futu_sim/events.jsonl`、`state/runs/futu_real/events.jsonl`
- **订单状态**: `state/runs/dry_run/orders/`、`state/runs/futu_sim/orders/`、`state/runs/futu_real/orders/`
- **运行报告**: `state/runs/classic_multifactor_*_report.json`

## 常见问题排查

### 1. 连接问题
```bash
# 检查Futu OpenD是否运行
netstat -an | grep 11111

# 检查环境变量
echo $FUTU_HOST $FUTU_PORT $FUTU_MARKET
```

### 2. 配置验证
```bash
# 验证配置文件格式
python3 -m json.tool configs/classic_multifactor/your_config.json

# 检查loop_mode匹配
# 分钟级: loop_mode必须为"intraday"
# 天级别: loop_mode必须为"daily"
```

### 3. 权限问题
```bash
# 检查文件权限
ls -la state/runs/

# 确保有写入权限
chmod 755 state/runs/
```

## 最佳实践建议

### 1. 美股交易时间管理
- 使用美东时区 (`America/New_York`)
- 考虑夏令时调整
- 避免在重大事件期间交易

### 2. 风险控制
- 始终先在模拟环境测试
- 逐步增加实盘资金规模
- 设置合理的止损和风控参数

### 3. 监控和维护
- 定期检查日志文件
- 监控系统资源使用情况
- 及时更新配置和参数

## 技术支持

如有问题请参考：
- 项目文档: `docs/system_integration_guide.md`
- 监控使用指南: `MONITOR_USAGE.md`
- 代码注释和日志输出

---

**重要提示**: 实盘交易存在风险，请确保充分测试和理解系统功能后再进行实盘操作。