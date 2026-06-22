# ✅ New Rule Applied (2026-06-22)

## Requested Behavior
- Every individual agent must have **≥ 60%** confidence to be considered at all.
- Only qualified agents (≥60%) are passed to Groq.
- **Groq** (final gate) only needs **≥ 51%** confidence **in the correct direction** (BUY or SELL).

## Changes Made

### 1. config.json
```json
"groq_observation_mode": {
  "enabled": true,
  "min_groq_confidence": 51,
  "agent_min_confidence": 60,
  ...
}
"risk_settings": { "min_confidence": 60 }
```

### 2. agents/decision_agent.py
- `__init__`: Added `self.agent_min_confidence = 60` and `self.groq_min_confidence = 51`
- `_collect_votes()`: Agents with `confidence < 60` are **completely skipped**.
- `_final_decision()`: 
  - Uses `groq_threshold = 51` for Groq approval.
  - Final check also uses the 51% threshold when Groq mode is active.
- Groq prompt updated to inform the model of the new rule.

### 3. scripts/run_analysis.py
- "No qualified signal" messages now clearly state:
  - "Agents required ≥60% (only agents ≥60% are considered)"
  - "Groq threshold: ≥51% (direction match only)"
- Decision payload includes `agent_min_confidence` and `groq_min_confidence`.

## Resulting Logic

1. All 5 agents run.
2. Only agents with **≥60%** are kept in the vote list.
3. Groq receives only qualified agents + full numeric context.
4. If Groq says **BUY** or **SELL** with **≥51%**, the signal is approved.
5. If Groq says WAIT or <51%, it becomes "No qualified signal" with clear explanation.

This keeps the "each agent gives its own confidence" principle while making Groq much easier to pass (51% instead of 60%).
