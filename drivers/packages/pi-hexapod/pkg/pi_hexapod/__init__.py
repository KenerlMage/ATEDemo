"""PI 六足平台驱动包（capability = hexapod，tier = shared）。"""

from .driver import DriverUnavailable, PiHexapod

__all__ = ["DriverUnavailable", "PiHexapod"]
__version__ = "1.0.0"
