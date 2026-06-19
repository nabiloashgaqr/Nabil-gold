"""Trading Session Agent v2.0.

يتحقق من ساعات التداول المسموحة ويمنع الإشارات خارج ساعات العمل.
يدعم جلسات متعددة مع allow_signals و allow_reports.

days في config.json: 0=Sunday, 1=Monday, ..., 6=Saturday (مثل Python weekday())

الSessions:
- Trading Session (11-17): allow_signals=true, allow_reports=false
- Report Session (22-23): allow_signals=false, allow_reports=true
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from utils.helpers import load_config


class TradingSessionAgent(BaseAgent):
    """Check if current time is within allowed trading hours."""

    name = "trading_session"

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        super().__init__(config or load_config())
        self.hours_config = self.config.get("trading_hours", {})
        self.signal_filters = self.config.get("signal_filters", {})
        self.quality_order = {"BEST": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}

    def check(self, now: datetime | None = None) -> Dict[str, Any]:
        """Return trading session status and whether trading is allowed."""
        try:
            now = now or datetime.now(timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)

            session_timezone = self.hours_config.get("timezone", "UTC")
            try:
                local_now = now.astimezone(ZoneInfo(str(session_timezone)))
            except Exception:  # noqa: BLE001 - fallback safely to UTC if timezone is invalid
                local_now = now.astimezone(timezone.utc)

            if not self.hours_config.get("enabled", False):
                return self._allowed(
                    reason="Trading hours check is disabled",
                    current_session=None,
                    session_quality="UNKNOWN",
                    trading_allowed=True,
                    allow_signals=True,
                    allow_reports=True,
                )

            # Check Friday after-hours (before session check)
            if local_now.weekday() == 4:  # Friday
                cutoff = int(self.hours_config.get("friday_cutoff_hour", 20))
                if local_now.hour >= cutoff and not self.signal_filters.get("allow_friday_after_hours", False):
                    return self._blocked(
                        reason=f"Friday after {cutoff}:00 UTC - weekend approaching",
                        current_session=None,
                        session_quality="LOW",
                        next_session=self._next_session_info(local_now),
                    )

            # Find active session (handles all day/weekend checks via session.days)
            active_session = self._find_active_session(local_now)
            if active_session is None:
                # Check if it's a weekend day not in any session
                is_weekend = local_now.weekday() in {5, 6}  # Saturday/Sunday
                if is_weekend:
                    return self._blocked(
                        reason="Weekend - markets closed",
                        current_session=None,
                        session_quality="NONE",
                        next_session=self._next_session_info(local_now),
                    )
                return self._blocked(
                    reason="Outside trading hours",
                    current_session=None,
                    session_quality="NONE",
                    next_session=self._next_session_info(local_now),
                )

            session_name = active_session.get("name", "Unknown")
            session_quality = active_session.get("quality", "UNKNOWN")
            
            # 🚀 الحصول على allow_signals و allow_reports
            allow_signals = active_session.get("allow_signals", True)
            allow_reports = active_session.get("allow_reports", False)
            
            # Check minimum quality requirement
            min_quality = self.hours_config.get("min_quality_required", "HIGH")
            if self._quality_order(session_quality) > self._quality_order(min_quality):
                return self._blocked(
                    reason=f"Session quality ({session_quality}) below minimum ({min_quality})",
                    current_session=session_name,
                    session_quality=session_quality,
                    allow_signals=allow_signals,
                    allow_reports=allow_reports,
                )

            return self._allowed(
                reason=f"Active session: {session_name} ({session_quality} quality)",
                current_session=session_name,
                session_quality=session_quality,
                trading_allowed=True,
                allow_signals=allow_signals,
                allow_reports=allow_reports,
                session_details={
                    "name": session_name,
                    "quality": session_quality,
                    "allow_signals": allow_signals,
                    "allow_reports": allow_reports,
                    "description": active_session.get("description", ""),
                    "hours": f"{int(active_session.get('start_hour', 0)):02d}:{int(active_session.get('start_minute', 0)):02d} - {int(active_session.get('end_hour', 23)):02d}:{int(active_session.get('end_minute', 59)):02d} {self.hours_config.get('timezone', 'UTC')}",
                    "days": active_session.get("days", []),
                },
            )

        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Trading session check failed")
            return self._allowed(
                reason=f"Session check failed: {exc} - allowing by default",
                current_session=None,
                session_quality="UNKNOWN",
                trading_allowed=True,
                allow_signals=True,
                allow_reports=True,
            )

    def _find_active_session(self, now: datetime) -> Dict[str, Any] | None:
        """Find which session is currently active. Prioritize the most specific session."""
        sessions = self.hours_config.get("sessions", [])
        current_minutes = now.hour * 60 + now.minute
        current_weekday = now.weekday()  # 0=Monday ... 6=Sunday

        matches: List[Dict[str, Any]] = []
        for session in sessions:
            start_hour = int(session.get("start_hour", 0))
            end_hour = int(session.get("end_hour", 23))
            start_minute = int(session.get("start_minute", 0))
            # Preserve legacy behavior: end_hour without end_minute means through the whole hour.
            end_minute = int(session.get("end_minute", 59))
            start = start_hour * 60 + start_minute
            end = end_hour * 60 + end_minute
            # days: 0=Sunday, 1=Monday, ..., 6=Saturday (Python weekday format)
            allowed_days = [int(d) for d in session.get("days", [1, 2, 3, 4, 5])]

            if current_weekday not in allowed_days:
                continue

            if start <= end:
                if start <= current_minutes <= end:
                    matches.append(session)
            else:
                # Overnight range (e.g., 22:00-06:00)
                if current_minutes >= start or current_minutes <= end:
                    matches.append(session)

        if not matches:
            return None

        # Pick the narrowest session (smallest duration = most specific)
        quality_order = {"BEST": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
        return min(
            matches,
            key=lambda s: (
                ((int(s.get("end_hour", 23)) * 60 + int(s.get("end_minute", 59))) - (int(s.get("start_hour", 0)) * 60 + int(s.get("start_minute", 0)))) % (24 * 60),
                quality_order.get(str(s.get("quality", "UNKNOWN")).upper(), 4),
            ),
        )

    def _next_session_info(self, now: datetime) -> Dict[str, Any] | None:
        """Return info about the next upcoming session."""
        sessions = self.hours_config.get("sessions", [])
        for offset in range(1, 8):
            check_date = (now + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            check_weekday = check_date.weekday()  # 0=Monday ... 6=Sunday
            for session in sessions:
                # days: 0=Sunday, 1=Monday, ..., 6=Saturday (Python weekday format)
                allowed_days = [int(d) for d in session.get("days", [1, 2, 3, 4, 5])]
                if check_weekday in allowed_days:
                    return {
                        "session": session.get("name", "Unknown"),
                        "day": check_date.strftime("%A"),
                        "hour": session.get("start_hour", 8),
                        "minute": session.get("start_minute", 0),
                        "quality": session.get("quality", "UNKNOWN"),
                    }
        return None

    def _quality_order(self, quality: str) -> int:
        return self.quality_order.get(quality.upper(), 4)

    def _allowed(
        self,
        reason: str,
        current_session: str | None,
        session_quality: str,
        trading_allowed: bool = True,
        allow_signals: bool = True,
        allow_reports: bool = False,
        session_details: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "trading_allowed": trading_allowed,
            "reason": reason,
            "current_session": current_session,
            "session_quality": session_quality,
            "allow_signals": allow_signals,
            "allow_reports": allow_reports,
            "session_details": session_details,
            "is_trading_hours": trading_allowed,
            "summary": f"{'✅' if allow_signals else '🚫'} الجلسة: {current_session or 'غير محدد'} | الجودة: {session_quality} | {reason}",
        }

    def _blocked(
        self,
        reason: str,
        current_session: str | None,
        session_quality: str,
        allow_signals: bool = False,
        allow_reports: bool = False,
        next_session: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "trading_allowed": False,
            "reason": reason,
            "current_session": current_session,
            "session_quality": session_quality,
            "allow_signals": allow_signals,
            "allow_reports": allow_reports,
            "session_details": None,
            "is_trading_hours": False,
            "next_session": next_session,
            "summary": f"🚫 خارج ساعات التداول - {reason}",
        }