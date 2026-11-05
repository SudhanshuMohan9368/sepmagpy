"""Python driver for SEPMAG bead-separation instruments."""

from .device import DeviceIOError, DeviceNotFound, SepmagDevice, SepmagError
from .protocol import Frame, Reading, parse_frame

__all__ = [
    "SepmagDevice",
    "SepmagError",
    "DeviceNotFound",
    "DeviceIOError",
    "Frame",
    "Reading",
    "parse_frame",
]
__version__ = "0.1.0"
