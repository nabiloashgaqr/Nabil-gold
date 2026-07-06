"""Gold Macro / Fundamental Agent.

This agent keeps macro direction separate from event-risk blocking.  It does not
call paid APIs.  Operators can feed a compact macro snapshot through one of:

- MACRO_CONTEXT_JSON env var
- config["macro_context"]
- storage/macro_context.json

The output is intentionally structured so it can be saved in signal_snapshot and
used later for attribution/learning without changing orchestration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from agents.base_agent import BaseAgent
from utils.helpers import load_config, sanitize_prompt_text


class MacroFundamentalAgent(BaseAgent):
    """Directional macro read for XAU/USD.

    Positive score = bullish gold context. Negative score = bearish gold context.
    Event timing/blocking remains the responsibility of NewsRiskAgent.
    """

    name = "macro_fundamental"

    def __init__(self, config: Dict[str, Any] | None = None, **_kwargs: Any) -> None:
        super().__init__(config or load_config())
        self.context_path = Path(__file__).resolve().parents[1] / "storage" / "macro_context.json"

    def analyze(self, data: Dict[str, Any] | None = None) -> Dict[str, Any]:
        data = data or {}
        context = self._load_context(data)
        macro_direction = self.macro_direction(context)
        trade_bias = self._trade_bias(macro_direction.get("bias"), macro_direction.get("confidence", 0))
        signal = {"BULLISH_GOLD": "BUY", "BEARISH_GOLD": "SELL"}.get(str(macro_direction.get("bias")), "WAIT")
        if macro_direction.get("confidence", 0) < 55:
            signal = "WAIT"
        return {
            "agent": self.name,
            "signal": signal,
            "direction": signal if signal in {"BUY", "SELL"} else "NEUTRAL",
            "confidence": macro_direction.get("confidence", 0),
            "macro_direction": macro_direction,
            "trade_bias": trade_bias,
            "summary": macro_direction.get("summary", "Macro context unavailable"),
            "reason_codes": macro_direction.get("reason_codes", []),
            "evidence": macro_direction.get("evidence", []),
            "invalidations": macro_direction.get("invalidations", []),
            "data_quality": macro_direction.get("data_quality", {}),
            "confidence_breakdown": macro_direction.get("confidence_breakdown", {}),
            "warnings": macro_direction.get("warnings", []),
            "timestamp": self.now_iso(),
        }

    async def analyze_async(self, data: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.analyze(data or {})

    def macro_direction(self, context: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """Return directional macro bias for gold from a compact context dict."""
        context = context or {}
        if not context:
            return self._neutral("Macro directional inputs not connected yet", missing=["macro_context"])

        score = 0.0
        max_abs = 0.0
        drivers: List[str] = []
        evidence: List[Dict[str, Any]] = []
        codes: List[str] = []
        warnings: List[str] = []
        breakdown = {
            "dxy": 0.0,
            "yields": 0.0,
            "fed": 0.0,
            "inflation_growth": 0.0,
            "risk": 0.0,
            "commodity": 0.0,
        }

        def add(component: str, points: float, label: str, code: str, value: Any = None) -> None:
            nonlocal score, max_abs
            score += points
            max_abs += abs(points) if points != 0 else 0.3  # flat/neutral contribute breadth even at 0 points
            breakdown[component] = round(breakdown.get(component, 0.0) + points, 2)
            drivers.append(label)
            codes.append(code)
            evidence.append({"name": component, "value": value if value is not None else label, "bias": "BULLISH_GOLD" if points > 0 else "BEARISH_GOLD" if points < 0 else "NEUTRAL"})

        dxy = self._norm(context.get("dxy_trend") or context.get("dollar_trend") or context.get("usd_trend"))
        if dxy in {"UP", "RISING", "STRONG", "BULLISH"}:
            add("dxy", -2.2, "Stronger USD pressures gold", "MACRO_DXY_BEARISH_GOLD", dxy)
        elif dxy in {"DOWN", "FALLING", "WEAK", "BEARISH"}:
            add("dxy", 2.2, "Weaker USD supports gold", "MACRO_DXY_BULLISH_GOLD", dxy)
        elif dxy == "FLAT":
            add("dxy", 0.0, "DXY flat — no USD directional pressure", "MACRO_DXY_FLAT", dxy)

        yields = self._norm(context.get("us10y_trend") or context.get("real_yields_trend") or context.get("yields_trend"))
        if yields in {"UP", "RISING", "STRONG", "BULLISH"}:
            add("yields", -2.0, "Rising US yields pressure non-yielding gold", "MACRO_YIELDS_BEARISH_GOLD", yields)
        elif yields in {"DOWN", "FALLING", "WEAK", "BEARISH"}:
            add("yields", 2.0, "Falling US yields support gold", "MACRO_YIELDS_BULLISH_GOLD", yields)
        elif yields == "FLAT":
            add("yields", 0.0, "US yields flat — no yield pressure on gold", "MACRO_YIELDS_FLAT", yields)

        fed = self._norm(context.get("fed_tone") or context.get("fed_policy") or context.get("rate_expectations"))
        if fed in {"HAWKISH", "HIGHER_FOR_LONGER", "RATE_HIKES", "TIGHTENING"}:
            add("fed", -1.8, "Hawkish Fed tone is bearish for gold", "MACRO_FED_HAWKISH", fed)
        elif fed in {"DOVISH", "CUTS", "RATE_CUTS", "EASING"}:
            add("fed", 1.8, "Dovish Fed / cuts expectations support gold", "MACRO_FED_DOVISH", fed)
        elif fed == "NEUTRAL":
            add("fed", 0.0, "Fed tone neutral", "MACRO_FED_NEUTRAL", fed)

        inflation = self._norm(context.get("inflation_surprise") or context.get("cpi_surprise") or context.get("pce_surprise"))
        if inflation in {"HOT", "ABOVE", "HIGHER", "UPSIDE"}:
            add("inflation_growth", 0.8, "Hot inflation can support hedging demand, but may lift yields", "MACRO_INFLATION_HOT", inflation)
            warnings.append("Hot inflation has mixed impact if yields and USD rise together")
        elif inflation in {"COOL", "BELOW", "LOWER", "DOWNSIDE"}:
            add("inflation_growth", 0.7, "Cooling inflation can support gold through lower rate expectations", "MACRO_INFLATION_COOLING", inflation)

        growth = self._norm(context.get("growth_surprise") or context.get("recession_risk") or context.get("labor_market"))
        if growth in {"WEAK", "RECESSION", "RISK", "SOFTENING", "COOLING"}:
            add("inflation_growth", 1.3, "Growth weakness supports defensive gold demand", "MACRO_GROWTH_WEAK", growth)
        elif growth in {"STRONG", "HOT", "RESILIENT"}:
            add("inflation_growth", -0.9, "Strong growth can reduce defensive gold demand", "MACRO_GROWTH_STRONG", growth)

        risk = self._norm(context.get("risk_sentiment") or context.get("risk_regime") or context.get("geopolitical_risk"))
        if risk in {"RISK_OFF", "HIGH", "STRESS", "GEOPOLITICAL", "SAFE_HAVEN"}:
            add("risk", 2.0, "Risk-off / geopolitical stress supports safe-haven gold", "MACRO_RISK_OFF_BULLISH_GOLD", risk)
        elif risk in {"RISK_ON", "LOW", "CALM"}:
            add("risk", -0.9, "Risk-on sentiment reduces safe-haven demand", "MACRO_RISK_ON_BEARISH_GOLD", risk)
        elif risk == "NEUTRAL":
            add("risk", 0.0, "Risk sentiment neutral", "MACRO_RISK_NEUTRAL", risk)

        oil = self._norm(context.get("oil_trend") or context.get("commodity_inflation"))
        if oil in {"UP", "RISING", "STRONG"}:
            add("commodity", 0.5, "Higher oil can lift inflation-hedge demand", "MACRO_OIL_UP_SUPPORTS_GOLD", oil)
        elif oil in {"DOWN", "FALLING", "WEAK"}:
            add("commodity", -0.3, "Softer oil reduces inflation-hedge pressure", "MACRO_OIL_DOWN_SOFT_GOLD", oil)
        elif oil == "FLAT":
            add("commodity", 0.0, "Oil flat — no commodity pressure", "MACRO_OIL_FLAT", oil)

        # VIX level as an extra risk signal (from yfinance)
        vix = context.get("vix_level")
        if isinstance(vix, (int, float)):
            if vix >= 30:
                add("risk", 1.5, f"VIX at {vix:.0f} — extreme fear supports gold", "MACRO_VIX_EXTREME_FEAR", vix)
            elif vix >= 25:
                add("risk", 1.0, f"VIX at {vix:.0f} — elevated fear supports gold", "MACRO_VIX_ELEVATED", vix)
            elif vix <= 13:
                add("risk", -0.5, f"VIX at {vix:.0f} — extreme complacency", "MACRO_VIX_COMPLACENCY", vix)

        confidence = self._confidence(score, max_abs, len(evidence))
        if score >= 1.5 and confidence >= 45:
            bias = "BULLISH_GOLD"
        elif score <= -1.5 and confidence >= 45:
            bias = "BEARISH_GOLD"
        else:
            bias = "NEUTRAL"
            codes.append("MACRO_NEUTRAL_MIXED")

        summary = self._summary(bias, confidence, drivers)
        data_quality = {
            "source": self._source_label(context),
            "freshness": str(context.get("freshness") or context.get("data_quality") or "OPERATOR_SUPPLIED").upper(),
            "missing_fields": self._missing_fields(context),
            "inputs": len(evidence),
        }
        return {
            "bias": bias,
            "trade_bias": self._trade_bias(bias, confidence),
            "confidence": confidence,
            "score": round(score, 2),
            "drivers": drivers[:8],
            "summary": summary,
            "reason_codes": self._dedupe(codes)[:12],
            "evidence": evidence[:10],
            "invalidations": self._invalidations(bias),
            "data_quality": data_quality,
            "confidence_breakdown": {k: round(v, 2) for k, v in breakdown.items()},
            "warnings": warnings[:4],
        }

    def _load_context(self, data: Dict[str, Any]) -> Dict[str, Any]:
        for source in (data.get("macro_context"), self.config.get("macro_context")):
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
        if self.context_path.exists():
            try:
                with self.context_path.open("r", encoding="utf-8") as file:
                    parsed = json.load(file)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError as exc:
                self.logger.warning("Invalid macro context file: %s", exc)
        return {}

    def _neutral(self, summary: str, missing: List[str] | None = None) -> Dict[str, Any]:
        return {
            "bias": "NEUTRAL",
            "trade_bias": "WAIT",
            "confidence": 0,
            "score": 0.0,
            "drivers": [],
            "summary": summary,
            "reason_codes": ["MACRO_DATA_UNAVAILABLE"],
            "evidence": [],
            "invalidations": [],
            "data_quality": {"source": "none", "freshness": "UNKNOWN", "missing_fields": missing or ["macro_context"], "inputs": 0},
            "confidence_breakdown": {"dxy": 0, "yields": 0, "fed": 0, "inflation_growth": 0, "risk": 0, "commodity": 0},
            "warnings": [summary],
        }

    @staticmethod
    def _norm(value: Any) -> str:
        return str(value or "").strip().upper().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _dedupe(items: List[str]) -> List[str]:
        seen: List[str] = []
        for item in items:
            if item and item not in seen:
                seen.append(item)
        return seen

    @staticmethod
    def _confidence(score: float, max_abs: float, inputs: int) -> int:
        if inputs <= 0:
            return 0
        # Even flat/neutral inputs contribute information breadth
        # (data was collected, just no directional signal)
        if max_abs <= 0:
            # All inputs are flat/neutral — still valuable to report
            return int(round(max(0.0, min(30.0, 10 + inputs * 4))))
        conviction = min(abs(score) / max_abs, 1.0)
        breadth = min(inputs / 5.0, 1.0)
        return int(round(max(0.0, min(90.0, 35 + conviction * 35 + breadth * 20))))

    @staticmethod
    def _trade_bias(bias: Any, confidence: Any) -> str:
        try:
            conf = float(confidence or 0)
        except (TypeError, ValueError):
            conf = 0
        if conf < 45:
            return "WAIT"
        return {"BULLISH_GOLD": "BUY", "BEARISH_GOLD": "SELL"}.get(str(bias), "WAIT")

    @staticmethod
    def _invalidations(bias: str) -> List[str]:
        if bias == "BULLISH_GOLD":
            return ["USD and real yields turn higher together", "Risk-off premium fades quickly"]
        if bias == "BEARISH_GOLD":
            return ["USD weakens sharply", "Yields fall or safe-haven demand accelerates"]
        return ["Wait for clearer DXY / yields / Fed alignment"]

    @staticmethod
    def _missing_fields(context: Dict[str, Any]) -> List[str]:
        groups = {
            "dxy_trend": ("dxy_trend", "dollar_trend", "usd_trend"),
            "yields_trend": ("us10y_trend", "real_yields_trend", "yields_trend"),
            "fed_tone": ("fed_tone", "fed_policy", "rate_expectations"),
            "risk_sentiment": ("risk_sentiment", "risk_regime", "geopolitical_risk"),
            "oil_trend": ("oil_trend", "commodity_inflation"),
        }
        missing = []
        for label, keys in groups.items():
            val = None
            for k in keys:
                v = context.get(k)
                if v and str(v).lower() not in {"unknown", "none", ""}:
                    val = v
                    break
            if not val:
                missing.append(label)
        return missing

    @staticmethod
    def _source_label(context: Dict[str, Any]) -> str:
        source = context.get("source") or context.get("provider") or "operator_macro_context"
        return sanitize_prompt_text(str(source), max_len=80)

    @staticmethod
    def _summary(bias: str, confidence: int, drivers: List[str]) -> str:
        if not drivers:
            return "Macro context unavailable"
        lead = "; ".join(drivers[:3])
        if len(drivers) > 3:
            lead += f"; +{len(drivers) - 3} more"
        bias_label = bias.replace("_", " ").title()
        if bias == "NEUTRAL" and confidence > 0:
            return f"{bias_label} ({confidence}%) — no strong directional signal; {lead}"
        return f"{bias_label} ({confidence}%) — {lead}"
