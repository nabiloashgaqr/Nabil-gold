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
from agents.macro_fundamental_agent import MacroFundamentalAgent
from utils.helpers import get_current_session, load_config, sanitize_prompt_text


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
    TIER1_KEYWORDS = {
        "FOMC", "INTEREST RATE", "RATE DECISION", "FED RATE", "FEDERAL FUNDS",
        "NONFARM", "NFP", "CPI", "PCE", "POWELL", "CENTRAL BANK", "ECB", "BOE", "BOJ",
    }
    TIER2_KEYWORDS = {
        "PPI", "PMI", "RETAIL SALES", "UNEMPLOYMENT CLAIMS", "JOBLESS CLAIMS", "ADP",
        "JOLTS", "ISM", "GDP", "CONSUMER CONFIDENCE", "DURABLE GOODS",
    }

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
            news_cfg = self.config.get("news_risk", {}) or {}
            before_high = int(filters.get("no_signal_before_news_minutes", 30))
            after_high = int(filters.get("no_signal_after_news_minutes", 15))
            tier1_before = int(news_cfg.get("tier1_before_minutes", max(60, before_high)))
            tier1_after = int(news_cfg.get("tier1_after_minutes", max(30, after_high)))
            tier2_before = int(news_cfg.get("tier2_before_minutes", max(30, before_high)))
            tier2_after = int(news_cfg.get("tier2_after_minutes", after_high))
            warning_before = int(news_cfg.get("warning_before_minutes", max(120, tier1_before)))
            medium_window = tier2_before
            upcoming: List[Dict[str, Any]] = []
            restrictions: List[str] = []
            warnings: List[str] = []
            market_status = "SAFE"
            can_trade = True
            risk_score = 8
            tier1_events_24h = 0
            tier2_events_24h = 0
            event_tiers: List[str] = []

            for event in self._load_events():
                event_time = self._parse_time(str(event.get("time", "")))
                if event_time is None:
                    continue
                event_time = event_time.astimezone(timezone.utc)
                impact = self._classify_impact(event)
                tier = self._classify_tier(event, impact)
                if not self._is_gold_relevant(event):
                    continue

                minutes_until = int((event_time - now).total_seconds() / 60)
                if 0 <= minutes_until <= 24 * 60:
                    if tier == "TIER_1":
                        tier1_events_24h += 1
                    elif tier == "TIER_2":
                        tier2_events_24h += 1
                enriched = self._enrich_event(event, impact, minutes_until, tier)
                event_tiers.append(tier)

                if -after_high <= minutes_until <= 24 * 60:
                    upcoming.append(enriched)

                event_name = event.get('event', event.get('name', 'News'))
                if tier == "TIER_1" and 0 <= minutes_until <= tier1_before:
                    market_status = "DANGER"
                    can_trade = False
                    risk_score = max(risk_score, 98)
                    restrictions.append(f"No trading - Tier 1 {event_name} in {minutes_until} min")
                elif tier == "TIER_1" and -tier1_after <= minutes_until < 0:
                    market_status = "HIGH_VOLATILITY"
                    can_trade = False
                    risk_score = max(risk_score, 92)
                    restrictions.append(f"No trading - Tier 1 {event_name} released {abs(minutes_until)} min ago")
                elif tier == "TIER_1" and tier1_before < minutes_until <= warning_before:
                    if market_status == "SAFE":
                        market_status = "CAUTION"
                    risk_score = max(risk_score, 65)
                    warnings.append(f"Strong warning - Tier 1 {event_name} in {minutes_until} min")

                elif tier == "TIER_2" and 0 <= minutes_until <= tier2_before:
                    if market_status == "SAFE":
                        market_status = "CAUTION"
                    risk_score = max(risk_score, 70 if impact == "HIGH" else 60)
                    warnings.append(f"Caution - Tier 2 {event_name} in {minutes_until} min")
                elif tier == "TIER_2" and -tier2_after <= minutes_until < 0:
                    if market_status == "SAFE":
                        market_status = "CAUTION"
                    risk_score = max(risk_score, 62)
                    warnings.append(f"Caution - Tier 2 {event_name} released {abs(minutes_until)} min ago")

                elif impact == "HIGH" and 0 <= minutes_until <= before_high:
                    market_status = "DANGER"
                    can_trade = False
                    risk_score = max(risk_score, 90)
                    restrictions.append(f"No trading - high-impact news {event_name} in {minutes_until} min")
                elif impact == "HIGH" and -after_high <= minutes_until < 0:
                    market_status = "HIGH_VOLATILITY"
                    can_trade = False
                    risk_score = max(risk_score, 86)
                    restrictions.append(f"No trading - high-impact news {event_name} released {abs(minutes_until)} min ago")
                elif impact == "HIGH" and before_high < minutes_until <= warning_before:
                    if market_status == "SAFE":
                        market_status = "CAUTION"
                    risk_score = max(risk_score, 55)
                    warnings.append(f"Warning - high-impact news in {minutes_until} min")

                if impact == "MEDIUM" and -tier2_after <= minutes_until <= medium_window:
                    if market_status == "SAFE":
                        market_status = "CAUTION"
                    risk_score = max(risk_score, 60)
                    warnings.append(f"Caution - medium-impact news {event_name} nearby")

            high_risk_day = tier1_events_24h >= int(self.config.get("news_risk", {}).get("high_risk_day_tier1_count", 3))
            if high_risk_day:
                if market_status == "SAFE":
                    market_status = "CAUTION"
                risk_score = max(risk_score, 75)
                warnings.append(f"High Risk Day: {tier1_events_24h} Tier 1 events in 24h")

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
            macro_direction = MacroFundamentalAgent(self.config).macro_direction(self._macro_context())
            reason_codes = self._reason_codes(market_status, can_trade, upcoming, high_risk_day)
            reason_codes.extend(macro_direction.get("reason_codes", []) or [])
            evidence = [
                {"name": "market_status", "value": market_status, "bias": "RISK" if market_status != "SAFE" else "SAFE"},
                {"name": "tier1_events_24h", "value": tier1_events_24h, "bias": "RISK" if tier1_events_24h else "NEUTRAL"},
                {"name": "tier2_events_24h", "value": tier2_events_24h, "bias": "CAUTION" if tier2_events_24h else "NEUTRAL"},
                {"name": "risk_score", "value": min(100, int(risk_score)), "bias": "HIGH" if risk_score >= 75 else "MEDIUM" if risk_score >= 50 else "LOW"},
            ]
            invalidations = ["Trade only after active news restriction expires"] if not can_trade else []
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
                "tier_summary": {"tier1_24h": tier1_events_24h, "tier2_24h": tier2_events_24h, "high_risk_day": high_risk_day},
                "event_tiers": event_tiers,
                "risk_score": min(100, int(risk_score)),
                "event_risk": {"status": market_status, "can_trade": can_trade, "score": min(100, int(risk_score)), "tier_summary": {"tier1_24h": tier1_events_24h, "tier2_24h": tier2_events_24h, "high_risk_day": high_risk_day}},
                "macro_direction": macro_direction,
                "reason_codes": self._dedupe(reason_codes),
                "evidence": evidence + list(macro_direction.get("evidence", []) or [])[:5],
                "invalidations": invalidations,
                "data_quality": {"source": "news_events", "freshness": "OK", "missing_fields": [], "macro": macro_direction.get("data_quality", {})},
                "confidence_breakdown": {"event_risk": min(100, int(risk_score)), "macro_direction": macro_direction.get("confidence", 0), "penalties": -20 if not can_trade else 0},
                "warnings": warnings[:6],
                "summary": self._summary(market_status, can_trade, upcoming, active_restrictions),
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("News risk check failed")
            return {
                "agent": self.name,
                "market_status": "CAUTION",
                "can_trade": True,
                "upcoming_events": [],
                "active_restrictions": [f"News check failed: {exc}"],
                "session_info": {"current_session": get_current_session(), "volatility_expected": "UNKNOWN", "best_for_gold": False},
                "tier_summary": {"tier1_24h": 0, "tier2_24h": 0, "high_risk_day": False},
                "risk_score": 50,
                "event_risk": {"status": "CAUTION", "can_trade": True, "score": 50},
                "macro_direction": {"bias": "NEUTRAL", "confidence": 0, "summary": "Unavailable"},
                "reason_codes": ["NEWS_CHECK_FAILED"],
                "evidence": [],
                "invalidations": [],
                "data_quality": {"source": "news_events", "freshness": "UNKNOWN", "missing_fields": ["news_check"]},
                "confidence_breakdown": {"event_risk": 50, "macro_direction": 0, "penalties": -10},
                "warnings": [f"News check failed: {exc}"],
                "summary": "News check failed - proceed with caution",
            }

    def _macro_context(self) -> Dict[str, Any]:
        """Load operator-supplied macro context without mixing it with event risk."""
        for source in (self.config.get("macro_context"),):
            if isinstance(source, dict) and source:
                return dict(source)
        env_context = os.environ.get("MACRO_CONTEXT_JSON")
        if env_context:
            try:
                parsed = json.loads(env_context)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError as exc:
                self.logger.warning("Invalid MACRO_CONTEXT_JSON: %s", exc)
        macro_path = Path(__file__).resolve().parents[1] / "storage" / "macro_context.json"
        if macro_path.exists():
            try:
                with macro_path.open("r", encoding="utf-8") as file:
                    parsed = json.load(file)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError as exc:
                self.logger.warning("Invalid macro context file: %s", exc)
        return {}

    @staticmethod
    def _dedupe(items: List[str]) -> List[str]:
        seen: List[str] = []
        for item in items:
            if item and item not in seen:
                seen.append(item)
        return seen[:16]

    def _reason_codes(self, market_status: str, can_trade: bool, upcoming: List[Dict[str, Any]], high_risk_day: bool) -> List[str]:
        codes: List[str] = []
        status = str(market_status or "SAFE").upper()
        if not can_trade or status == "DANGER":
            codes.append("NEWS_HARD_BLOCK")
        elif status in {"CAUTION", "HIGH_VOLATILITY"}:
            codes.append(f"NEWS_{status}")
        else:
            codes.append("NEWS_SAFE")
        if high_risk_day:
            codes.append("NEWS_HIGH_RISK_DAY")
        if upcoming:
            tier = str(upcoming[0].get("tier") or "").upper()
            if tier:
                codes.append(f"NEWS_NEXT_{tier}")
        return codes[:8]

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
        # Auto ForexFactory feed (free, no key)
        try:
            from services.news_feed_forexfactory import fetch_forexfactory_events
            ff_events = fetch_forexfactory_events()
            if ff_events:
                events.extend(ff_events)
        except Exception:
            pass

        sanitized: List[Dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            clean = dict(event)
            # Event titles/forecast/previous may originate from a third-party
            # feed (ForexFactory) or operator-supplied NEWS_EVENTS_JSON. Both
            # eventually get embedded into downstream decision/status text, so
            # they are sanitized once here for every
            # source rather than relying on each downstream consumer to do it.
            for text_field in ("event", "name", "forecast", "previous"):
                if text_field in clean and clean[text_field]:
                    clean[text_field] = sanitize_prompt_text(clean[text_field], max_len=160)
            sanitized.append(clean)
        return sanitized


    def _classify_tier(self, event: Dict[str, Any], impact: str) -> str:
        """Classify event into institutional risk tiers."""
        explicit = str(event.get("tier", "")).upper().replace(" ", "_")
        if explicit in {"TIER_1", "TIER1"}:
            return "TIER_1"
        if explicit in {"TIER_2", "TIER2"}:
            return "TIER_2"
        if explicit in {"TIER_3", "TIER3"}:
            return "TIER_3"
        name = str(event.get("event", event.get("name", ""))).upper()
        if any(keyword in name for keyword in self.TIER1_KEYWORDS):
            return "TIER_1"
        if any(keyword in name for keyword in self.TIER2_KEYWORDS):
            return "TIER_2"
        if impact == "HIGH":
            return "TIER_2"
        if impact == "MEDIUM":
            return "TIER_2"
        return "TIER_3"

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

    def _enrich_event(self, event: Dict[str, Any], impact: str, minutes_until: int, tier: str = "TIER_3") -> Dict[str, Any]:
        return {
            "event": event.get("event", event.get("name", "Unknown Event")),
            "time": event.get("time"),
            "impact": impact,
            "tier": tier,
            "currency": str(event.get("currency", "USD")).upper(),
            "minutes_until": minutes_until,
            "expected": event.get("expected"),
            "previous": event.get("previous"),
            "special_handling": self._special_handling(event, tier),
        }

    def _special_handling(self, event: Dict[str, Any], tier: str) -> str:
        name = str(event.get("event", event.get("name", ""))).upper()
        if "NFP" in name or "NONFARM" in name:
            return "NFP_DAY_REDUCE_EXPOSURE"
        if "FOMC" in name or "RATE DECISION" in name or "INTEREST RATE" in name:
            return "CENTRAL_BANK_DECISION_PROTECTION"
        if "CPI" in name or "PCE" in name:
            return "INFLATION_RELEASE_PROTECTION"
        if tier == "TIER_1":
            return "MANDATORY_CAUTION"
        return "STANDARD"

    def _session_risk(self, now: datetime) -> Tuple[str, int, str | None]:
        session = get_current_session(now)
        if session == "Late NY / Rollover":
            return "CAUTION", 35, "Caution - rollover / low-liquidity period"
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
            return restrictions[0] if restrictions else "Market is risky right now - no trading"
        if market_status == "CAUTION":
            return restrictions[0] if restrictions else "Market needs caution due to news / trading session"
        high_events = [event for event in upcoming if event.get("impact") == "HIGH" and event.get("minutes_until", 9999) >= 0]
        if high_events:
            first = high_events[0]
            return f"Market is safe now; nearest high-impact news in {first.get('minutes_until')} min"
        return "Market is safe now; no high-impact news nearby"

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
