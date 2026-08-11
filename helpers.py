"""Shared helper utilities for configuration, storage, sessions and formatting."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, TypeVar

from utils.instruments import point_size, price_decimals


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"
DEFAULT_TRADES_PATH = PROJECT_ROOT / "storage" / "trades.json"


_PROMPT_INJECTION_MARKERS = ["SYSTEM:", "Ignore previous", "###", "<|", "PROMPT:", "ASSISTANT:"]


def sanitize_prompt_text(text: Any, max_len: int = 240) -> str:
    """Strip characters/phrases commonly used for prompt injection before any
    semi-external text (news event titles, memory rules, AI-generated
    reasoning, etc.) is embedded into an AI prompt.

    This is a defensive measure, not a guarantee: it removes a known set of
    injection markers and structural characters (backticks, braces) and caps
    length, but cannot catch every possible injection phrasing.
    """
    if not text:
        return ""
    s = str(text).replace("`", "'").replace("{", "(").replace("}", " )")
    for marker in _PROMPT_INJECTION_MARKERS:
        s = s.replace(marker, "")
    s = " ".join(s.split())
    return s[:max_len]


def setup_logging(level: int = logging.INFO) -> None:
    """Configure console logging for GitHub Actions."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def is_weekend_hebron(now=None) -> bool:
    """Weekend gate (operator directives 2026-08-09/10).

    Saturday: fully closed. Sunday: closed UNTIL the weekly market open
    (~21:00 UTC broker open), open afterwards — the operator wants the
    Sunday-night open captured while weekend daytime stays silent.
    VPS clock runs UTC."""
    from datetime import datetime
    if now is None:
        now = datetime.utcnow()
    wd = now.weekday()  # 5=Sat, 6=Sun
    if wd == 5:
        return True
    if wd == 6:
        return now.hour < 21
    return False


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    """Load config.json and resolve ENV: placeholders when useful."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as file:
        config = json.load(file)
    return config


def get_env_or_config(config: Dict[str, Any], dotted_path: str, env_name: str | None = None, default: Any = None) -> Any:
    """Read value from environment first, then from nested config."""
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    current: Any = config
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    if isinstance(current, str) and current.startswith("ENV:"):
        return os.environ.get(current.replace("ENV:", "", 1), default)
    return current


def format_price(price: float | int | str | None, symbol: str | None = None) -> str:
    """Format a price using instrument-specific decimals."""
    decimals = price_decimals(symbol) if symbol else 2
    try:
        return f"{float(price):.{decimals}f}"
    except (TypeError, ValueError):
        return f"{0:.{decimals}f}"


def calculate_pips(entry: float, exit_price: float, trade_type: str = "BUY", symbol: str | None = None) -> float:
    """Calculate broker-style points for any configured instrument."""
    ps = point_size(symbol)
    if trade_type.upper() == "SELL":
        return round((entry - exit_price) / ps, 1)
    return round((exit_price - entry) / ps, 1)


def now_utc() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)



def canonical_session_label(dt: datetime | None = None, tz_name: str = "Asia/Jerusalem") -> str:
    """Return the canonical session label used across dashboard and Telegram.

    Labels are based on local Asia/Jerusalem time:
    - Asia Morning: 03:00-09:59
    - London / Europe Midday: 10:00-14:59
    - London + New York Afternoon: 15:00-18:59
    - New York Evening: 19:00-23:59
    - Late New York Night: 00:00-02:59
    """
    from zoneinfo import ZoneInfo

    dt = dt or now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        local = dt.astimezone(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        local = dt.astimezone(timezone.utc)
    hour = local.hour
    if 3 <= hour < 10:
        return "Asia Morning"
    if 10 <= hour < 15:
        return "London / Europe Midday"
    if 15 <= hour < 19:
        return "London + New York Afternoon"
    if 19 <= hour < 24:
        return "New York Evening"
    return "Late New York Night"


def get_current_session(dt: datetime | None = None) -> str:
    """Return a clear FX session label with time range in UTC."""
    dt = dt or now_utc()
    hour = dt.hour
    if 0 <= hour < 7:
        return "Asian Session (00:00-07:00 UTC)"
    if 7 <= hour < 12:
        return "London Session (07:00-12:00 UTC)"
    if 12 <= hour < 16:
        return "London-NY Overlap (12:00-16:00 UTC)"
    if 16 <= hour < 21:
        return "New York Session (16:00-21:00 UTC)"
    return "Late NY Session (21:00-00:00 UTC)"


def is_market_open(dt: datetime | None = None) -> bool:
    """Approximate FX market open state in UTC."""
    dt = dt or now_utc()
    weekday = dt.weekday()  # Monday=0
    if weekday == 5:  # Saturday
        return False
    if weekday == 6 and dt.hour < 22:  # Sunday before open
        return False
    if weekday == 4 and dt.hour >= 22:  # Friday after close
        return False
    return True


_T = TypeVar("_T")


@contextmanager
def _interprocess_file_lock(path: str | Path, timeout: float = 15.0):
    """Serialize read/modify/write transactions across VPS processes.

    Analysis and the tick manager are separate Windows processes.  A normal
    ``threading.Lock`` therefore cannot protect ``storage/trades.json``.  This
    lock uses one byte in a sibling ``.lock`` file (msvcrt on Windows, flock on
    POSIX) and is intentionally kept outside the JSON file, which is atomically
    replaced after every successful mutation.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(f"{target}.lock")
    fh = lock_path.open("a+b")
    acquired = False
    deadline = time.monotonic() + max(float(timeout), 0.1)
    try:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        while not acquired:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for local storage lock: {lock_path}")
                time.sleep(0.025)
        yield
    finally:
        if acquired:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def _load_json_list_strict(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"Local storage is not a JSON list: {path}")
    return data


def _atomic_write_json_list(path: Path, rows: List[Dict[str, Any]]) -> None:
    """Write complete JSON then atomically swap it into place.

    Readers see either the old complete book or the new complete book; they can
    never observe the zero-byte/half-written window produced by open(..., 'w').
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as file:
            tmp_name = file.name
            json.dump(rows, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_name, path)
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def load_trades(path: str | Path | None = None) -> List[Dict[str, Any]]:
    """Load one complete local JSON snapshot.

    Writes are atomic, so a JSON decode failure now means genuine corruption,
    not a harmless in-progress write.  Keep the legacy safe return for report
    readers; transactional writers use ``mutate_trades`` and fail closed.
    """
    trades_path = Path(path) if path else DEFAULT_TRADES_PATH
    try:
        with _interprocess_file_lock(trades_path):
            return _load_json_list_strict(trades_path)
    except TimeoutError:
        # Never turn lock contention into an authoritative empty book: the tick
        # reconciler would interpret that as "cancel every broker pending".
        raise
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logging.getLogger(__name__).error("Invalid local JSON %s: %s", trades_path, exc)
        return []


def load_trades_checked(path: str | Path | None = None) -> List[Dict[str, Any]]:
    """Strict locked read for execution paths; corruption fails closed."""
    trades_path = Path(path) if path else DEFAULT_TRADES_PATH
    with _interprocess_file_lock(trades_path):
        return _load_json_list_strict(trades_path)


def save_trades(trades: List[Dict[str, Any]], path: str | Path | None = None) -> None:
    """Persist a complete list under an inter-process lock + atomic replace."""
    trades_path = Path(path) if path else DEFAULT_TRADES_PATH
    with _interprocess_file_lock(trades_path):
        _atomic_write_json_list(trades_path, trades)


def mutate_trades(
    mutator: Callable[[List[Dict[str, Any]]], _T],
    path: str | Path | None = None,
) -> _T:
    """Run an indivisible local JSON read/modify/write transaction.

    A corrupt source fails closed and is never replaced by an empty list.
    """
    trades_path = Path(path) if path else DEFAULT_TRADES_PATH
    with _interprocess_file_lock(trades_path):
        rows = _load_json_list_strict(trades_path)
        result = mutator(rows)
        _atomic_write_json_list(trades_path, rows)
        return result


def save_trade(trade: Dict[str, Any], path: str | Path | None = None) -> None:
    """Append a trade without losing a concurrent tick-manager update."""
    mutate_trades(lambda trades: trades.append(trade), path)


def get_today_trades(path: str | Path | None = None) -> List[Dict[str, Any]]:
    """Return local fallback trades created today UTC."""
    today = now_utc().date().isoformat()
    results = []
    for trade in load_trades(path):
        created_at = str(trade.get("created_at", ""))
        if created_at.startswith(today):
            results.append(trade)
    return results


def get_agent_weights(config: Dict[str, Any]) -> Dict[str, float]:
    """Return the canonical agent weights from config with safe fallbacks.

    This is the SINGLE SOURCE OF TRUTH for agent weights across the codebase.
    Any module that needs agent weights should call this function instead of
    hard-coding defaults locally.

    Keys starting with '_' (e.g. _description) are silently ignored.
    Non-numeric values are skipped safely.
    """
    config_weights = config.get("agent_weights", {}) or {}
    if config_weights:
        weights = {}
        for k, v in config_weights.items():
            if k.startswith("_"):
                continue
            try:
                weights[k] = float(v)
            except (TypeError, ValueError):
                continue
        total = sum(weights.values())
        # Normalize if the sum is materially off 1.0 (protects against bad configs/tests)
        if total > 0 and abs(total - 1.0) > 0.01:
            weights = {k: v / total for k, v in weights.items()}
        if weights:
            return weights
    return {
        "technical": 0.20,
        "classical": 0.25,
        "smc": 0.20,
        "price_action": 0.20,
        "multitimeframe": 0.15,
    }
