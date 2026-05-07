from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.trading_pipeline import MarketCloseTaskConfig, SimCloseTradingPipeline


CONFIG = MarketCloseTaskConfig(
    market="us",
    task_name="us_real_env_sim_trading_v1",
    account_filename="us_sim_account.json",
    report_filename="us_sim_close_report.json",
    lot_size_default=1,
    quote_prefix="US",
    symbol_suffix="US",
)


def main() -> None:
    pipeline = SimCloseTradingPipeline(REPO_ROOT, CONFIG)
    report = pipeline.run()
    print(pipeline.report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
