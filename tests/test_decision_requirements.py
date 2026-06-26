"""
🔬 اختبارات متطلبات الإشارة – One-Agent + external model Observation Mode
- وكيل واحد اتجاهي كافٍ
- external model هو البوابة النهائية
"""

import pytest
from agents.decision_agent import DecisionAgent


@pytest.fixture
def mock_config():
    return {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {
            "min_agents_agree": 1,
            "min_agreement_percentage": 1,
            "allow_all_signals": True,
            "description": "One-Agent + external model mode"
        },
        "agent_weights": {
            "technical": 0.20, "classical": 0.20, "smc": 0.25,
            "price_action": 0.15, "multitimeframe": 0.20
        },
        "external_model_observation_mode": {"enabled": True, "allow_single_agent_context": True}
    }


@pytest.fixture
def agent(mock_config):
    return DecisionAgent(mock_config)


def test_min_agents_requirement():
    """اختبار: وكيل واحد كافٍ في وضع Observation"""
    config = {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {"min_agents_agree": 1, "min_agreement_percentage": 1},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}
    }
    agent = DecisionAgent(config)
    votes = {
        'BUY': [
            {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
        ],
        'SELL': [], 'WAIT': []
    }
    result = agent._classic_decision(votes)
    # One-Agent mode: 1 agent is enough
    assert result['decision'] == 'BUY'
    assert result['buy_count'] == 1


def test_3_agents_agree_buy():
    config = {"risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5}, "signal_requirements": {"min_agents_agree": 1, "min_agreement_percentage": 1}, "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}}
    agent = DecisionAgent(config)
    votes = {'BUY': [
            {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'classical', 'confidence': 75, 'weight': 0.2, 'score': 0.15, 'adjusted_confidence': 75},
            {'agent': 'smc', 'confidence': 85, 'weight': 0.25, 'score': 0.21, 'adjusted_confidence': 85},
        ], 'SELL': [], 'WAIT': []}
    result = agent._classic_decision(votes)
    assert result['decision'] == 'BUY'
    assert result['buy_count'] == 3
    assert result['buy_agreement_pct'] == 100.0


def test_3_agents_agree_sell():
    config = {"risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5}, "signal_requirements": {"min_agents_agree": 1, "min_agreement_percentage": 1}, "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}}
    agent = DecisionAgent(config)
    votes = {'BUY': [], 'SELL': [
            {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'price_action', 'confidence': 75, 'weight': 0.15, 'score': 0.11, 'adjusted_confidence': 75},
            {'agent': 'multitimeframe', 'confidence': 85, 'weight': 0.2, 'score': 0.17, 'adjusted_confidence': 85},
        ], 'WAIT': []}
    result = agent._classic_decision(votes)
    assert result['decision'] == 'SELL'
    assert result['sell_count'] == 3


def test_60_percent_agreement():
    config = {"risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5}, "signal_requirements": {"min_agents_agree": 1, "min_agreement_percentage": 1}, "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}}
    agent = DecisionAgent(config)
    votes = {'BUY': [
            {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'classical', 'confidence': 75, 'weight': 0.2, 'score': 0.15, 'adjusted_confidence': 75},
            {'agent': 'smc', 'confidence': 85, 'weight': 0.25, 'score': 0.21, 'adjusted_confidence': 85},
        ], 'SELL': [
            {'agent': 'price_action', 'confidence': 70, 'weight': 0.15, 'score': 0.10, 'adjusted_confidence': 70},
            {'agent': 'multitimeframe', 'confidence': 65, 'weight': 0.2, 'score': 0.13, 'adjusted_confidence': 65},
        ], 'WAIT': []}
    result = agent._classic_decision(votes)
    assert result['buy_agreement_pct'] == 60.0
    assert result['decision'] == 'BUY'


def test_below_60_percent_rejected():
    """في وضع One-Agent: حتى 50% مسموح، القرار يعتمد على الوزن"""
    config = {"risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5}, "signal_requirements": {"min_agents_agree": 1, "min_agreement_percentage": 1}, "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}}
    agent = DecisionAgent(config)
    votes = {'BUY': [
            {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'classical', 'confidence': 75, 'weight': 0.2, 'score': 0.15, 'adjusted_confidence': 75},
            {'agent': 'smc', 'confidence': 85, 'weight': 0.25, 'score': 0.21, 'adjusted_confidence': 85},
        ], 'SELL': [
            {'agent': 'price_action', 'confidence': 70, 'weight': 0.15, 'score': 0.10, 'adjusted_confidence': 70},
            {'agent': 'multitimeframe', 'confidence': 65, 'weight': 0.2, 'score': 0.13, 'adjusted_confidence': 65},
            {'agent': 'news_risk', 'confidence': 60, 'weight': 0.15, 'score': 0.09, 'adjusted_confidence': 60},
        ], 'WAIT': []}
    result = agent._classic_decision(votes)
    assert result['buy_agreement_pct'] == 50.0
    # One-Agent mode allows it, BUY wins by score
    assert result['decision'] == 'BUY'


def test_no_max_trades_limit():
    config = {"risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5}, "signal_requirements": {"min_agents_agree": 1, "min_agreement_percentage": 1, "allow_all_signals": True}, "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}}
    agent = DecisionAgent(config)
    assert hasattr(agent, 'allow_all_signals')
    assert agent.allow_all_signals == True


def test_5_agents_strong_buy():
    config = {"risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5}, "signal_requirements": {"min_agents_agree": 1, "min_agreement_percentage": 1}, "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}}
    agent = DecisionAgent(config)
    votes = {'BUY': [
            {'agent': 'technical', 'confidence': 85, 'weight': 0.2, 'score': 0.17, 'adjusted_confidence': 85},
            {'agent': 'classical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'smc', 'confidence': 88, 'weight': 0.25, 'score': 0.22, 'adjusted_confidence': 88},
            {'agent': 'price_action', 'confidence': 75, 'weight': 0.15, 'score': 0.11, 'adjusted_confidence': 75},
            {'agent': 'multitimeframe', 'confidence': 82, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 82},
        ], 'SELL': [], 'WAIT': []}
    result = agent._classic_decision(votes)
    assert result['decision'] == 'BUY'
    assert result['buy_count'] == 5
    assert result['buy_agreement_pct'] == 100.0
    assert result['confidence'] > 80


def test_conflict_buy_vs_sell():
    config = {"risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5}, "signal_requirements": {"min_agents_agree": 1, "min_agreement_percentage": 1}, "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}}
    agent = DecisionAgent(config)
    votes = {'BUY': [
            {'agent': 'technical', 'confidence': 70, 'weight': 0.2, 'score': 0.14, 'adjusted_confidence': 70},
            {'agent': 'classical', 'confidence': 65, 'weight': 0.2, 'score': 0.13, 'adjusted_confidence': 65},
            {'agent': 'smc', 'confidence': 72, 'weight': 0.25, 'score': 0.18, 'adjusted_confidence': 72},
        ], 'SELL': [
            {'agent': 'price_action', 'confidence': 90, 'weight': 0.15, 'score': 0.13, 'adjusted_confidence': 90},
            {'agent': 'multitimeframe', 'confidence': 85, 'weight': 0.2, 'score': 0.17, 'adjusted_confidence': 85},
        ], 'WAIT': []}
    result = agent._classic_decision(votes)
    # Opposing SELL agents now subtract from BUY confidence/edge; this conflict
    # is too weak for a valid classic consensus entry.
    assert result['decision'] == 'WAIT'
    assert result['consensus']['BUY']['opposition_penalty'] > 0


def test_all_conditions_met():
    config = {"risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5}, "signal_requirements": {"min_agents_agree": 1, "min_agreement_percentage": 1}, "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}}
    agent = DecisionAgent(config)
    votes = {'BUY': [
            {'agent': 'technical', 'confidence': 85, 'weight': 0.2, 'score': 0.17, 'adjusted_confidence': 85},
            {'agent': 'classical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'smc', 'confidence': 88, 'weight': 0.25, 'score': 0.22, 'adjusted_confidence': 88},
            {'agent': 'multitimeframe', 'confidence': 82, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 82},
        ], 'SELL': [
            {'agent': 'price_action', 'confidence': 70, 'weight': 0.15, 'score': 0.10, 'adjusted_confidence': 70},
        ], 'WAIT': []}
    result = agent._classic_decision(votes)
    assert result['decision'] == 'BUY'
    assert result['buy_count'] == 4
    assert result['buy_agreement_pct'] == 80.0
    assert result['total_voting_agents'] == 5
    assert result['rejection_reason'] is None


def test_signal_message_format(agent):
    result = {
        'signal': 'BUY', 'confidence': 85, 'reasoning': '1+ agents agreed',
        'votes': {'BUY': [
                {'agent': 'technical', 'confidence': 85, 'weight': 0.2, 'score': 0.17, 'adjusted_confidence': 85},
                {'agent': 'classical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
                {'agent': 'smc', 'confidence': 88, 'weight': 0.25, 'score': 0.22, 'adjusted_confidence': 88},
            ], 'SELL': [], 'WAIT': []},
        'classic': {'buy_count': 3, 'sell_count': 0, 'buy_agreement_pct': 100.0, 'sell_agreement_pct': 0, 'total_voting_agents': 3, 'decision': 'BUY', 'confidence': 85, 'rejection_reason': None},
        'ai': {'available': True, 'provider': 'external_model', 'consensus_strength': 'Strong'},
        'learning': {'enabled': True, 'overall_win_rate': 65.5},
        'risk_assessment': {'score': 0, 'assessment': 'Acceptable ✅', 'factors': []},
        'weights': {'technical': 0.20, 'classical': 0.20, 'smc': 0.25, 'price_action': 0.15, 'multitimeframe': 0.20}
    }
    message = agent.get_decision_message(result)
    assert '🔥 Agreement requirements:' in message
    assert 'BUY' in message


def test_wait_signal_message(agent):
    result = {
        'signal': 'WAIT', 'confidence': 50, 'reasoning': 'No agents agreed',
        'votes': {'BUY': [], 'SELL': [], 'WAIT': [
                {'agent': 'technical', 'confidence': 40, 'weight': 0.2, 'score': 0.08, 'adjusted_confidence': 40},
            ]},
        'classic': {'buy_count': 0, 'sell_count': 0, 'buy_agreement_pct': 0, 'sell_agreement_pct': 0, 'total_voting_agents': 0, 'decision': 'WAIT', 'confidence': 50, 'rejection_reason': 'Not enough agents (0/1)'},
        'ai': {'available': False}, 'learning': {'enabled': True},
        'risk_assessment': {'score': 1, 'assessment': 'Moderate ⚠️', 'factors': ['RSI in extreme zone']},
        'weights': {}
    }
    message = agent.get_decision_message(result)
    assert '❌ Wait reason:' in message
    assert 'Not enough agents' in message
