"""Telegram service for signal and update messages."""

from __future__ import annotations

import html
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

from utils.helpers import calculate_pips, canonical_session_label, format_price, load_config

class TelegramService:
    API_BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        c = self.config.get("telegram", {})
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or c.get("bot_token")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID") or c.get("chat_id")
        self.session = requests.Session()

    def send_message(self, text: str, urgent: bool = False) -> bool:
        if not self.bot_token or not self.chat_id: return False
        url = self.API_BASE.format(token=self.bot_token, method="sendMessage")
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            resp = self.session.post(url, json=payload, timeout=20)
            return resp.status_code == 200
        except Exception: return False

    def send_signal(self, decision: Dict[str, Any]) -> bool:
        symbol = str(decision.get("symbol", "XAU/USD"))
        signal = decision.get("signal", {})
        trade_type = str(decision.get("decision", "WAIT")).upper()
        
        # Header
        lines = [f"📊 <b>{symbol} SIGNAL — {trade_type}</b>", "━━━━━━━━━━━━━━━━━━━━━"]
        
        # Price and Context
        lines.append(f"📈 Price {format_price(decision.get('current_price'))} · Confidence {decision.get('confidence')}%")
        lines.append(f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append("──────────────────")
        
        # Trade Plan
        lines.append("🎯 <b>TRADE PLAN</b>")
        lines.append(f"• <b>Order:</b> {trade_type} Market")
        lines.append(f"• <b>Entry:</b> {format_price(signal.get('entry', {}).get('price'))}")
        lines.append(f"• <b>Stop Loss:</b> {format_price(signal.get('stop_loss'))}")
        lines.append(f"• <b>TP1:</b> {format_price(signal.get('tp1'))}")
        lines.append(f"• <b>TP2:</b> {format_price(signal.get('tp2'))}")
        lines.append("──────────────────")
        
        # Gemini Opinion
        gemini = decision.get("gemini_review", {})
        if gemini.get("available"):
            lines.append("🧠 <b>GEMINI INDEPENDENT REVIEW</b>")
            lines.append(f"• <b>Opinion:</b> {gemini.get('verdict')} - {gemini.get('reason')}")
            lines.append("──────────────────")

        # Why this trade
        lines.append("💡 <b>WHY THIS TRADE</b>")
        for r in (decision.get("reasons", []) or [])[:4]:
            lines.append(f"• {html.escape(str(r))}")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>ID: {decision.get('trade_id')}</i>")
        
        return self.send_message("\n".join(lines))
