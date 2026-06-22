"""Compare Groq models on the same decision context.

Runs the full local agent stack once, then asks two Groq models for the final
DecisionAgent output using identical data. It does not save trades.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.classical_agent import ClassicalAgent
from agents.daily_bias_agent import DailyBiasAgent
from agents.decision_agent import DecisionAgent
from agents.multitimeframe_agent import MultiTimeframeAgent
from agents.news_risk_agent import NewsRiskAgent
from agents.price_action_agent import PriceActionAgent
from agents.risk_management_agent import RiskManagementAgent
from agents.smc_agent import SMCAgent
from agents.technical_agent import TechnicalAgent
from services.ai_service import AIService
from services.database import DatabaseService
from services.dynamic_risk import DynamicRiskManager
from services.market_data import MarketDataService
from services.telegram_bot import TelegramService
from utils.helpers import load_config, setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Groq models on same decision context")
    parser.add_argument("--model-a", default="llama-3.1-8b-instant")
    parser.add_argument("--model-b", default="llama-3.3-70b-versatile")
    parser.add_argument("--output", default="storage/groq_model_comparison.json")
    parser.add_argument("--send-telegram", action="store_true", default=False)
    return parser.parse_args()


def run_agent(agent_name: str, agent: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return agent.analyze(data)
    except Exception as exc:  # noqa: BLE001
        return {"agent": agent_name, "signal": "WAIT", "direction": "NEUTRAL", "confidence": 0, "error": str(exc)}


def build_context(config: Dict[str, Any]) -> Dict[str, Any]:
    market = MarketDataService(config)
    data = market.get_gold_data()
    if not data:
        raise RuntimeError("Could not fetch market data")
    db = DatabaseService(config)
    context: Dict[str, Any] = {
        "technical": run_agent("technical", TechnicalAgent(config, ai_service=None), data),
        "classical": run_agent("classical", ClassicalAgent(config, ai_service=None), data),
        "smc": run_agent("smc", SMCAgent(config, ai_service=None), data),
        "price_action": run_agent("price_action", PriceActionAgent(config, ai_service=None), data),
        "multitimeframe": run_agent("multitimeframe", MultiTimeframeAgent(config, ai_service=None), data),
        "daily_bias": run_agent("daily_bias", DailyBiasAgent(config), data),
        "session": {"trading_allowed": True, "allow_signals": True, "session_quality": "HIGH", "current_session": "ModelCompare"},
        "news": NewsRiskAgent(config).check(),
        "current_price": data.get("current_price"),
        "spread_points": data.get("spread_points"),
        "portfolio": {
            "open_trades_count": len(db.get_open_trades()),
            "today_signals_count": db.get_today_signals_count(),
            "consecutive_losses": db.get_consecutive_losses(),
        },
        "memory_rules": db.get_active_memory_rules(limit=int(config.get("ai_memory_rules", {}).get("max_active_rules_in_prompt", 8))),
    }
    context["risk"] = RiskManagementAgent(config).evaluate(context)
    context["dynamic_risk"] = DynamicRiskManager(config).evaluate(db)
    return context


async def run_model(config: Dict[str, Any], model: str, context: Dict[str, Any]) -> Dict[str, Any]:
    cfg = copy.deepcopy(config)
    cfg.setdefault("ai_service", {})["enabled"] = True
    cfg["ai_service"]["provider"] = "groq"
    cfg["ai_service"]["model"] = model
    cfg["ai_service"]["api_key"] = "ENV:GROQ_API_KEY"
    cfg["ai_service"]["fallback_to_classic"] = False
    ai = AIService(cfg)
    decision_agent = DecisionAgent(cfg, ai_service=ai)
    analysis = await decision_agent.analyze_async(context)
    payload = decision_agent._to_trade_decision(analysis, context)
    return {
        "model": model,
        "analysis": analysis,
        "decision_payload": payload,
        "ai": payload.get("ai", {}),
        "decision": payload.get("decision"),
        "confidence": payload.get("confidence"),
        "summary": payload.get("summary"),
    }


def format_telegram(report: Dict[str, Any]) -> str:
    a = report["models"][0]
    b = report["models"][1]
    def line(item: Dict[str, Any]) -> str:
        ai = item.get("ai", {}) or {}
        warn = ai.get("ai_warnings") or []
        return (
            f"• {item['model']}: {item.get('decision')} | conf {item.get('confidence')}% | "
            f"tokens {ai.get('tokens_used', 'N/A')} | warnings {len(warn)}"
        )
    return "\n".join([
        "🧪 <b>Groq Model Comparison</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Price: {report.get('current_price')}",
        line(a),
        line(b),
        "",
        f"Agreement: {'✅' if a.get('decision') == b.get('decision') else '⚠️ different'}",
        "JSON saved as a GitHub Actions artifact.",
        "━━━━━━━━━━━━━━━━━━━━",
    ])


async def main_async() -> None:
    setup_logging()
    args = parse_args()
    config = load_config()
    context = build_context(config)
    results = []
    for model in [args.model_a, args.model_b]:
        results.append(await run_model(config, model, context))
    report = {
        "current_price": context.get("current_price"),
        "context_summary": {
            "technical": {"signal": context.get("technical", {}).get("signal"), "confidence": context.get("technical", {}).get("confidence")},
            "classical": {"direction": context.get("classical", {}).get("direction"), "confidence": context.get("classical", {}).get("confidence")},
            "smc": {"direction": context.get("smc", {}).get("direction"), "confidence": context.get("smc", {}).get("confidence")},
            "price_action": {"direction": context.get("price_action", {}).get("direction"), "confidence": context.get("price_action", {}).get("confidence")},
            "multitimeframe": {"direction": context.get("multitimeframe", {}).get("direction"), "confidence": context.get("multitimeframe", {}).get("confidence")},
            "risk": {"approved": context.get("risk", {}).get("approved"), "grade": context.get("risk", {}).get("trade_grade")},
        },
        "models": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(format_telegram(report).replace("<b>", "").replace("</b>", ""))
    print(f"Report saved to: {out}")
    if args.send_telegram or os.environ.get("GITHUB_ACTIONS") == "true":
        TelegramService(config).send_message(format_telegram(report))


if __name__ == "__main__":
    asyncio.run(main_async())
