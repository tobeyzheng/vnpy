"""
Futu gateway for VeighNa.
"""

try:
    from importlib.metadata import PackageNotFoundError, version
except ImportError:  # pragma: no cover
    from importlib_metadata import PackageNotFoundError, version

from .futu_gateway import FutuGateway

try:
    __version__ = version("vnpy_futu")
except PackageNotFoundError:
    __version__ = "dev"

__all__ = ["FutuGateway", "__version__"]
