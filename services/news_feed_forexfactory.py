"""ForexFactory News Feed – مجاني بدون API Key.

يقرأ تقويم ForexFactory لهذا الأسبوع، ويعيد أحداث USD عالية/متوسطة التأثير.
يعمل كـ fallback تلقائي لـ NewsRiskAgent إذا لم توجد NEWS_EVENTS_JSON يدوية.

المصدر: https://nfs.faireconomy.media/ff_calendar_thisweek.xml
"""
from __future__ import annotations
import logging, time
from datetime import datetime, timezone
from typing import Any, Dict, List
import xml.etree.ElementTree as ET
import requests
logger = logging.getLogger(__name__)
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
_cache: Dict[str, Any] = {"ts": 0, "events": []}
CACHE_TTL = 1800
GOLD_RELEVANT = {"USD", "ALL"}
HIGH_KEYWORDS = {"NFP","NON-FARM","NONFARM","CPI","FOMC","GDP","INTEREST RATE","RATE DECISION","POWELL","PCE","FED FUNDS"}
MEDIUM_KEYWORDS = {"PMI","RETAIL SALES","UNEMPLOYMENT","JOBLESS","JOLTS","PPI","ADP","CONSUMER CONFIDENCE","DURABLE"}
def _impact_from_ff(impact: str, title: str) -> str:
    imp = impact.strip().upper(); t = title.upper()
    if imp in {"HIGH","HOLIDAY"} or any(k in t for k in HIGH_KEYWORDS): return "HIGH"
    if imp == "MEDIUM" or any(k in t for k in MEDIUM_KEYWORDS): return "MEDIUM"
    return "LOW"
def fetch_forexfactory_events(timeout: int = 12) -> List[Dict[str, Any]]:
    now = time.time()
    if _cache["events"] and now - _cache["ts"] < CACHE_TTL: return _cache["events"]
    try:
        r = requests.get(FF_URL, timeout=timeout, headers={"User-Agent":"Gold-AI-Signals/1.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        events: List[Dict[str, Any]] = []
        for ev in root.findall(".//event"):
            country = (ev.findtext("country") or "").strip().upper()
            if country not in GOLD_RELEVANT: continue
            title = (ev.findtext("title") or "").strip()
            date_str = (ev.findtext("date") or "").strip()
            time_str = (ev.findtext("time") or "").strip()
            impact_ff = (ev.findtext("impact") or "").strip()
            dt = None
            for fmt in ("%m-%d-%Y %I:%M%p", "%m-%d-%Y"):
                try:
                    if time_str and time_str.lower() not in ("all day","tentative",""):
                        dt = datetime.strptime(f"{date_str} {time_str}", fmt).replace(tzinfo=timezone.utc)
                    else:
                        dt = datetime.strptime(date_str, "%m-%d-%Y").replace(tzinfo=timezone.utc)
                    break
                except Exception: continue
            if not dt: continue
            impact = _impact_from_ff(impact_ff, title)
            events.append({"time": dt.isoformat().replace("+00:00","Z"), "event": title, "currency": country, "impact": impact, "forecast": ev.findtext("forecast") or "", "previous": ev.findtext("previous") or "", "source": "forexfactory"})
        _cache["ts"] = now; _cache["events"] = events
        logger.info("ForexFactory: loaded %d USD events", len(events))
        return events
    except Exception as exc:
        logger.warning("ForexFactory fetch failed: %s", exc)
        return _cache.get("events", [])
