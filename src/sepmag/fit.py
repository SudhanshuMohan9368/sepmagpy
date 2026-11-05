"""Sigmoid fit V(t) = Vmin + (Vmax - Vmin) / (1 + (t / t50)^p)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.optimize import curve_fit


@dataclass(frozen=True)
class SigmoidFit:
    vmin: float
    vmax: float
    t50: float
    p: float
    r2: float

    def __call__(self, t: npt.ArrayLike) -> npt.NDArray[np.float64]:
        return sigmoid(np.asarray(t, dtype=float), self.vmin, self.vmax, self.t50, self.p)


def sigmoid(
    t: npt.NDArray[np.float64], vmin: float, vmax: float, t50: float, p: float
) -> npt.NDArray[np.float64]:
    t = np.maximum(t, 0.0)
    return vmin + (vmax - vmin) / (1.0 + (t / t50) ** p)


def fit_sigmoid(t: npt.ArrayLike, v: npt.ArrayLike) -> SigmoidFit:
    """Least-squares fit; raises ``ValueError`` on degenerate/insufficient data."""
    t_ = np.asarray(t, dtype=float)
    v_ = np.asarray(v, dtype=float)
    if t_.size < 5 or np.ptp(v_) == 0:
        raise ValueError("need >=5 points with non-constant signal")

    n = max(3, t_.size // 10)
    v_start, v_end = float(v_[:n].mean()), float(v_[-n:].mean())
    mid = (v_start + v_end) / 2
    t50_0 = float(t_[np.argmin(np.abs(v_ - mid))]) or float(np.median(t_[t_ > 0]))
    p0 = [v_end, v_start, max(t50_0, 1e-6), 2.0]  # Vmin, Vmax, t50, p

    try:
        popt, _ = curve_fit(
            sigmoid,
            t_,
            v_,
            p0=p0,
            bounds=([-np.inf, -np.inf, 1e-9, 0.1], [np.inf, np.inf, np.inf, 50.0]),
            maxfev=20000,
        )
    except (RuntimeError, ValueError) as e:
        raise ValueError(f"sigmoid fit did not converge: {e}") from e

    resid = v_ - sigmoid(t_, *popt)
    ss_tot = float(((v_ - v_.mean()) ** 2).sum())
    r2 = 1.0 - float((resid**2).sum()) / ss_tot
    return SigmoidFit(float(popt[0]), float(popt[1]), float(popt[2]), float(popt[3]), r2)
