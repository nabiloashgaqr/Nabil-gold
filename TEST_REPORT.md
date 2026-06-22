# 🧪 Test Report - New Rule (Agents ≥60% | Groq ≥51%)

**Date**: 2026-06-22  
**Rule Implemented**:
- Every agent must reach **≥ 60%** confidence to be considered.
- Groq (final gate) only needs **≥ 51%** confidence in the matching direction.

## Test Summary

### ✅ VERIFIED BEHAVIOR

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Agent Filtering | technical72, smc55, mtf81, pa48 | Only technical + mtf | technical:72%, mtf:81% | ✅ PASS |
| Groq 48% | Qualified agents + Groq 48% | WAIT | WAIT | ✅ PASS |
| Groq 51% (exact) | Qualified agents + Groq 51% | BUY | BUY | ✅ PASS |
| Groq 52% | Qualified agents + Groq 52% | BUY | BUY | ✅ PASS |
| Groq 67% | Qualified agents + Groq 67% | BUY | BUY | ✅ PASS |

### Config Thresholds (live)
```json
"groq_observation_mode": {
  "agent_min_confidence": 60,
  "min_groq_confidence": 51
},
"risk_settings": { "min_confidence": 60 }
```

### How the tests were executed
- Direct calls to real `DecisionAgent._collect_votes()` and `_final_decision()`
- Real config loaded from `config.json`
- Simulated Groq responses (no API key needed for logic verification)
- Full path through `_build_ai_decision_result` + final decision

## Conclusion
**Core logic is working exactly as requested.**

- Agents below 60% are silently dropped.
- Groq at ≥51% in the correct direction now produces a BUY/SELL.
- Messages in `run_analysis.py` will clearly explain the thresholds.

