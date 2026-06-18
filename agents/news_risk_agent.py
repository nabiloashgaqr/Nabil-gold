"""News & Risk Agent.

يراقب الأخبار المؤثرة على الذهب والدولار ويمنع الإشارات في الأوقات الخطرة.
النسخة الحالية مناسبة لـ GitHub Actions: تقرأ أحداثاً يدوية من
``storage/news_events.json`` أو من متغير البيئة ``NEWS_EVENTS_JSON``، ولا تحتاج
خدمة خارجية. يمكن لاحقاً ربطها بمصدر أخبار اقتصادي.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from agents.base_agent import BaseAgent
from utils.helpers import get_current_session, load_config


class NewsRiskAgent(BaseAgent):
    """Classify economic/news risk and decide whether trading is allowed."""

    name = "news_risk"

    HIGH_IMPACT_KEYWORDS = {
        "NFP",
        "NONFARM",
        "CPI",
        "FOMC",
        "GDP",
        "INTEREST RATE",
        "RATE DECISION",
        "FED RATE",
        "FEDERAL FUNDS",
        "PCE",
        "POWELL",
        "FOMC MINUTES",
        "ISM SERVICES",
    }
    MEDIUM_IMPACT_KEYWORDS = {
        "PMI",
        "RETAIL SALES",
        "UNEMPLOYMENT CLAIMS",
        "JOBLESS CLAIMS",
        "JOLTS",
        "PPI",
        "ADP",
        "CONSUMER CONFIDENCE",
        "DURABLE GOODS",
    }
    GOLD_RELEVANT_CURRENCIES = {"USD", "XAU", "GOLD", "US"}

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        super().__init__(config or load_config())
        self.events_path = Path(__file__).resolve().parents[1] / "storage" / "news_events.json"

    def check(self, now: datetime | None = None) -> Dict[str, Any]:
        """Return market news status: SAFE/CAUTION/DANGER/HIGH_VOLATILITY."""
        try:
            now = now or datetime.now(timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            filters = self.config.get("filters", {})
            before_high = int(filters.get("no_signal_before_news_minutes", 30))
            after_high = int(filters.get("no_signal_after_news_minutes", 15))
            warning_before = max(60, before_high)
            medium_window = max(30, before_high)
            upcoming: List[Dict[str, Any]] = []
            restrictions: List[str] = []
            warnings: List[str] = []
            market_status = "SAFE"
            can_trade = True
            risk_score = 8

            for event in self._load_events():
                event_time = self._parse_time(str(event.get("time", "")))
                if event_time is None:
                    continue
                event_time = event_time.astimezone(timezone.utc)
                impact = self._classify_impact(event)
                if not self._is_gold_relevant(event):
                    continue

                minutes_until = int((event_time - now).total_seconds() / 60)
                enriched = self._enrich_event(event, impact, minutes_until)

                if -after_high <= minutes_until <= 24 * 60:
                    upcoming.append(enriched)

                # High impact hard filters.
                if impact == "HIGH" and 0 <= minutes_until <= before_high:
                    market_status = "DANGER"
                    can_trade = False
                    risk_score = max(risk_score, 95)
                    restrictions.append(f"لا تداول - خبر عالي التأثير {event.get('event', 'News')} خلال {minutes_until} دقيقة")
                elif impact == "HIGH" and -after_high <= minutes_until < 0:
                    market_status = "HIGH_VOLATILITY"
                    can_trade = False
                    risk_score = max(risk_score, 88)
                    restrictions.append(f"لا تداول - خبر عالي التأثير {event.get('event', 'News')} صدر منذ {abs(minutes_until)} دقيقة")
                elif impact == "HIGH" and before_high < minutes_until <= warning_before:
                    if market_status == "SAFE":
                        market_status = "CAUTION"
                    risk_score = max(risk_score, 55)
                    warnings.append(f"تحذير - خبر عالي التأثير خلال {minutes_until} دقيقة")

                # Medium impact reduces confidence but does not fully block by itself.
                if impact == "MEDIUM" and -after_high <= minutes_until <= medium_window:
                    if market_status == "SAFE":
                        market_status = "CAUTION"
                    risk_score = max(risk_score, 60)
                    warnings.append(f"حذر - خبر متوسط التأثير {event.get('event', 'News')} قريب")

            session_status, session_risk, session_warning = self._session_risk(now)
            risk_score = max(risk_score, session_risk)
            if session_status == "CAUTION" and market_status == "SAFE":
                market_status = "CAUTION"
            if session_warning:
                warnings.append(session_warning)

            if restrictions:
                active_restrictions = restrictions
            else:
                active_restrictions = warnings

            upcoming.sort(key=lambda item: item.get("minutes_until", 999999))
            session = get_current_session(now)
            return {
                "agent": self.name,
                "market_status": market_status,
                "can_trade": can_trade,
                "upcoming_events": upcoming[:10],
                "active_restrictions": active_restrictions[:8],
                "session_info": {
                    "current_session": session,
                    "volatility_expected": self._session_volatility(session),
                    "best_for_gold": session in {"London", "London-NY Overlap", "New York"},
                },
                "risk_score": min(100, int(risk_score)),
                "summary": self._summary(market_status, can_trade, upcoming, active_restrictions),
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("News risk check failed")
            return {
                "agent": self.name,
                "market_status": "CAUTION",
                "can_trade": True,
                "upcoming_events": [],
                "active_restrictions": [f"فشل فحص الأخبار: {exc}"],
                "session_info": {"current_session": get_current_session(), "volatility_expected": "UNKNOWN", "best_for_gold": False},
                "risk_score": 50,
                "summary": "فشل فحص الأخبار - تشغيل بحذر",
            }

    def _load_events(self) -> List[Dict[str, Any]]:
        """Load manual events from env/config/file."""
        events: List[Dict[str, Any]] = []
        env_events = os.environ.get("NEWS_EVENTS_JSON")
        if env_events:
            try:
                parsed = json.loads(env_events)
                if isinstance(parsed, list):
                    events.extend(parsed)
                elif isinstance(parsed, dict):
                    events.extend(parsed.get("events", []))
            except json.JSONDecodeError as exc:
                self.logger.warning("Invalid NEWS_EVENTS_JSON: %s", exc)

        config_events = self.config.get("news_events", [])
        if isinstance(config_events, list):
            events.extend(config_events)

        if self.events_path.exists():
            try:
                with self.events_path.open("r", encoding="utf-8") as file:
                    data = json.load(file)
                if isinstance(data, list):
                    events.extend(data)
                elif isinstance(data, dict):
                    events.extend(data.get("events", []))
            except json.JSONDecodeError as exc:
                self.logger.warning("Invalid news events file: %s", exc)
        return [event for event in events if isinstance(event, dict)]

    def _classify_impact(self, event: Dict[str, Any]) -> str:
        explicit = str(event.get("impact", "")).upper().strip()
        if explicit in {"HIGH", "MEDIUM", "LOW"}:
            return explicit
        name = str(event.get("event", event.get("name", ""))).upper()
        if any(keyword in name for keyword in self.HIGH_IMPACT_KEYWORDS):
            return "HIGH"
        if any(keyword in name for keyword in self.MEDIUM_IMPACT_KEYWORDS):
            return "MEDIUM"
        return "LOW"

    def _is_gold_relevant(self, event: Dict[str, Any]) -> bool:
        currency = str(event.get("currency", "USD")).upper()
        name = str(event.get("event", event.get("name", ""))).upper()
        if currency in self.GOLD_RELEVANT_CURRENCIES:
            return True
        return any(keyword in name for keyword in ["USD", "FED", "FOMC", "INFLATION", "CPI", "PCE", "GOLD", "XAU", "TREASURY"])

    def _enrich_event(self, event: Dict[str, Any], impact: str, minutes_until: int) -> Dict[str, Any]:
        return {
            "event": event.get("event", event.get("name", "Unknown Event")),
            "time": event.get("time"),
            "impact": impact,
            "currency": str(event.get("currency", "USD")).upper(),
            "minutes_until": minutes_until,
            "expected": event.get("expected"),
            "previous": event.get("previous"),
        }

    def _session_risk(self, now: datetime) -> Tuple[str, int, str | None]:
        session = get_current_session(now)
        if session == "Late NY / Rollover":
            return "CAUTION", 35, "حذر - فترة rollover/liquidity منخفضة"
        if session == "Asian":
            return "SAFE", 18, None
        if session == "London-NY Overlap":
            return "SAFE", 22, None
        return "SAFE", 12, None

    def _session_volatility(self, session: str) -> str:
        if session == "London-NY Overlap":
            return "HIGH"
        if session in {"London", "New York"}:
            return "MODERATE_HIGH"
        if session == "Asian":
            return "MODERATE"
        return "LOW"

    def _summary(self, market_status: str, can_trade: bool, upcoming: List[Dict[str, Any]], restrictions: List[str]) -> str:
        if not can_trade:
            return restrictions[0] if restrictions else "السوق خطر حالياً - لا تداول"
        if market_status == "CAUTION":
            return restrictions[0] if restrictions else "السوق يحتاج حذراً بسبب أخبار/جلسة تداول"
        high_events = [event for event in upcoming if event.get("impact") == "HIGH" and event.get("minutes_until", 9999) >= 0]
        if high_events:
            first = high_events[0]
            return f"سوق آمن حالياً، أقرب خبر عالي التأثير خلال {first.get('minutes_until')} دقيقة"
        return "سوق آمن حالياً، لا أخبار عالية التأثير قريبة"

    def _parse_time(self, value: str) -> datetime | None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            # Support common compact format if users enter it manually.
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        return None
