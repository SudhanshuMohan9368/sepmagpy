# sepmag

Python driver + data reader for SEPMAG bead-separation instruments
(protocol recovered from Monitor v4.3.0 - see [PROTOCOL.md](PROTOCOL.md)).

```
pip install -e .[dev]
sepmag info                      # enumerate, open, print board id + one frame
sepmag monitor --interval 0.1    # live polling (Ctrl-C to stop)
sepmag read-latest --fit         # latest step from SQL Server, sigmoid fit
pytest -m "not hardware and not db"
pytest -m db                     # needs localhost\SQLEXPRESS
pytest -m hardware               # needs device attached, vendor app CLOSED
```

```python
from sepmag import SepmagDevice
with SepmagDevice() as dev:
    print(dev.get_sensor_id(), dev.read_sample())
```

**There is no start/stop command**: the board answers each `0x37` poll with the
current readings; "measurement" is host-side timing (`SepmagDevice.stream`).
Close the vendor app first - two hosts polling one HID endpoint will interleave.
