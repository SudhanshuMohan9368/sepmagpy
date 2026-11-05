"""Read-only access to the SEPMAG Monitor SQL Server database."""

from __future__ import annotations

import re
from types import TracebackType
from typing import Any

import pandas as pd
import pyodbc

DEFAULT_CONN = (
    r"DRIVER={ODBC Driver 17 for SQL Server};"
    r"SERVER=localhost\SQLEXPRESS;DATABASE=SepmagMonitorDDBBv2;"
    r"Trusted_Connection=yes;"
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return f"[{name}]"


class SepmagDB:
    """Thin read-only wrapper. Table/column names follow vendor v4.3.0 SQL
    (t_Process, t_Steps, t_Value); override via ``reflect()`` if they differ."""

    def __init__(self, conn_str: str = DEFAULT_CONN, timeout: int = 5) -> None:
        self._conn = pyodbc.connect(conn_str, timeout=timeout, readonly=True)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SepmagDB:
        return self

    def __exit__(
        self,
        et: type[BaseException] | None,
        ev: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
        cur = self._conn.cursor()
        try:
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description]
            return pd.DataFrame.from_records(
                [tuple(r) for r in cur.fetchall()], columns=cols
            )
        finally:
            cur.close()

    def reflect(self) -> dict[str, list[str]]:
        """{table: [columns]} for all user tables."""
        df = self._query(
            "SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        return {t: g["COLUMN_NAME"].tolist() for t, g in df.groupby("TABLE_NAME", sort=False)}

    def list_processes(self, limit: int = 100) -> pd.DataFrame:
        """Most recent processes first. ``ProcessStart`` is vendor-native (long)."""
        return self._query(
            f"SELECT TOP {int(limit)} ProcessId, UserRef, ProcessStart, Lot, "
            "TemplateId, isProcessEnd FROM t_Process ORDER BY ProcessId DESC"
        )

    def list_steps(self, process_id: int | None = None) -> pd.DataFrame:
        sql = (
            "SELECT StepId, ProcessId, TemplateStepId, t50, P, t99, tf, V0, Vmax, "
            "Regression, samplingRate, triggerLevel, isEnd, dataExcluded FROM t_Steps"
        )
        if process_id is None:
            return self._query(sql + " ORDER BY StepId DESC")
        return self._query(sql + " WHERE ProcessId = ? ORDER BY StepId", (process_id,))

    def latest_step_id(self) -> int | None:
        """Newest step that actually has samples."""
        df = self._query("SELECT MAX(StepId) AS s FROM t_Value")
        v = df["s"].iloc[0]
        return None if pd.isna(v) else int(v)

    def step_series(self, step_id: int) -> pd.DataFrame:
        """Raw signal vs time for a step: columns ``t`` (s), ``value`` (counts)."""
        df = self._query(
            "SELECT xValue AS t, yValue AS value FROM t_Value "
            "WHERE StepId = ? ORDER BY xValue",
            (step_id,),
        )
        return df.astype({"t": float, "value": float})
