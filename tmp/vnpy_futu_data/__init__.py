"""Futu OpenD historical bar data manager for local backtesting.

Pulls K-line data from the Futu OpenD gateway and stores it in vnpy's
SQLite database for native BacktestingEngine consumption, with cache
metadata to avoid duplicate downloads and support incremental updates.
"""

from .symbol_mapping import futu_to_vnpy, vnpy_to_futu, futu_to_vnpy_interval, vnpy_to_futu_interval
from .futu_data_manager import FutuDataManager

__all__ = [
    "FutuDataManager",
    "futu_to_vnpy",
    "vnpy_to_futu",
    "futu_to_vnpy_interval",
    "vnpy_to_futu_interval",
]
