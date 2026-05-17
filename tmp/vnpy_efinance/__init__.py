"""
Efinance datafeed for VeighNa.
"""

try:
    from importlib.metadata import PackageNotFoundError, version
except ImportError:  # pragma: no cover
    from importlib_metadata import PackageNotFoundError, version

from .efinance_datafeed import EfinanceDatafeed

try:
    __version__ = version("vnpy_efinance")
except PackageNotFoundError:
    __version__ = "dev"

__all__ = ["EfinanceDatafeed", "__version__"]