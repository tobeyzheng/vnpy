from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FutuAccountSnapshot:
    status: str
    account_count: Optional[int] = None
    message: str = "sdk_not_connected"
