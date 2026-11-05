"""SEPMAG device driver over hidapi."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from types import TracebackType
from typing import Any

import hid

from .protocol import (
    CMD_READ_DATA,
    PRODUCT_IDS,
    REPORT_LEN,
    VENDOR_ID,
    Frame,
    build_command,
    parse_frame,
)

log = logging.getLogger(__name__)


class SepmagError(Exception):
    """Base driver error."""


class DeviceNotFound(SepmagError):
    pass


class DeviceIOError(SepmagError):
    pass


def enumerate_devices() -> list[dict[str, Any]]:
    """All attached SEPMAG HID interfaces (the mass-storage interface is not HID)."""
    return [
        d
        for pid in PRODUCT_IDS
        for d in hid.enumerate(VENDOR_ID, pid)
    ]


class SepmagDevice:
    """Poll/response driver. The board has no start/stop command: every
    ``read_frame()`` sends 0x37 and reads one reply (see PROTOCOL.md)."""

    def __init__(
        self,
        *,
        timeout_ms: int = 1000,
        reconnect_attempts: int = 3,
        reconnect_delay_s: float = 0.5,
        path: bytes | None = None,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_delay_s = reconnect_delay_s
        self._path = path
        self._dev: Any | None = None
        self.last_frame: Frame | None = None

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        if self._dev is not None:
            return
        infos = enumerate_devices()
        if self._path is not None:
            infos = [i for i in infos if i["path"] == self._path]
        if not infos:
            raise DeviceNotFound(
                f"no SEPMAG device (VID {VENDOR_ID:#06x}, PID in "
                f"{[hex(p) for p in PRODUCT_IDS]}) found"
            )
        info = infos[0]
        dev = hid.device()
        try:
            dev.open_path(info["path"])
        except OSError as e:
            raise DeviceIOError(
                f"cannot open {info['path']!r} (is the vendor Monitor app running?): {e}"
            ) from e
        self._dev, self._path = dev, info["path"]
        log.info("opened SEPMAG %04x:%04x", info["vendor_id"], info["product_id"])

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            finally:
                self._dev = None

    def __enter__(self) -> SepmagDevice:
        self.connect()
        return self

    def __exit__(
        self,
        et: type[BaseException] | None,
        ev: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def reconnect(self) -> None:
        self.close()
        last: Exception | None = None
        for _ in range(self.reconnect_attempts):
            try:
                self.connect()
                return
            except SepmagError as e:
                last = e
                time.sleep(self.reconnect_delay_s)
        raise DeviceIOError(f"reconnect failed: {last}")

    # -- I/O ---------------------------------------------------------------
    def _transact(self, cmd: int) -> bytes:
        if self._dev is None:
            raise DeviceIOError("not connected")
        try:
            n = self._dev.write(list(build_command(cmd)))
            if n < 0:
                raise OSError("write failed")
            data = bytes(self._dev.read(REPORT_LEN, self.timeout_ms))
        except OSError as e:
            raise DeviceIOError(str(e)) from e
        if not data:
            raise DeviceIOError(f"timeout after {self.timeout_ms} ms")
        return data

    def read_frame(self) -> Frame:
        """One poll: send 0x37, parse reply. Reconnects once on I/O failure."""
        try:
            data = self._transact(CMD_READ_DATA)
        except DeviceIOError as e:
            log.warning("I/O error (%s); reconnecting", e)
            self.reconnect()
            data = self._transact(CMD_READ_DATA)
        self.last_frame = parse_frame(data)
        return self.last_frame

    def get_sensor_id(self) -> str:
        """Board id (decimal concat of header bytes), as the vendor 'placaId'."""
        return self.read_frame().board_id

    def read_sample(self) -> dict[str, int]:
        """Raw counts keyed by sensor key, active sensors only."""
        f = self.read_frame()
        return {r.key: r.value for r in f.readings if r.active}

    def stream(
        self, interval_s: float = 0.1, duration_s: float | None = None
    ) -> Iterator[tuple[float, dict[str, int]]]:
        """Yield ``(elapsed_s, sample)`` at a fixed period (host-side timing,
        like the vendor's samplingRate). Stops after ``duration_s`` if given."""
        t0 = time.perf_counter()
        n = 0
        while True:
            now = time.perf_counter() - t0
            if duration_s is not None and now > duration_s:
                return
            yield now, self.read_sample()
            n += 1
            delay = t0 + n * interval_s - time.perf_counter()
            if delay > 0:
                time.sleep(delay)

    def send_command(self, cmd: int) -> bytes:
        """Raw escape hatch for protocol exploration (UNVERIFIED commands)."""
        return self._transact(cmd)
