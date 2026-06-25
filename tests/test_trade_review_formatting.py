"""Formatting guards for AI trade-review Telegram summaries."""

from __future__ import annotations

from services.trade_review import format_trade_review_summary


def test_trade_review_summary_escapes_groq_html_text() -> None:
    text = format_trade_review_summary(
        {
            "enabled": True,
            "reviewed": [
                {
                    "trade_id": "TRADE_<bad>&1",
                    "review": {
                        "failure_category": "ENTRY_<EARLY>",
                        "root_cause": "Price broke <support> & reversed",
                        "confidence_in_review": "80<90",
                        "rule_suggestions": ["Avoid <news> & low liquidity"],
                    },
                    "memory_rule_ids": ["MEM_1"],
                }
            ],
            "errors": [{"trade_id": "ERR_<id>", "error": "Bad <json> & parse"}],
        }
    )

    # Intended Telegram HTML tags remain, but untrusted values are escaped.
    assert "<b>AI Trade Review" in text
    assert "<code>TRADE_&lt;bad&gt;&amp;1</code>" in text
    assert "ENTRY_&lt;EARLY&gt;" in text
    assert "Price broke &lt;support&gt; &amp; reversed" in text
    assert "Avoid &lt;news&gt; &amp; low liquidity" in text
    assert "ERR_&lt;id&gt;: Bad &lt;json&gt; &amp; parse" in text
