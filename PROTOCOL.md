# SEPMAG HID protocol (from MONITOR_WPFv4.3.0.exe)

Source: ILSpy decompile of `MONITOR_WPFv4.3.0.exe`. Everything below is read
directly from `MONITOR_WPF.usbdeviceMultiProcesses` unless marked **UNVERIFIED**
(needs a hardware capture). Method names in the original brief
(`startDataHarvester`, `resetFormDataHarvester`, `readValue`, `GetDevices`,
`getSelectedDevice`) **do not exist in v4.3.0**; the real call chain is
`OnlineProcessGui.doSomething()` -> `MainWindow.checkDeviceState(55)` ->
`usbdeviceMultiProcesses.ReadWriteFromDevice()` (background thread).

## 1. Discovery

* HID interface GUID `{4D1E55B2-F16F-11CF-88CB-001111000030}` (standard HID class).
* Enumerated via SetupAPI; hardware-ID string must contain (case-insensitive)
  `vid_04d8&pid_0054` **or** `vid_04d8&pid_fd4d`.
* Opened twice with `CreateFile(path, ...)`: one `GENERIC_WRITE` handle, one
  `GENERIC_READ` handle, share mode READ|WRITE, `OPEN_EXISTING`, no overlapped I/O.
* Hot-plug: `WM_DEVICECHANGE` (arrival/removal/config) re-runs discovery.
* The mass-storage interface (`MI_00`) is irrelevant; the HID one is `MI_01`.

## 2. Transport

* Plain `WriteFile` / `ReadFile` on the HID handle, **65-byte buffers**
  (byte 0 = report ID `0x00`, bytes 1..64 = payload). Synchronous, blocking.
* hidapi equivalent: `write([0x00] + 64 bytes)`; `read(64)` returns payload only
  (report-ID byte stripped). **Index mapping: vendor `buf[i]` == hidapi `data[i-1]`.**
  All offsets below are given in hidapi (0-based payload) form, with the vendor
  index in parentheses.

## 3. Commands (host -> device)

Frame: `[0x00, CMD, 0xFF * 63]` (65 bytes; unused payload filled with `0xFF`).

| CMD | Name in source | Sent by app? | Meaning |
|-----|----------------|--------------|---------|
| `0x37` (55) | `QCRDEVICEDATA` | **Yes**, continuously | Poll: device replies with one report holding all sensor readings |
| `0x38` (56) | `QCRDEVICEID`   | **No** (constant exists, never assigned) | Presumably a separate ID query - **UNVERIFIED**, not needed since ID is in the 0x37 reply |
| `0x80` (128) | (LED toggle) | **No** - gated by `ToggleLEDsPending`, which is never set true (dead code) | LED toggle - **UNVERIFIED** |
| `0x81` (129) | -               | **No** - buffer prepared, never written | unknown |

**There is no start / stop / arm command.** The instrument is a pure
poll-response sensor board: the host sends `0x37`, reads one report, repeats.
"Start measurement" in the vendor app is purely host-side (a timer that polls
and stores `(t, value)` into SQL). Timing: poll interval comes from the step's
`samplingRate` (ms; default 100) via `TIMEINCREMENT`; the app sleeps the
remainder of each period. The device is also read in a tight loop inside
`ReadWriteFromDevice` (no sleep while attached), so the cached value is at most
one round-trip old.

## 4. Response to `0x37` (device -> host), 64-byte payload

Offsets are hidapi `data[n]` (vendor index in parentheses).

| Offset | Vendor idx | Meaning |
|--------|-----------|---------|
| 0 | 1 | Not read by the app. Echoes CMD `0x37` - **verified on hardware** (board 2511, PID FD4D, 2026-11-05) |
| 1 | 2 | Board "year" byte |
| 2 | 3 | Board serial byte |
| 3+3k | 4+3k | Sensor/channel ID for slot k (k = 0..5) |
| 4+3k | 5+3k | Reading, low byte |
| 5+3k | 6+3k | Reading, high byte |

i.e. six slots at offsets 3,6,9,12,15,18 (vendor 4,7,10,13,16,19).
Reading = `(hi << 8) | lo`, little-endian unsigned 16-bit (10-bit ADC in practice, 0..1023).

### Sensor Id ("placaId")

`board_id = str(data[1]) + str(data[2])` (decimal strings concatenated, e.g.
`15` and `3` -> `"153"`). Each sensor key is `board_id + str(channel_id)`.
The first two *characters* of `board_id` are parsed as the **serial year**
(`getSerialYear`): `int(board_id[:2])`. (Quirk: because it is a string slice,
`data[1]=9, data[2]=12` gives `"912"` -> 91. We reproduce this faithfully.)

### Layout switch by year

* **year >= 15** (6-channel board): six slots as above; sensor keys
  `board_id + str(channel_id)`.
* **year < 15** (legacy single-channel board): one sensor with key
  `board_id + "1"`, reading `= (data[2] << 8) | data[1]` (vendor
  `(buf[3] << 8) + buf[2]`). Note these are the same bytes used for the board
  ID, so legacy boards presumably reply in a different layout - **UNVERIFIED**
  (no legacy hardware available).

### Active sensors

The set of populated slots is decided **once**, on the first reply after
attach: a slot is "active" if `reading < 1022`. Unpopulated slots read ~1023
(ADC saturated / pulled high). `MINVALUE = 1022`.

## 5. Reading -> opacity

None. The raw 16-bit count is used unchanged as the Y value: plotted as-is,
stored as `yValue` in `t_Value`, and used as `basalSensorValue` when the device
is not attached. X is elapsed seconds since process start.

## 6. Error handling in the app

* `WriteFile` failure -> mark detached, re-run discovery, reopen handles.
* All exceptions in the loop are swallowed (empty `catch`).
* `ReadFile` failure -> reply ignored (previous cached values stay).

## 7. Open questions (need a hardware capture to close)

1. Does byte 0 of the reply echo `0x37`? (Verify on first read; the driver logs it.)
2. What do `0x38`, `0x80`, `0x81` do? The vendor never sends them; the driver
   only exposes `0x37` and a guarded raw `send_command` for experiments.
3. Legacy (<15) frame layout.
4. Whether the board needs a minimum poll interval (vendor polls back-to-back).

## 8. Database mapping (for `sepmag.db`)

Vendor SQL (`ddbbMeasureManager.addAProcessData`):

* `t_Value(ProcessId, StepId, xValue, yValue)` - `xValue` = seconds, `yValue` = raw counts.
* `t_Steps(StepId, ProcessId, TemplateStepId, t99, t50, P, tf, isTemplate, isEnd, triggerLevel, samplingRate, V0, Vmax, Regression, dataExcluded, runningDataNumber, graphColor)`
  - the vendor's own fitted `t50`, `P`, `V0`, `Vmax`, `Regression` (r) are
  stored per step, which gives a ground truth to validate our fit.
* `t_Process(ProcessId, UserRef, ProcessStart, Lot, TemplateId, isProcessEnd)`.

Note: no table/column called `aValue`/`processLot` appears in the v4.3.0 SQL;
the module reflects the live schema at runtime and falls back to the names above.
