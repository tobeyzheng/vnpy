# 06 — 多标策略真实回测 Runbook（双轨 + 对账）

> 适用于 plan `us_multi_symbol_quant_phase2_v2`。本 runbook 仅描述 **真实回测**
> 流程；阶段 ② v2 仍然不允许 REAL 实盘，SIM 升级请新开 phase3 plan。

---

## 1. 双轨概览

| 轨道 | 引擎 | 是否真实撮合 | 是否联网 | 触发方式 |
|------|------|------|------|------|
| 本地影子轨 | `services/backtest`（vnpy DB + 本地 K 线） | ✅ | ❌ | 命令行 |
| Futu 主轨 | Futu 量化平台 | ✅ | 平台侧自连 | **手工**：上传策略 + 平台界面点击 |
| 阶段 ② dry-run（参考） | `phase2/runners/run_phase2_backtest.py` | ❌ | ❌ | `--dry-run` |

> 阶段 ② v2 仓库分支不允许从脚本自动连 Futu OpenD 触发 Futu 平台回测；
> Futu 主轨产物只能由用户在 Futu 客户端手工导出后录入。

---

## 2. 本地影子轨 — 真实回测命令

### 2.1 前置检查

```bash
# 1) 确认本地数据落盘（≥ 5 年日 K，覆盖池里全部 12 只）
ls tmp/data/ | head

# 2) 确认 phase2 测试全部绿（必须 50 passed）
python3 -m pytest phase2/strategy/tests/ -q
```

### 2.2 真实回测命令（**会改写 `state/runs/`，需用户确认**）

```bash
python3 -m services.backtest.cli \
  --symbols US.AAPL,US.MSFT,US.NVDA,US.GOOGL,US.META,US.AMZN,US.TSLA,\
US.AVGO,US.AMD,US.JPM,US.XOM,US.UNH \
  --start 2020-01-01 --end 2025-12-31 \
  --initial-cash 1000000 \
  --max-single-position-pct 0.16 \
  --max-total-positions 5 \
  --output state/runs/us_multi_symbol_quant_phase2_v2/real_local_001/local_expected.json
```

> 命令前请向 AI 显式回复 `确认执行`，否则 AI 不应自动跑此命令（项目规则 2）。

### 2.3 验证产物

```bash
test -f state/runs/us_multi_symbol_quant_phase2_v2/real_local_001/local_expected.json && \
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(list(d.keys())[:10])" \
  state/runs/us_multi_symbol_quant_phase2_v2/real_local_001/local_expected.json
```

至少应包含：`final_equity` / `total_return` / `max_drawdown` / `sharpe` / `win_rate` 五项。

---

## 3. Futu 主轨 — 手工流程

### 3.1 准备策略文件

```bash
# 1) 验证 futumd 单文件策略
python3 -m py_compile phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py
python3 phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py --check

# 2) 复制到平台投递目录（与 NVDA 策略并列；不修改 tmp/strategy 现有文件）
cp phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py tmp/strategy/
```

### 3.2 在 Futu 量化平台手工操作

1. 登录 Futu 客户端 → 量化策略页
2. **新建策略** → 选择"自定义"
3. **上传** `tmp/strategy/us_multi_symbol_phase2_strategy_futumd.py`
4. **运行标的** → 勾选 12 只（与 `pool_config.yaml` 完全一致）
5. **回测设置**：
    - 区间：`2020-01-01 ~ today`
    - 初始资金：`1,000,000 USD`
    - 频率：日 K
    - 复权方式：前复权
6. **执行回测** → 等待平台返回（视区间长度 1~10 分钟）
7. **导出** → 平台 CSV / JSON → 手工保存到：

```
state/runs/us_multi_symbol_quant_phase2_v2/real_local_001/futu_actual.json
```

### 3.3 `futu_actual.json` 最小 schema

```json
{
  "final_equity": 1234567.89,
  "total_return": 0.2347,
  "max_drawdown": 0.082,
  "sharpe": 1.21,
  "win_rate": 0.563
}
```

---

## 4. 双轨对账 — 复用阶段 ② reconcile runner

```bash
python3 phase2/runners/run_phase2_reconcile.py \
  --actual   state/runs/us_multi_symbol_quant_phase2_v2/real_local_001/futu_actual.json \
  --expected state/runs/us_multi_symbol_quant_phase2_v2/real_local_001/local_expected.json \
  --run-id   real_001
```

退出码：

| exit | 含义 | 后续 |
|------|------|------|
| 0 | PASS（5 项指标差异均 ≤ 20%） | 继续 SIM 准入清单评估 |
| 5 | FAIL（任一指标差异 > 20%） | 看 `reconcile_report.json` 排查；不允许提交 SIM |

---

## 5. 池更新脚本配套使用

```bash
# 周度 dry-run（不写盘）
python3 phase2/runners/run_pool_update.py --dry-run

# 用户确认后落盘
python3 phase2/runners/run_pool_update.py --apply --confirm
```

退出码：

| exit | 触发条件 |
|------|---------|
| 0 | OK |
| 2 | universe schema 校验失败 / 新池 > 硬上限 20 |
| 5 | (add+remove)/size > 30%（变更过激） |
| 6 | 任一 sector 占比 > 40% |
| 7 | `--apply` 没有 `--confirm` |

---

## 6. 不做清单（阶段 ② v2 边界）

- 不允许从脚本自动连 Futu OpenD 触发 Futu 平台回测；
- 不允许翻 `LIVE_SUBMIT = True`；
- 不允许 REAL 提交（即使 SIM 也需要新开 phase3 plan）；
- 不允许池更新脚本联网拉行情；行情来源仅 `tmp/data/` 与 `pool_metrics_snapshot.json`。

---

## 7. 阅读路径与关联

- 阶段 ② dry-run 入口：[`docs/system_integration_guide.md`](../../system_integration_guide.md)（"阶段 ② v2"小节）
- 池更新源代码：[`phase2/runners/run_pool_update.py`](../../../phase2/runners/run_pool_update.py)
- 迁移版策略：[`phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py`](../../../phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py)
- 对账 runner：[`phase2/runners/run_phase2_reconcile.py`](../../../phase2/runners/run_phase2_reconcile.py)
- SIM 准入：[`docs/research/us_multi_symbol_quant/05_sim_gate_checklist.md`](./05_sim_gate_checklist.md)
