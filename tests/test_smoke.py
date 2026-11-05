import numpy as np
import pytest

from sepmag.fit import fit_sigmoid, sigmoid
from sepmag.protocol import (
    CMD_READ_DATA,
    FrameError,
    build_command,
    parse_frame,
)


def make_reply(year=15, serial=3, chans=((1, 300), (2, 1023), (3, 450), (4, 1023), (5, 1023), (6, 1023))):
    b = bytearray([0x37, year, serial] + [0] * 61)
    for k, (ch, v) in enumerate(chans):
        o = 3 + 3 * k
        b[o], b[o + 1], b[o + 2] = ch, v & 0xFF, v >> 8
    return bytes(b)


def test_build_command():
    c = build_command(CMD_READ_DATA)
    assert len(c) == 65 and c[0] == 0 and c[1] == 0x37 and set(c[2:]) == {0xFF}


def test_parse_six_channel():
    f = parse_frame(make_reply())
    assert f.board_id == "153" and f.year == 15
    assert [r.value for r in f.readings] == [300, 1023, 450, 1023, 1023, 1023]
    assert [r.channel for r in f.readings if r.active] == [1, 3]
    assert f.values["1531"] == 300


def test_parse_accepts_report_id_prefix():
    assert parse_frame(b"\x00" + make_reply()).board_id == "153"


def test_short_frame():
    with pytest.raises(FrameError):
        parse_frame(b"\x00" * 10)


def test_fit_recovers_parameters():
    t = np.linspace(0.5, 120, 400)
    rng = np.random.default_rng(0)
    v = sigmoid(t, 100, 900, 30, 2.5) + rng.normal(0, 3, t.size)
    f = fit_sigmoid(t, v)
    assert f.t50 == pytest.approx(30, rel=0.05)
    assert f.p == pytest.approx(2.5, rel=0.1)
    assert f.r2 > 0.99


@pytest.mark.db
def test_db_reachable():
    from sepmag.db import SepmagDB

    with SepmagDB() as db:
        assert "t_Value" in db.reflect()


@pytest.mark.hardware
def test_hardware_frame():
    from sepmag.device import SepmagDevice

    with SepmagDevice() as dev:
        f = dev.read_frame()
    assert f.board_id
