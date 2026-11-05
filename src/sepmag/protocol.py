"""Pure (I/O-free) SEPMAG HID frame encoding/decoding. See PROTOCOL.md."""

from __future__ import annotations

from dataclasses import dataclass, field

VENDOR_ID = 0x04D8
PRODUCT_IDS: tuple[int, ...] = (0x0054, 0xFD4D)

REPORT_LEN = 64
CMD_READ_DATA = 0x37  # vendor QCRDEVICEDATA (55)
CMD_DEVICE_ID = 0x38  # vendor QCRDEVICEID (56) - never sent by the vendor app
CMD_LED_TOGGLE = 0x80  # dead code in vendor app - UNVERIFIED
FILL = 0xFF

SLOTS = 6
SLOT_BASE = 3  # hidapi payload offset of slot 0's channel-id byte
ACTIVE_THRESHOLD = 1022  # vendor MINVALUE: reading < this => sensor populated
NEW_BOARD_MIN_YEAR = 15


class FrameError(ValueError):
    """Raised when a device reply cannot be decoded."""


def build_command(cmd: int) -> bytes:
    """65-byte Windows HID output buffer: report-id 0x00, cmd, 0xFF padding."""
    if not 0 <= cmd <= 0xFF:
        raise ValueError(f"command out of range: {cmd}")
    return bytes([0x00, cmd]) + bytes([FILL]) * (REPORT_LEN - 1)


@dataclass(frozen=True)
class Reading:
    channel: int
    key: str  # board_id + str(channel), as the vendor app keys sensors
    value: int  # raw 16-bit counts (10-bit ADC in practice)
    active: bool


@dataclass(frozen=True)
class Frame:
    raw: bytes
    echo: int  # payload byte 0; presumed echo of the command (UNVERIFIED)
    board_id: str
    year: int
    readings: tuple[Reading, ...] = field(default_factory=tuple)

    @property
    def values(self) -> dict[str, int]:
        return {r.key: r.value for r in self.readings}


def _u16(lo: int, hi: int) -> int:
    return (hi << 8) | lo


def parse_frame(data: bytes) -> Frame:
    """Decode a reply to CMD_READ_DATA.

    ``data`` is the hidapi payload (report-id stripped). A 65-byte buffer whose
    first byte is the report id 0x00 is also accepted.
    """
    if len(data) == REPORT_LEN + 1 and data[0] == 0:
        data = data[1:]
    if len(data) < REPORT_LEN:
        raise FrameError(f"short frame: {len(data)} bytes")
    data = bytes(data[:REPORT_LEN])

    board_id = f"{data[1]}{data[2]}"
    year = int(board_id[:2])  # vendor getSerialYear(): first two *characters*

    readings: list[Reading] = []
    if year >= NEW_BOARD_MIN_YEAR:
        for k in range(SLOTS):
            o = SLOT_BASE + 3 * k
            ch, val = data[o], _u16(data[o + 1], data[o + 2])
            readings.append(Reading(ch, f"{board_id}{ch}", val, val < ACTIVE_THRESHOLD))
    else:  # legacy single-sensor board (layout UNVERIFIED)
        val = _u16(data[1], data[2])
        readings.append(Reading(1, f"{board_id}1", val, True))

    return Frame(data, data[0], board_id, year, tuple(readings))
