# # 增量拉取（仅拉缺口）
# python3 run_futu_data_pull.py \
#     --symbols US.NVDA \
#     --start 2022-01-01 --end 2026-05-17

# # 查看缓存状态
# python3 run_futu_data_pull.py --status

# 2. 运行回测
    # --strategy tmp/strategy/us_strategy_simple_multifactor2.py \

python3 run_local_backtest.py \
    --strategy tmp/strategy/us_nvda_1d_strategy_trend_momentum.py \
    --symbol NVDA.SMART \
    --interval 1d \
    --start 2022-01-01 \
    --end 2024-12-31 \
    --capital 100000 \
    --setting '{
      "fast_window": 5,
      "slow_window": 20,
      "rsi_window": 9,
      "rsi_oversold": 30.0,
      "rsi_overbought": 85.0,
      "volume_ratio_threshold": 1.2,
      "stop_loss_pct": 0.05,
      "take_profit_pct": 0.15,
      "position_pct": 0.3,
      "LIVE_SUBMIT": true
    }'