from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class OpenDConfig:
    host: str = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port: int = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    trd_env: str = os.getenv("FUTU_TRADE_ENV", "SIMULATE")
