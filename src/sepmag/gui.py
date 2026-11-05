"""SEPMAG logger GUI: detect slots, timed/long runs, live plot, continuous CSV.

Run:  python -m sepmag.gui      (or: sepmag gui)
Close the vendor Monitor app first.
"""

from __future__ import annotations

import csv
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .device import SepmagDevice, SepmagError

RUNS_DIR = Path.home() / "Documents" / "sepmag-py" / "runs"


class Logger(threading.Thread):
    """Owns the device while running. Pushes ('sample', t, {key: v}), ('error', msg),
    ('done', reason) onto ``out``. Writes every row to CSV immediately."""

    def __init__(
        self,
        keys: list[str],
        interval_s: float,
        duration_s: float | None,
        csv_path: Path,
        out: queue.Queue[tuple[Any, ...]],
    ) -> None:
        super().__init__(daemon=True)
        self.keys, self.interval_s, self.duration_s = keys, interval_s, duration_s
        self.csv_path, self.out = csv_path, out
        self.stop_event = threading.Event()

    def run(self) -> None:
        try:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            with SepmagDevice() as dev, open(self.csv_path, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["timestamp", "elapsed_s", *self.keys])
                t0 = time.perf_counter()
                n = 0
                while not self.stop_event.is_set():
                    t = time.perf_counter() - t0
                    if self.duration_s is not None and t > self.duration_s:
                        break
                    try:
                        sample = dev.read_sample()
                        row = [sample.get(k, "") for k in self.keys]
                        w.writerow([datetime.now().isoformat(timespec="milliseconds"), f"{t:.3f}", *row])
                        fh.flush()
                        self.out.put(("sample", t, {k: sample.get(k) for k in self.keys}))
                    except SepmagError as e:  # one bad poll must not end a long run
                        self.out.put(("error", str(e)))
                    n += 1
                    wait = t0 + n * self.interval_s - time.perf_counter()
                    if self.stop_event.wait(max(wait, 0.0)):
                        break
            self.out.put(("done", "stopped" if self.stop_event.is_set() else "finished"))
        except Exception as e:  # noqa: BLE001 - report to GUI
            self.out.put(("done", f"failed: {e}"))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SEPMAG Logger")
        self.resize(1000, 650)
        self.q: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.worker: Logger | None = None
        self.checks: dict[str, QCheckBox] = {}
        self.t: list[float] = []
        self.y: dict[str, list[float]] = {}
        self.board = ""

        root = QWidget()
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        side = QVBoxLayout()
        lay.addLayout(side, 0)

        # slots
        g = QGroupBox("Slots")
        self.slot_box = QVBoxLayout(g)
        self.btn_detect = QPushButton("Detect slots")
        self.btn_detect.clicked.connect(self.detect)
        self.slot_box.addWidget(self.btn_detect)
        self.slot_list = QVBoxLayout()
        self.slot_box.addLayout(self.slot_list)
        side.addWidget(g)

        # timing
        g = QGroupBox("Timing")
        tl = QVBoxLayout(g)
        row = QHBoxLayout()
        row.addWidget(QLabel("Read every"))
        self.iv_val = QDoubleSpinBox()
        self.iv_val.setRange(0.1, 9999)
        self.iv_val.setValue(1.0)
        self.iv_unit = QComboBox()
        self.iv_unit.addItems(["seconds", "minutes"])
        row.addWidget(self.iv_val)
        row.addWidget(self.iv_unit)
        tl.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Run for"))
        self.d_h, self.d_m, self.d_s = QSpinBox(), QSpinBox(), QSpinBox()
        for sp, mx, sfx in ((self.d_h, 999, " h"), (self.d_m, 59, " m"), (self.d_s, 59, " s")):
            sp.setRange(0, mx)
            sp.setSuffix(sfx)
            row.addWidget(sp)
        self.d_m.setValue(1)
        tl.addLayout(row)
        tl.addWidget(QLabel("(0 h 0 m 0 s = run until Stop)"))
        side.addWidget(g)

        # output
        g = QGroupBox("CSV output")
        ol = QVBoxLayout(g)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("auto: runs/run_<time>.csv")
        b = QPushButton("Browse...")
        b.clicked.connect(self.browse)
        ol.addWidget(self.path_edit)
        ol.addWidget(b)
        side.addWidget(g)

        g = QGroupBox("Latest values (stay after stop)")
        self.val_box = QVBoxLayout(g)
        self.val_labels: dict[str, QLabel] = {}
        side.addWidget(g)

        self.btn_start = QPushButton("Start")
        self.btn_start.clicked.connect(self.start)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.stop)
        self.btn_stop.setEnabled(False)
        self.btn_png = QPushButton("Save plot PNG")
        self.btn_png.clicked.connect(self.save_png)
        for w in (self.btn_start, self.btn_stop, self.btn_png):
            side.addWidget(w)
        self.status = QLabel("Idle")
        self.status.setWordWrap(True)
        side.addWidget(self.status)
        side.addStretch(1)

        # plot
        self.fig = Figure(tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        plot_col = QVBoxLayout()
        plot_col.addWidget(NavigationToolbar2QT(self.canvas, self))
        plot_col.addWidget(self.canvas, 1)
        lay.addLayout(plot_col, 1)
        self._last_draw = 0.0
        self._style_axes("s")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.pump)
        self.timer.start(200)
        self._dirty = False
        QTimer.singleShot(200, self.detect)

    # ---- helpers ----
    def _style_axes(self, unit: str) -> None:
        self.ax.set_xlabel(f"Time ({unit})")
        self.ax.set_ylabel("Raw counts")
        self.ax.grid(True, alpha=0.3)

    def set_status(self, msg: str) -> None:
        self.status.setText(msg)

    def selected_keys(self) -> list[str]:
        return [k for k, c in self.checks.items() if c.isChecked()]

    def browse(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "CSV file", str(RUNS_DIR), "CSV (*.csv)")
        if p:
            self.path_edit.setText(p)

    def save_png(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Save plot", str(RUNS_DIR), "PNG (*.png)")
        if p:
            self.fig.savefig(p, dpi=150)

    # ---- actions ----
    def detect(self) -> None:
        if self.worker is not None:
            return
        try:
            with SepmagDevice() as dev:
                frame = dev.read_frame()
        except SepmagError as e:
            self.set_status(f"Detect failed: {e}")
            return
        while self.slot_list.count():
            w = self.slot_list.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.checks.clear()
        self.board = frame.board_id
        for r in frame.readings:
            label = f"Slot {r.channel}  (now {r.value})" if r.active else f"Slot {r.channel}  - empty/no sensor"
            cb = QCheckBox(label)
            cb.setEnabled(r.active)
            cb.setChecked(r.active)
            self.slot_list.addWidget(cb)
            if r.active:
                self.checks[r.key] = cb
        n = len(self.checks)
        self.set_status(f"Board {frame.board_id}: {n} sensor slot(s) found")

    def start(self) -> None:
        keys = self.selected_keys()
        if not keys:
            self.set_status("Select at least one slot")
            return
        interval = self.iv_val.value() * (60.0 if self.iv_unit.currentText() == "minutes" else 1.0)
        dur = self.d_h.value() * 3600 + self.d_m.value() * 60 + self.d_s.value()
        path = Path(self.path_edit.text()) if self.path_edit.text() else (
            RUNS_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.csv"
        )
        self.t.clear()
        self.y = {k: [] for k in keys}
        self.ax.clear()
        self.lines = {k: self.ax.plot([], [], label=self._slot_name(k), lw=1.2)[0] for k in keys}
        self.ax.legend(loc="upper right")
        while self.val_box.count():
            w = self.val_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.val_labels = {}
        for k in keys:
            lb = QLabel(f"{self._slot_name(k)}: --")
            lb.setStyleSheet("font-size: 16px; font-weight: bold;")
            self.val_box.addWidget(lb)
            self.val_labels[k] = lb
        self._unit_div, self._unit = 1.0, "s"
        self._style_axes("s")
        self.canvas.draw_idle()
        self.worker = Logger(keys, interval, dur or None, path, self.q)
        self.worker.start()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_detect.setEnabled(False)
        self.set_status(f"Running -> {path}")

    def _slot_name(self, key: str) -> str:
        return f"Slot {key[len(self.board):]}" if key.startswith(self.board) else key

    def stop(self) -> None:
        if self.worker:
            self.worker.stop_event.set()

    def pump(self) -> None:
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            if msg[0] == "sample":
                _, t, vals = msg
                self.t.append(t)
                for k, v in vals.items():
                    self.y[k].append(float("nan") if v is None else v)
                self._dirty = True
                for k, v in vals.items():
                    self.val_labels[k].setText(f"{self._slot_name(k)}: {v}")
                self.set_status(f"Running  t={t:.0f}s  samples={len(self.t)}")
            elif msg[0] == "error":
                self.set_status(f"Read error (continuing): {msg[1]}")
            elif msg[0] == "done":
                self.worker = None
                self.btn_start.setEnabled(True)
                self.btn_stop.setEnabled(False)
                self.btn_detect.setEnabled(True)
                self.set_status(f"Run {msg[1]}. {len(self.t)} samples.")
                self._dirty = True
        now = time.monotonic()
        if self._dirty and self.t and now - self._last_draw >= max(0.2, len(self.t) / 50000):
            self.redraw()
            self._dirty = False
            self._last_draw = now

    def redraw(self) -> None:
        span = self.t[-1]
        div, unit = (1.0, "s") if span < 600 else (60.0, "min") if span < 36000 else (3600.0, "h")
        xs = [x / div for x in self.t]  # every sample is plotted
        for k, ln in self.lines.items():
            ln.set_data(xs, self.y[k])
        self.ax.set_xlabel(f"Time ({unit})")
        self.ax.relim()
        self.ax.autoscale_view()
        self.canvas.draw_idle()

    def closeEvent(self, ev: Any) -> None:  # noqa: N802
        if self.worker:
            self.worker.stop_event.set()
            self.worker.join(timeout=3)
        ev.accept()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
