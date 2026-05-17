# Optimization Report — strategy_simple_multifactor on NVDA.SMART

- IS window: `2024-01-01` → `2025-11-12`
- OOS window: `2025-11-13` → `2026-05-13`
- Capital: `10000.0`
- Min trades filter: `5`
- Output dir: `tmp/data/optimize_results/strategy_simple_multifactor_NVDA_SMART_20260513_211905`
- Total elapsed: `4224.8s`

## Cross-interval summary

| interval | IS sharpe | IS trades | IS ret% | IS dd% | OOS sharpe | OOS trades | OOS ret% | OOS dd% |
|---|---|---|---|---|---|---|---|---|
| 10m | 2.357 | 375 | 315.65 | -20.06 | 2.248 | 67 | 32.02 | -7.44 |
| 30m | 1.968 | 125 | 227.43 | -11.77 | 0.020 | 73 | 0.44 | -32.09 |
| 1h | 1.870 | 170 | 178.06 | -15.99 | 0.600 | 17 | 15.96 | -25.48 |
| 4h | 1.510 | 28 | 160.00 | -17.26 | 1.409 | 6 | 18.66 | -12.84 |
| 1d | 1.652 | 14 | 246.78 | -23.76 | 1.107 | 4 | 4.45 | -3.27 |

## Best params per interval (from IS)

### 10m

```json
{
  "fast_window": 3,
  "slow_window": 13,
  "rsi_window": 9,
  "rsi_oversold": 25.0,
  "rsi_overbought": 65.0,
  "volume_ratio_threshold": 1.5,
  "stop_loss_pct": 0.05,
  "take_profit_pct": 0.05,
  "position_pct": 0.3,
  "LIVE_SUBMIT": true
}
```

### 30m

```json
{
  "fast_window": 8,
  "slow_window": 30,
  "rsi_window": 14,
  "rsi_oversold": 25.0,
  "rsi_overbought": 65.0,
  "volume_ratio_threshold": 1.2,
  "stop_loss_pct": 0.03,
  "take_profit_pct": 0.08,
  "position_pct": 0.3,
  "LIVE_SUBMIT": true
}
```

### 1h

```json
{
  "fast_window": 10,
  "slow_window": 20,
  "rsi_window": 14,
  "rsi_oversold": 25.0,
  "rsi_overbought": 65.0,
  "volume_ratio_threshold": 1.3,
  "stop_loss_pct": 0.05,
  "take_profit_pct": 0.1,
  "position_pct": 0.3,
  "LIVE_SUBMIT": true
}
```

### 4h

```json
{
  "fast_window": 8,
  "slow_window": 50,
  "rsi_window": 14,
  "rsi_oversold": 25.0,
  "rsi_overbought": 65.0,
  "volume_ratio_threshold": 1.6,
  "stop_loss_pct": 0.06,
  "take_profit_pct": 0.12,
  "position_pct": 0.3,
  "LIVE_SUBMIT": true
}
```

### 1d

```json
{
  "fast_window": 10,
  "slow_window": 50,
  "rsi_window": 14,
  "rsi_oversold": 25.0,
  "rsi_overbought": 65.0,
  "volume_ratio_threshold": 1.5,
  "stop_loss_pct": 0.05,
  "take_profit_pct": 0.15,
  "position_pct": 0.3,
  "LIVE_SUBMIT": true
}
```
