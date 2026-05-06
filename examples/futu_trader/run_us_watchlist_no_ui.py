from __future__ import annotations

import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from market_watchlist_trader import main


if __name__ == "__main__":
    if "--market" not in sys.argv:
        sys.argv.extend(["--market", "US"])
    if "--watchlist" not in sys.argv:
        sys.argv.extend(["--watchlist", "examples/futu_trader/config/quant_watchlist.json"])
    if "--top-n" not in sys.argv:
        sys.argv.extend(["--top-n", "10"])
    main()
