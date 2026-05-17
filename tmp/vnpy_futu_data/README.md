# Futu OpenD 行情数据管理（vnpy 本地回测专用）

通过本地 OpenD 拉取历史 K 线，落入 vnpy 的 SQLite 行情库 `~/.vntrader/database.db`，
供 `BacktestingEngine` 原生消费。

## 特性

- **零适配回测**：写入 vnpy 原生 SQLite，`BacktestingEngine.load_data()` 直接命中。
- **缓存复用**：`tmp/data/cache_index.json` 记录每只标的已覆盖区间，重复请求 0 次 OpenD 调用。
- **增量拉取**：扩展区间时仅拉缺口段，避免重复下载。
- **DB 唯一索引兜底**：即便缓存索引误判，SQLite 唯一索引也会去重。
- **限流友好**：分页 1000 根、间隔 0.5s、失败指数退避 3 次。
- **多市场**：港股 (`HK.xxxxx`) / 美股 (`US.AAPL`) / 沪 (`SH.6xxxxx`) / 深 (`SZ.0xxxxx`)。
- **多周期**：`1d` / `1m` / `5m` / `15m` / `30m` / `60m`。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FUTU_OPEND_HOST` | `127.0.0.1` | OpenD 主机 |
| `FUTU_OPEND_PORT` | `11111` | OpenD 端口 |
| `FUTU_OPEND_PASSWORD` | 空 | unlock 密码（一般 quote 不需要） |

## CLI 用法

```bash
# 拉取日线（默认，自动复用已有数据）
python tmp/run_futu_data_pull.py \
    --symbols HK.00700,US.AAPL,SH.600519 \
    --start 2024-01-01 --end 2024-12-31

# 拉取 5 分钟级
python tmp/run_futu_data_pull.py \
    --symbols HK.00700 --interval 5m \
    --start 2024-12-01 --end 2024-12-31

# 查看缓存状态
python tmp/run_futu_data_pull.py --status

# 强制重拉（忽略缓存）
python tmp/run_futu_data_pull.py --symbols HK.00700 \
    --start 2024-01-01 --end 2024-12-31 --force
```

## 在脚本中调用

```python
from tmp.vnpy_futu_data import FutuDataManager

mgr = FutuDataManager()
results = mgr.pull(
    futu_codes=["HK.00700", "US.AAPL"],
    start="2024-01-01",
    end="2024-12-31",
    interval_alias="1d",
)
mgr.close()
```

## 与 vnpy 回测衔接

数据进库后，`BacktestingEngine` 直接读取，**回测脚本无需改动**：

```python
from vnpy_ctastrategy.backtesting import BacktestingEngine
from vnpy.trader.constant import Exchange, Interval

engine = BacktestingEngine()
engine.set_parameters(
    vt_symbol="00700.SEHK",          # 与 (symbol="00700", exchange=SEHK) 一致
    interval=Interval.DAILY,
    start=datetime(2024, 1, 1),
    end=datetime(2024, 12, 31),
    rate=0, slippage=0, size=1, pricetick=0.01, capital=100_000,
)
engine.load_data()  # 命中本地 SQLite
```

## 局限与未来工作

- vnpy 原生 `Interval` 只区分 `1m/1h/1d/1w`，因此 `5m/15m/30m` 当前折叠到 `MINUTE` 桶。
  日线策略不受影响。如果未来需要严格区分，可在 `symbol` 加后缀（`00700_5m`）。
- 当前仅现货标的；期权 / 牛熊证 / 期货回测后续再加。
- `autype` 默认前复权（`qfq`）；要切换可传 `--autype hfq` 或 `none`。

## 文件清单

- `tmp/vnpy_futu_data/__init__.py`：包入口
- `tmp/vnpy_futu_data/symbol_mapping.py`：futu ↔ vnpy 符号 / 周期映射
- `tmp/vnpy_futu_data/futu_data_manager.py`：核心 `FutuDataManager`
- `tmp/run_futu_data_pull.py`：CLI 入口
- `tmp/data/cache_index.json`：缓存元信息（纳入 git 跟踪）
