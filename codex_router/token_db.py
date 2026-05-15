"""SQLite-backed token usage persistence for codex_router."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    preset_name TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    method TEXT NOT NULL DEFAULT 'http'
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ts ON token_usage (timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_preset_ts ON token_usage (preset_name, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_date ON token_usage (date(timestamp));",
]


class TokenDB:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(_CREATE_TABLE)
        for idx in _CREATE_INDEXES:
            self._conn.execute(idx)
        self._conn.commit()

    def record(
        self,
        preset_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        method: str = "http",
    ) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._conn.execute(
            "INSERT INTO token_usage (timestamp, preset_name, model, input_tokens, output_tokens, total_tokens, method) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now, preset_name, model, input_tokens, output_tokens, total_tokens, method),
        )
        self._conn.commit()

    def get_total(self) -> dict[str, Any]:
        cur = self._conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(total_tokens),0), COUNT(*) FROM token_usage"
        )
        row = cur.fetchone()
        return {
            "input_tokens": row[0],
            "output_tokens": row[1],
            "total_tokens": row[2],
            "request_count": row[3],
        }

    def get_by_preset(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT preset_name, COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(total_tokens),0), COUNT(*) "
            "FROM token_usage GROUP BY preset_name ORDER BY SUM(total_tokens) DESC"
        )
        return [
            {
                "preset_name": r[0],
                "input_tokens": r[1],
                "output_tokens": r[2],
                "total_tokens": r[3],
                "request_count": r[4],
            }
            for r in cur.fetchall()
        ]

    def get_daily(self, days: int = 30) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT date(timestamp), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(total_tokens),0), COUNT(*) "
            "FROM token_usage WHERE timestamp >= datetime('now', ?||' days') "
            "GROUP BY date(timestamp) ORDER BY date(timestamp)",
            (str(-days),),
        )
        return [
            {"date": r[0], "input_tokens": r[1], "output_tokens": r[2], "total_tokens": r[3], "request_count": r[4]}
            for r in cur.fetchall()
        ]

    def get_weekly(self, weeks: int = 12) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT strftime('%Y-%W', timestamp), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(total_tokens),0), COUNT(*) "
            "FROM token_usage WHERE timestamp >= datetime('now', ?||' days') "
            "GROUP BY strftime('%Y-%W', timestamp) ORDER BY 1",
            (str(-weeks * 7),),
        )
        return [
            {"week": r[0], "input_tokens": r[1], "output_tokens": r[2], "total_tokens": r[3], "request_count": r[4]}
            for r in cur.fetchall()
        ]

    def get_monthly(self, months: int = 12) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT strftime('%Y-%m', timestamp), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(total_tokens),0), COUNT(*) "
            "FROM token_usage WHERE timestamp >= datetime('now', ?||' months') "
            "GROUP BY strftime('%Y-%m', timestamp) ORDER BY 1",
            (str(-months),),
        )
        return [
            {"month": r[0], "input_tokens": r[1], "output_tokens": r[2], "total_tokens": r[3], "request_count": r[4]}
            for r in cur.fetchall()
        ]

    def get_hourly_curve(self, date: str | None = None) -> list[dict[str, Any]]:
        if date is None:
            date = "date('now')"
        else:
            date = f"'{date}'"
        cur = self._conn.execute(
            f"SELECT strftime('%H', timestamp), COALESCE(SUM(total_tokens),0), COUNT(*) "
            f"FROM token_usage WHERE date(timestamp) = {date} "
            f"GROUP BY strftime('%H', timestamp) ORDER BY 1"
        )
        hour_map = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        return [
            {"hour": f"{h:02d}", "total_tokens": hour_map.get(f"{h:02d}", (0, 0))[0],
             "request_count": hour_map.get(f"{h:02d}", (0, 0))[1]}
            for h in range(24)
        ]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
