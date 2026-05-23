#!/usr/bin/env bash
set -euo pipefail

export FUTU_HOST=127.0.0.1
export FUTU_PORT=11111
export FUTU_MARKET=US

while true; do
    python3 phase2/runners/run_phase2_live_daily.py \
    --futu-env 模拟 \
    --live-submit \
    --strategy-path /projects/vnpy/phase2/strategy/us_multi_symbol_phase2_strategy_futumd_v3.py \
    --pool-config /projects/vnpy/phase2/strategy/config/pool_config_fixed.yaml \
    --init-cash 100000 \
    --rebalance-time 15:55 \
    --session-tz America/New_York \
    --bar-warmup 260 \
    --max-single-position-pct 0.70 \      # 0.34 → 0.70，匹配策略 1/3×2.0 上限
    --max-daily-new-position-pct 0.99 \   # 0.70 → 0.99，给一天 3 个新仓留出空间
    --max-market-exposure-pct 0.99 \      # 默认 0.95 → 0.99，对齐 pool_budget_pct
    --max-drawdown-pct 0.30 \             # 默认 0.20 → 0.30，覆盖回测真实 MDD 27.57%
    --max-order-value 80000 \             # 默认 20000 → 80000，覆盖单仓 NAV×0.66
    --max-runtime-seconds 86400 \
        || echo "[run_sim_us] runner exited with $?, restarting in 60s"
    sleep 60   # 防 busy-loop；下一轮立即重新进入"等待 15:55"
done