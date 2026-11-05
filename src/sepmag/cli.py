"""`sepmag` command line."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence


def _cmd_info(_: argparse.Namespace) -> int:
    from .device import SepmagDevice, enumerate_devices

    devs = enumerate_devices()
    if not devs:
        print("no SEPMAG device found")
        return 1
    for d in devs:
        print(f"{d['vendor_id']:04x}:{d['product_id']:04x} {d['path']!r}")
    with SepmagDevice() as dev:
        f = dev.read_frame()
    print(f"board id : {f.board_id} (year {f.year})")
    print(f"echo byte: {f.echo:#04x}  (presumed command echo, unverified)")
    for r in f.readings:
        print(f"  ch{r.channel}: {r.value:5d}  {'active' if r.active else '-'}")
    return 0


def _cmd_monitor(a: argparse.Namespace) -> int:
    from .device import SepmagDevice

    with SepmagDevice() as dev:
        try:
            for t, sample in dev.stream(a.interval, a.duration):
                print(f"{t:8.2f}s  " + "  ".join(f"{k}={v}" for k, v in sample.items()))
        except KeyboardInterrupt:
            pass
    return 0


def _cmd_read_latest(a: argparse.Namespace) -> int:
    from .db import SepmagDB
    from .fit import fit_sigmoid

    with SepmagDB() as db:
        step = a.step or db.latest_step_id()
        if step is None:
            print("no samples in database")
            return 1
        df = db.step_series(step)
    print(f"step {step}: {len(df)} samples, t={df.t.min():.1f}..{df.t.max():.1f}s")
    if a.csv:
        df.to_csv(a.csv, index=False)
        print(f"wrote {a.csv}")
    else:
        print(df.head(10).to_string(index=False))
    if a.fit:
        f = fit_sigmoid(df.t, df.value)
        print(f"fit: Vmin={f.vmin:.1f} Vmax={f.vmax:.1f} t50={f.t50:.2f}s p={f.p:.2f} r2={f.r2:.4f}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sepmag")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="enumerate, open, read board id + one frame").set_defaults(fn=_cmd_info)
    m = sub.add_parser("monitor", help="live poll")
    m.add_argument("--interval", type=float, default=0.1)
    m.add_argument("--duration", type=float, default=None)
    m.set_defaults(fn=_cmd_monitor)
    sub.add_parser("gui", help="logger GUI").set_defaults(fn=lambda _: __import__("sepmag.gui", fromlist=["main"]).main())
    r = sub.add_parser("read-latest", help="latest step series from SQL Server")
    r.add_argument("--step", type=int)
    r.add_argument("--csv")
    r.add_argument("--fit", action="store_true")
    r.set_defaults(fn=_cmd_read_latest)
    a = ap.parse_args(argv)
    try:
        return int(a.fn(a))
    except Exception as e:  # noqa: BLE001 - CLI boundary
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
