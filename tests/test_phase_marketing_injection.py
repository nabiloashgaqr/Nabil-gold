from pathlib import Path


def test_pricing_page_injects_phase_1_to_6_marketing_layers():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")

    assert "Six practical layers behind every alert" in html
    assert "ست طبقات عملية خلف كل تنبيه" in html
    assert "Multi-agent analysis" in html
    assert "Independent AI review" in html
    assert "Risk guardrails" in html
    assert "Live management" in html
    assert "Performance quality" in html
    assert "Executive reports" in html


def test_pricing_page_promotes_new_edge_metrics_without_technical_secrets():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")

    assert "RR Capture" in html
    assert "Session Edge" in html
    assert "News Impact" in html
    assert "Market Fit" in html
    assert "Independent Review" in html
    assert "API key" not in html
    assert "GitHub Secrets" not in html
    assert "Supabase schema" not in html
