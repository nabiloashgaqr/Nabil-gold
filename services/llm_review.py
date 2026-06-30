"""Gemini-based review helpers for signals and learning.

Phase 1 design goals:
- Optional and fail-safe: if Gemini is not configured or quota is hit, core bot logic continues.
- Reviewer only: does NOT replace DecisionAgent or LearningService.
- Keep prompts compact and sanitized so free-tier request budgets remain practical.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

import requests

from utils.helpers import sanitize_prompt_text

logger = logging.getLogger(__name__)


class GeminiReviewService:
    """Small wrapper around Gemini Flash for review/explanation tasks."""

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.api_key = os.environ.get("GEMINI_API_KEY") or ""
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.enabled = bool(self.api_key)
        self.timeout = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "20") or 20)
        self.session = requests.Session()

    def review_signal(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Review a candidate signal and return a short structured summary.

        Expected output keys:
        - verdict: APPROVE / CAUTION / REJECT / UNAVAILABLE
        - confidence_note: short text
        - strengths: list[str]
        - risks: list[str]
        - summary: short paragraph
        """
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_signal_payload(payload)
        prompt = (
            "You are reviewing a trading signal produced by a rule-based multi-agent system. "
            "Do not invent prices or indicators. Use only the provided data. "
            "Return STRICT JSON with keys verdict, confidence_note, strengths, risks, summary. "
            "verdict must be one of APPROVE, CAUTION, REJECT. strengths and risks must be short arrays of strings. "
            "summary must be concise and factual.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def summarize_learning(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Summarize learning / post-trade insights in a short structured form."""
        if not self.enabled:
            return self._unavailable("Gemini API key not configured")

        compact = self._compact_learning_payload(payload)
        prompt = (
            "You are reviewing trading performance and agent-learning data. "
            "Return STRICT JSON with keys lessons, warnings, summary. "
            "lessons and warnings must be short arrays of strings. summary must be concise. "
            "Do not mention missing information unless critical.\n\n"
            f"DATA:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return self._generate_json(prompt)

    def _generate_json(self, prompt: str) -> Dict[str, Any]:
        try:
            url = self.API_URL.format(model=self.model)
            response = self.session.post(
                url,
                params={"key": self.api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            text = self._extract_text(data)
            if not text:
                return self._unavailable("Gemini returned empty content")
            parsed = json.loads(text)
            parsed["available"] = True
            parsed["model"] = self.model
            return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini review unavailable: %s", exc)
            return self._unavailable(str(exc))

    def _extract_text(self, data: Dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        for candidate in candidates:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                text = part.get("text")
                if text:
                    return str(text)
        return ""

    def _compact_signal_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        decision = payload.get("decision", {}) or {}
        signal = decision.get("signal", {}) or {}
        classic = decision.get("classic", {}) or {}
        risk = decision.get("risk", {}) or {}
        daily_bias = decision.get("daily_bias", {}) or {}
        return {
            "symbol": payload.get("symbol") or decision.get("symbol"),
            "decision": decision.get("decision"),
            "confidence": decision.get("confidence"),
            "summary": sanitize_prompt_text(decision.get("summary") or decision.get("reasoning") or "", 500),
            "supportive_evidence": [sanitize_prompt_text(x, 140) for x in (decision.get("supportive_evidence") or [])[:5]],
            "warnings": [sanitize_prompt_text(x, 140) for x in (decision.get("warnings") or [])[:5]],
            "agent_context": classic.get("strongest_directional") or decision.get("agent_context"),
            "counts": {
                "buy": classic.get("buy_count"),
                "sell": classic.get("sell_count"),
                "total_voting_agents": classic.get("total_voting_agents"),
            },
            "signal": {
                "entry": (signal.get("entry", {}) or {}).get("price"),
                "stop_loss": signal.get("stop_loss"),
                "tp1": signal.get("tp1"),
                "tp2": signal.get("tp2"),
                "rr_ratio": signal.get("rr_ratio"),
            },
            "risk_summary": sanitize_prompt_text(risk.get("summary") or signal.get("risk_summary") or "", 300),
            "daily_bias": {
                "bias": daily_bias.get("bias"),
                "confidence": daily_bias.get("confidence"),
            },
        }

    def _compact_learning_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "report_date": payload.get("report_date"),
            "overall_win_rate": payload.get("overall_win_rate"),
            "total_trades_analyzed": payload.get("total_trades_analyzed"),
            "changes_summary": sanitize_prompt_text(payload.get("changes_summary") or "", 400),
            "top_performers": list(payload.get("top_performers") or [])[:5],
            "bottom_performers": list(payload.get("bottom_performers") or [])[:5],
            "recommendations": [sanitize_prompt_text(x, 160) for x in (payload.get("recommendations") or [])[:8]],
        }

    def _unavailable(self, reason: str) -> Dict[str, Any]:
        return {
            "available": False,
            "verdict": "UNAVAILABLE",
            "confidence_note": "Gemini review unavailable",
            "strengths": [],
            "risks": [],
            "lessons": [],
            "warnings": [],
            "summary": sanitize_prompt_text(reason, 240),
        }


def get_gemini_review_service(config: Dict[str, Any] | None = None) -> GeminiReviewService:
    return GeminiReviewService(config)
