"""SQLite/WAL tick aggregation store for the Auction Flow Agent."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import stat
import statistics
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AuctionFlowStore:
    def __init__(self, path: str | os.PathLike[str] = "storage/auction_flow.sqlite3"):
        resolved = Path(path)
        self.path = resolved if resolved.is_absolute() else PROJECT_ROOT / resolved
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @staticmethod
    def _schema_sql() -> str:
        return """
            CREATE TABLE IF NOT EXISTS seconds (
                ts INTEGER PRIMARY KEY,
                open_mid REAL NOT NULL,
                high_mid REAL NOT NULL,
                low_mid REAL NOT NULL,
                close_mid REAL NOT NULL,
                sum_mid REAL NOT NULL,
                tick_count INTEGER NOT NULL,
                up_count INTEGER NOT NULL,
                down_count INTEGER NOT NULL,
                flat_count INTEGER NOT NULL,
                sum_spread REAL NOT NULL,
                max_spread REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS minutes (
                ts INTEGER PRIMARY KEY,
                open_mid REAL NOT NULL,
                high_mid REAL NOT NULL,
                low_mid REAL NOT NULL,
                close_mid REAL NOT NULL,
                sum_mid REAL NOT NULL,
                tick_count INTEGER NOT NULL,
                up_count INTEGER NOT NULL,
                down_count INTEGER NOT NULL,
                flat_count INTEGER NOT NULL,
                sum_spread REAL NOT NULL,
                max_spread REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_seconds_ts ON seconds(ts);
            CREATE INDEX IF NOT EXISTS idx_minutes_ts ON minutes(ts);
        """

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(str(self.path), timeout=10.0)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("PRAGMA busy_timeout=10000")
            # A deployment rollback may replace/remove the SQLite file while a
            # previously loaded process is still winding down. Self-heal any
            # newly recreated empty file before the first query/write.
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
            ).fetchone()
            if not exists:
                con.executescript(self._schema_sql())
                con.commit()
            yield con
            con.commit()
        finally:
            con.close()

    def _ensure_schema(self) -> None:
        with self._connect():
            pass
        self.ensure_writable_attribute()

    def ensure_writable_attribute(self) -> None:
        """Clear a Windows read-only file attribute on SQLite sidecars.

        ACL ownership is enforced by the installer for the Scheduled Task
        principal; chmod handles the independent DOS read-only attribute.
        """
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.path) + suffix)
            if not path.exists():
                continue
            try:
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            except OSError:
                pass

    @staticmethod
    def _get(tick: Any, key: str, default: Any = 0) -> Any:
        if isinstance(tick, Mapping):
            return tick.get(key, default)
        try:
            return tick[key]
        except Exception:
            return getattr(tick, key, default)

    def record_tick(self, tick: Any) -> bool:
        with self._connect() as con:
            return self._record_tick(con, tick)

    def bulk_record_ticks(self, ticks: Iterable[Any], *, commit_every: int = 20000) -> int:
        """Aggregate a history chunk in memory, then upsert one row/bucket.

        MT5 can return millions of ticks for calibration. Running a SELECT and
        two UPSERTs per historical tick would make the one-command installer
        take hours; this preserves the exact counts while writing only second
        and minute aggregates.
        """
        def bucket_add(book: Dict[int, Dict[str, Any]], key: int, mid: float, spread: float, up: int, down: int, flat: int) -> None:
            row = book.get(key)
            if row is None:
                book[key] = {"open": mid, "high": mid, "low": mid, "close": mid,
                             "sum_mid": mid, "ticks": 1, "up": up, "down": down,
                             "flat": flat, "sum_spread": spread, "max_spread": spread}
                return
            row["high"] = max(row["high"], mid)
            row["low"] = min(row["low"], mid)
            row["close"] = mid
            row["sum_mid"] += mid
            row["ticks"] += 1
            row["up"] += up
            row["down"] += down
            row["flat"] += flat
            row["sum_spread"] += spread
            row["max_spread"] = max(row["max_spread"], spread)

        seconds: Dict[int, Dict[str, Any]] = {}
        minutes: Dict[int, Dict[str, Any]] = {}
        count = 0
        with self._connect() as con:
            meta = {str(k): str(v) for k, v in con.execute("SELECT key,value FROM meta")}
            previous_mid = float(meta.get("last_mid", 0) or 0)
            last_fp = str(meta.get("last_fingerprint", ""))
            last_values: tuple[str, float, float, int] | None = None
            for tick in ticks:
                bid = float(self._get(tick, "bid", 0) or 0)
                ask = float(self._get(tick, "ask", 0) or 0)
                if bid <= 0 or ask <= 0 or ask < bid:
                    continue
                ts_ms = int(self._get(tick, "time_msc", 0) or 0)
                if ts_ms <= 0:
                    ts_ms = int(float(self._get(tick, "time", time.time()) or time.time()) * 1000)
                last = float(self._get(tick, "last", 0) or 0)
                flags = int(self._get(tick, "flags", 0) or 0)
                fingerprint = f"{ts_ms}|{bid:.8f}|{ask:.8f}|{last:.8f}|{flags}"
                if fingerprint == last_fp:
                    continue
                mid = (bid + ask) / 2.0
                spread = ask - bid
                if previous_mid <= 0:
                    previous_mid = mid
                up = 1 if mid > previous_mid else 0
                down = 1 if mid < previous_mid else 0
                flat = 1 if not up and not down else 0
                second = ts_ms // 1000
                bucket_add(seconds, second, mid, spread, up, down, flat)
                bucket_add(minutes, second - second % 60, mid, spread, up, down, flat)
                previous_mid = mid
                last_fp = fingerprint
                last_values = (fingerprint, mid, spread, ts_ms)
                count += 1
            for key, row in seconds.items():
                self._upsert_aggregate(con, "seconds", key, row)
            for key, row in minutes.items():
                self._upsert_aggregate(con, "minutes", key, row)
            if last_values:
                fp, mid, spread, ts_ms = last_values
                pairs = {
                    "last_fingerprint": fp, "last_mid": repr(mid),
                    "last_tick_ts_ms": str(ts_ms), "last_write_ts": repr(time.time()),
                    "last_spread": repr(spread),
                }
                con.executemany(
                    "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    list(pairs.items()),
                )
        return count

    def _record_tick(self, con: sqlite3.Connection, tick: Any) -> bool:
        bid = float(self._get(tick, "bid", 0) or 0)
        ask = float(self._get(tick, "ask", 0) or 0)
        if bid <= 0 or ask <= 0 or ask < bid:
            return False
        ts_ms = int(self._get(tick, "time_msc", 0) or 0)
        if ts_ms <= 0:
            ts_ms = int(float(self._get(tick, "time", time.time()) or time.time()) * 1000)
        last = float(self._get(tick, "last", 0) or 0)
        flags = int(self._get(tick, "flags", 0) or 0)
        fingerprint = f"{ts_ms}|{bid:.8f}|{ask:.8f}|{last:.8f}|{flags}"
        prior_fp_row = con.execute("SELECT value FROM meta WHERE key='last_fingerprint'").fetchone()
        if prior_fp_row and prior_fp_row[0] == fingerprint:
            return False
        mid = (bid + ask) / 2.0
        spread = ask - bid
        prev_mid_row = con.execute("SELECT value FROM meta WHERE key='last_mid'").fetchone()
        previous_mid = float(prev_mid_row[0]) if prev_mid_row else mid
        up = 1 if mid > previous_mid else 0
        down = 1 if mid < previous_mid else 0
        flat = 1 if not up and not down else 0
        second = ts_ms // 1000
        minute = second - (second % 60)
        self._upsert_bucket(con, "seconds", second, mid, spread, up, down, flat)
        self._upsert_bucket(con, "minutes", minute, mid, spread, up, down, flat)
        pairs = {
            "last_fingerprint": fingerprint,
            "last_mid": repr(mid),
            "last_tick_ts_ms": str(ts_ms),
            "last_write_ts": repr(time.time()),
            "last_spread": repr(spread),
        }
        con.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            list(pairs.items()),
        )
        return True

    @staticmethod
    def _upsert_aggregate(con: sqlite3.Connection, table: str, ts: int, row: Mapping[str, Any]) -> None:
        con.execute(
            f"""
            INSERT INTO {table}(
                ts,open_mid,high_mid,low_mid,close_mid,sum_mid,tick_count,
                up_count,down_count,flat_count,sum_spread,max_spread
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ts) DO UPDATE SET
                high_mid=max({table}.high_mid,excluded.high_mid),
                low_mid=min({table}.low_mid,excluded.low_mid),
                close_mid=excluded.close_mid,
                sum_mid={table}.sum_mid+excluded.sum_mid,
                tick_count={table}.tick_count+excluded.tick_count,
                up_count={table}.up_count+excluded.up_count,
                down_count={table}.down_count+excluded.down_count,
                flat_count={table}.flat_count+excluded.flat_count,
                sum_spread={table}.sum_spread+excluded.sum_spread,
                max_spread=max({table}.max_spread,excluded.max_spread)
            """,
            (ts, row["open"], row["high"], row["low"], row["close"],
             row["sum_mid"], row["ticks"], row["up"], row["down"], row["flat"],
             row["sum_spread"], row["max_spread"]),
        )

    @staticmethod
    def _upsert_bucket(
        con: sqlite3.Connection, table: str, ts: int, mid: float, spread: float,
        up: int, down: int, flat: int,
    ) -> None:
        con.execute(
            f"""
            INSERT INTO {table}(
                ts,open_mid,high_mid,low_mid,close_mid,sum_mid,tick_count,
                up_count,down_count,flat_count,sum_spread,max_spread
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ts) DO UPDATE SET
                high_mid=max({table}.high_mid,excluded.high_mid),
                low_mid=min({table}.low_mid,excluded.low_mid),
                close_mid=excluded.close_mid,
                sum_mid={table}.sum_mid+excluded.sum_mid,
                tick_count={table}.tick_count+1,
                up_count={table}.up_count+excluded.up_count,
                down_count={table}.down_count+excluded.down_count,
                flat_count={table}.flat_count+excluded.flat_count,
                sum_spread={table}.sum_spread+excluded.sum_spread,
                max_spread=max({table}.max_spread,excluded.max_spread)
            """,
            (ts, mid, mid, mid, mid, mid, 1, up, down, flat, spread, spread),
        )

    def mark_heartbeat(self) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO meta(key,value) VALUES('collector_heartbeat',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (repr(time.time()),),
            )

    def meta(self) -> Dict[str, str]:
        with self._connect() as con:
            return {str(k): str(v) for k, v in con.execute("SELECT key,value FROM meta")}

    def health(self, *, now: float | None = None) -> Dict[str, Any]:
        now = float(now if now is not None else time.time())
        meta = self.meta()
        broker_tick = float(meta.get("last_tick_ts_ms", 0) or 0) / 1000.0
        # MT5 time_msc is broker-server time and can be several hours ahead of
        # UTC. Freshness must use when this process actually wrote the tick,
        # otherwise max(0, now-broker_tick) becomes exactly 0 and downstream
        # code once converted that valid zero to infinity.
        last_write = float(meta.get("last_write_ts", 0) or 0)
        heartbeat = float(meta.get("collector_heartbeat", 0) or 0)
        with self._connect() as con:
            recent = con.execute(
                "SELECT COALESCE(SUM(tick_count),0) FROM seconds WHERE ts>=?",
                (int(now) - 300,),
            ).fetchone()
        return {
            "last_tick_age_seconds": max(0.0, now - last_write) if last_write else math.inf,
            "heartbeat_age_seconds": max(0.0, now - heartbeat) if heartbeat else math.inf,
            "broker_tick_time_seconds": broker_tick or None,
            "last_local_write_seconds": last_write or None,
            "unique_ticks_5m": int((recent or [0])[0] or 0),
            "current_spread": float(meta.get("last_spread", 0) or 0),
            "last_mid": float(meta.get("last_mid", 0) or 0),
            "path": str(self.path),
        }

    def flow_state(
        self,
        *,
        session_timezone: str = "Asia/Hebron",
        point_value: float = 0.10,
        profile_bin_points: float = 10.0,
        value_area_pct: float = 70.0,
        now: float | None = None,
    ) -> Dict[str, Any]:
        now = float(now if now is not None else time.time())
        zone = ZoneInfo(session_timezone)
        local = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(zone)
        start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = start_local.astimezone(timezone.utc).timestamp()
        with self._connect() as con:
            session_rows = con.execute(
                "SELECT ts,open_mid,high_mid,low_mid,close_mid,sum_mid,tick_count,up_count,down_count,flat_count,sum_spread,max_spread "
                "FROM minutes WHERE ts>=? AND ts<=? ORDER BY ts",
                (int(start_utc), int(now)),
            ).fetchall()
            rows5 = con.execute(
                "SELECT ts,open_mid,high_mid,low_mid,close_mid,sum_mid,tick_count,up_count,down_count,flat_count,sum_spread,max_spread "
                "FROM minutes WHERE ts>=? ORDER BY ts",
                (int(now) - 300,),
            ).fetchall()
            row60 = con.execute(
                "SELECT COALESCE(SUM(up_count),0),COALESCE(SUM(down_count),0),COALESCE(SUM(tick_count),0) "
                "FROM seconds WHERE ts>=?",
                (int(now) - 60,),
            ).fetchone()
            activity_rows = con.execute(
                "SELECT tick_count FROM minutes WHERE ts>=? AND ts<? ORDER BY ts",
                (int(now) - 1860, int(now) - 60),
            ).fetchall()

        total_ticks = sum(int(r[6]) for r in session_rows)
        total_mid = sum(float(r[5]) for r in session_rows)
        vwap = total_mid / total_ticks if total_ticks else 0.0
        bin_size = max(float(profile_bin_points) * float(point_value), float(point_value))
        profile: Dict[float, int] = {}
        for row in session_rows:
            ticks = int(row[6])
            if ticks <= 0:
                continue
            avg = float(row[5]) / ticks
            key = round(round(avg / bin_size) * bin_size, 8)
            profile[key] = profile.get(key, 0) + ticks
        poc, vah, val = self._value_area(profile, value_area_pct)
        up5 = sum(int(r[7]) for r in rows5)
        down5 = sum(int(r[8]) for r in rows5)
        up60, down60, ticks60 = [int(x or 0) for x in (row60 or (0, 0, 0))]
        imbalance60 = (up60 - down60) / max(up60 + down60, 1)
        imbalance5 = (up5 - down5) / max(up5 + down5, 1)
        rates = [int(r[0]) for r in activity_rows if int(r[0]) > 0]
        median_rate = statistics.median(rates) if rates else 0.0
        activity_ratio = float(ticks60) / median_rate if median_rate > 0 else 0.0
        recent = [
            {
                "ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4]), "ticks": int(r[6]),
                "up": int(r[7]), "down": int(r[8]),
                "avg_spread": float(r[10]) / max(int(r[6]), 1),
                "max_spread": float(r[11]),
            }
            for r in rows5
        ]
        return {
            "session_start": start_local.isoformat(),
            "session_vwap": vwap,
            "poc": poc,
            "vah": vah,
            "val": val,
            "profile_bins": len(profile),
            "session_ticks": total_ticks,
            "imbalance_60s": imbalance60,
            "imbalance_5m": imbalance5,
            "activity_ratio": activity_ratio,
            "recent_minutes": recent,
            "health": self.health(now=now),
        }

    @staticmethod
    def _value_area(profile: Mapping[float, int], value_area_pct: float) -> tuple[float, float, float]:
        if not profile:
            return 0.0, 0.0, 0.0
        ordered = sorted(profile)
        poc = max(ordered, key=lambda p: (int(profile[p]), -abs(p)))
        target = sum(int(v) for v in profile.values()) * max(0.01, min(1.0, float(value_area_pct) / 100.0))
        selected = {poc}
        total = int(profile[poc])
        left = ordered.index(poc) - 1
        right = ordered.index(poc) + 1
        while total < target and (left >= 0 or right < len(ordered)):
            lv = int(profile[ordered[left]]) if left >= 0 else -1
            rv = int(profile[ordered[right]]) if right < len(ordered) else -1
            if rv > lv:
                price = ordered[right]
                right += 1
            else:
                price = ordered[left]
                left -= 1
            selected.add(price)
            total += int(profile[price])
        return float(poc), float(max(selected)), float(min(selected))

    def prune(self, *, second_days: int = 7, minute_days: int = 90) -> None:
        now = int(time.time())
        with self._connect() as con:
            con.execute("DELETE FROM seconds WHERE ts<?", (now - int(second_days) * 86400,))
            con.execute("DELETE FROM minutes WHERE ts<?", (now - int(minute_days) * 86400,))
            # End the write transaction before asking SQLite to checkpoint WAL.
            # Windows raised "database table is locked" when checkpoint ran in
            # the same transaction immediately after 5.2M tick aggregates.
            con.commit()
        # Checkpointing is housekeeping, never a calibration/trading gate. A
        # concurrent dashboard reader may hold the WAL briefly; PASSIVE can be
        # retried by SQLite later and must not fail the one-command install.
        try:
            with sqlite3.connect(str(self.path), timeout=10.0) as con:
                con.execute("PRAGMA busy_timeout=10000")
                con.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.OperationalError:
            pass
