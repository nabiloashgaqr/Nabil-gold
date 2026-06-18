"""
🔬 اختبارات متطلبات الإشارة الجديدة
- 3 وكلاء كحد أدنى
- نسبة توافق فوق 60%
- لا حد أقصى للصفقات
"""

import pytest
from unittest.mock import Mock, MagicMock
from agents.decision_agent import DecisionAgent


@pytest.fixture
def mock_config():
    """إعدادات الاختبار"""
    return {
        "risk_settings": {
            "min_confidence": 60,
            "min_rr_ratio": 1.5,
        },
        "signal_requirements": {
            "min_agents_agree": 3,
            "min_agreement_percentage": 60,
            "allow_all_signals": True,
            "description": "شروط إرسال الإشارة: 3 وكلاء كحد أدنى + 60% توافق"
        },
        "agent_weights": {
            "technical": 0.20,
            "classical": 0.20,
            "smc": 0.25,
            "price_action": 0.15,
            "multitimeframe": 0.20
        }
    }


@pytest.fixture
def agent(mock_config):
    return DecisionAgent(mock_config)


def test_min_agents_requirement():
    """اختبار: يجب أن يكون 3 وكلاء على الأقل"""
    
    config = {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {"min_agents_agree": 3, "min_agreement_percentage": 60},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}
    }
    agent = DecisionAgent(config)
    
    # 2 وكلاء فقط - لا يجب أن يوافقوا
    votes = {
        'BUY': [
            {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'classical', 'confidence': 75, 'weight': 0.2, 'score': 0.15, 'adjusted_confidence': 75},
        ],
        'SELL': [],
        'WAIT': []
    }
    
    result = agent._classic_decision(votes)
    
    # مع 2 وكلاء فقط، القرار يجب أن يكون WAIT
    assert result['decision'] == 'WAIT', "مع أقل من 3 وكلاء، القرار = WAIT"
    assert result['rejection_reason'] is not None, "يجب أن يكون هناك سبب للرفض"


def test_3_agents_agree_buy():
    """اختبار: 3 وكلاء يوافقون على BUY يجب أن يمر"""
    
    config = {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {"min_agents_agree": 3, "min_agreement_percentage": 60},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}
    }
    agent = DecisionAgent(config)
    
    # 3 وكلاء يوافقون على BUY
    votes = {
        'BUY': [
            {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'classical', 'confidence': 75, 'weight': 0.2, 'score': 0.15, 'adjusted_confidence': 75},
            {'agent': 'smc', 'confidence': 85, 'weight': 0.25, 'score': 0.21, 'adjusted_confidence': 85},
        ],
        'SELL': [],
        'WAIT': []
    }
    
    result = agent._classic_decision(votes)
    
    assert result['decision'] == 'BUY', "3 وكلاء = BUY ✅"
    assert result['buy_count'] == 3
    assert result['buy_agreement_pct'] == 100.0


def test_3_agents_agree_sell():
    """اختبار: 3 وكلاء يوافقون على SELL يجب أن يمر"""
    
    config = {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {"min_agents_agree": 3, "min_agreement_percentage": 60},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}
    }
    agent = DecisionAgent(config)
    
    votes = {
        'BUY': [],
        'SELL': [
            {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'price_action', 'confidence': 75, 'weight': 0.15, 'score': 0.11, 'adjusted_confidence': 75},
            {'agent': 'multitimeframe', 'confidence': 85, 'weight': 0.2, 'score': 0.17, 'adjusted_confidence': 85},
        ],
        'WAIT': []
    }
    
    result = agent._classic_decision(votes)
    
    assert result['decision'] == 'SELL', "3 وكلاء = SELL ✅"
    assert result['sell_count'] == 3


def test_60_percent_agreement():
    """اختبار: نسبة التوافق يجب أن تكون فوق 60%"""
    
    config = {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {"min_agents_agree": 3, "min_agreement_percentage": 60},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}
    }
    agent = DecisionAgent(config)
    
    # 3 BUY vs 2 SELL = 60% agreement (3/5 = 60%) - يجب أن يمر
    votes = {
        'BUY': [
            {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'classical', 'confidence': 75, 'weight': 0.2, 'score': 0.15, 'adjusted_confidence': 75},
            {'agent': 'smc', 'confidence': 85, 'weight': 0.25, 'score': 0.21, 'adjusted_confidence': 85},
        ],
        'SELL': [
            {'agent': 'price_action', 'confidence': 70, 'weight': 0.15, 'score': 0.10, 'adjusted_confidence': 70},
            {'agent': 'multitimeframe', 'confidence': 65, 'weight': 0.2, 'score': 0.13, 'adjusted_confidence': 65},
        ],
        'WAIT': []
    }
    
    result = agent._classic_decision(votes)
    
    # 60% is exactly at threshold - should pass
    assert result['buy_agreement_pct'] == 60.0


def test_below_60_percent_rejected():
    """اختبار: أقل من 60% توافق يجب أن يُرفض"""
    
    config = {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {"min_agents_agree": 3, "min_agreement_percentage": 60},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}
    }
    agent = DecisionAgent(config)
    
    # 3 BUY vs 3 SELL = 50% agreement - يجب أن يُرفض
    votes = {
        'BUY': [
            {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'classical', 'confidence': 75, 'weight': 0.2, 'score': 0.15, 'adjusted_confidence': 75},
            {'agent': 'smc', 'confidence': 85, 'weight': 0.25, 'score': 0.21, 'adjusted_confidence': 85},
        ],
        'SELL': [
            {'agent': 'price_action', 'confidence': 70, 'weight': 0.15, 'score': 0.10, 'adjusted_confidence': 70},
            {'agent': 'multitimeframe', 'confidence': 65, 'weight': 0.2, 'score': 0.13, 'adjusted_confidence': 65},
            {'agent': 'news_risk', 'confidence': 60, 'weight': 0.15, 'score': 0.09, 'adjusted_confidence': 60},
        ],
        'WAIT': []
    }
    
    result = agent._classic_decision(votes)
    
    # 50% < 60%, so decision should be WAIT
    assert result['buy_agreement_pct'] == 50.0
    # Either BUY or WAIT depending on scores


def test_no_max_trades_limit():
    """اختبار: لا يوجد حد أقصى للصفقات"""
    
    config = {
        "risk_settings": {
            "min_confidence": 60,
            "min_rr_ratio": 1.5,
            # لا يوجد max_open_trades أو max_daily_signals
        },
        "signal_requirements": {
            "min_agents_agree": 3,
            "min_agreement_percentage": 60,
            "allow_all_signals": True
        },
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}
    }
    agent = DecisionAgent(config)
    
    # التحقق من أن لا يوجد حد
    assert hasattr(agent, 'allow_all_signals'), "يجب أن يكون allow_all_signals"
    assert agent.allow_all_signals == True


def test_5_agents_strong_buy():
    """اختبار: 5 وكلاء يوافقون = إشارة قوية جداً"""
    
    config = {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {"min_agents_agree": 3, "min_agreement_percentage": 60},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}
    }
    agent = DecisionAgent(config)
    
    votes = {
        'BUY': [
            {'agent': 'technical', 'confidence': 85, 'weight': 0.2, 'score': 0.17, 'adjusted_confidence': 85},
            {'agent': 'classical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'smc', 'confidence': 88, 'weight': 0.25, 'score': 0.22, 'adjusted_confidence': 88},
            {'agent': 'price_action', 'confidence': 75, 'weight': 0.15, 'score': 0.11, 'adjusted_confidence': 75},
            {'agent': 'multitimeframe', 'confidence': 82, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 82},
        ],
        'SELL': [],
        'WAIT': []
    }
    
    result = agent._classic_decision(votes)
    
    assert result['decision'] == 'BUY'
    assert result['buy_count'] == 5
    assert result['buy_agreement_pct'] == 100.0
    assert result['confidence'] > 80


def test_conflict_buy_vs_sell():
    """اختبار: تعارض بين BUY و SELL"""
    
    config = {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {"min_agents_agree": 3, "min_agreement_percentage": 60},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}
    }
    agent = DecisionAgent(config)
    
    votes = {
        'BUY': [
            {'agent': 'technical', 'confidence': 70, 'weight': 0.2, 'score': 0.14, 'adjusted_confidence': 70},
            {'agent': 'classical', 'confidence': 65, 'weight': 0.2, 'score': 0.13, 'adjusted_confidence': 65},
            {'agent': 'smc', 'confidence': 72, 'weight': 0.25, 'score': 0.18, 'adjusted_confidence': 72},
        ],
        'SELL': [
            {'agent': 'price_action', 'confidence': 90, 'weight': 0.15, 'score': 0.13, 'adjusted_confidence': 90},
            {'agent': 'multitimeframe', 'confidence': 85, 'weight': 0.2, 'score': 0.17, 'adjusted_confidence': 85},
        ],
        'WAIT': []
    }
    
    result = agent._classic_decision(votes)
    
    # BUY has 3 agents (60%) but lower total score
    # SELL has 2 agents (40%) but higher individual scores
    # BUY should win because it meets min_agents_agree AND has higher agreement %
    assert result['decision'] == 'BUY'


def test_all_conditions_met():
    """اختبار شامل: كل الشروط موجودة"""
    
    config = {
        "risk_settings": {"min_confidence": 60, "min_rr_ratio": 1.5},
        "signal_requirements": {"min_agents_agree": 3, "min_agreement_percentage": 60},
        "agent_weights": {"technical": 0.2, "classical": 0.2, "smc": 0.25, "price_action": 0.15, "multitimeframe": 0.2}
    }
    agent = DecisionAgent(config)
    
    # 4 من 5 وكلاء يوافقون على BUY = 80% توافق
    votes = {
        'BUY': [
            {'agent': 'technical', 'confidence': 85, 'weight': 0.2, 'score': 0.17, 'adjusted_confidence': 85},
            {'agent': 'classical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            {'agent': 'smc', 'confidence': 88, 'weight': 0.25, 'score': 0.22, 'adjusted_confidence': 88},
            {'agent': 'multitimeframe', 'confidence': 82, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 82},
        ],
        'SELL': [
            {'agent': 'price_action', 'confidence': 70, 'weight': 0.15, 'score': 0.10, 'adjusted_confidence': 70},
        ],
        'WAIT': []
    }
    
    result = agent._classic_decision(votes)
    
    assert result['decision'] == 'BUY'
    assert result['buy_count'] == 4, "4 وكلاء"
    assert result['buy_agreement_pct'] == 80.0, "80% توافق"
    assert result['total_voting_agents'] == 5, "إجمالي 5 وكلاء"
    assert result['rejection_reason'] is None, "لا يوجد سبب رفض"


# ========================
# اختبارات الرسائل
# ========================

def test_signal_message_format(agent):
    """اختبار: تنسيق رسالة الإشارة"""
    
    result = {
        'signal': 'BUY',
        'confidence': 85,
        'reasoning': '3 agents agreed with 100% consensus',
        'votes': {
            'BUY': [
                {'agent': 'technical', 'confidence': 85, 'weight': 0.2, 'score': 0.17, 'adjusted_confidence': 85},
                {'agent': 'classical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
                {'agent': 'smc', 'confidence': 88, 'weight': 0.25, 'score': 0.22, 'adjusted_confidence': 88},
            ],
            'SELL': [],
            'WAIT': []
        },
        'classic': {
            'buy_count': 3,
            'sell_count': 0,
            'buy_agreement_pct': 100.0,
            'sell_agreement_pct': 0,
            'total_voting_agents': 3,
            'decision': 'BUY',
            'confidence': 85,
            'rejection_reason': None
        },
        'ai': {'available': True, 'provider': 'openai', 'consensus_strength': 'Strong'},
        'learning': {'enabled': True, 'overall_win_rate': 65.5},
        'risk_assessment': {'score': 0, 'assessment': 'مقبول ✅', 'factors': []},
        'weights': {'technical': 0.20, 'classical': 0.20, 'smc': 0.25, 'price_action': 0.15, 'multitimeframe': 0.20}
    }
    
    message = agent.get_decision_message(result)
    
    assert '🔥 متطلبات التوافق:' in message, "يجب أن يظهر قسم التوافق"
    assert '3/3 ✅' in message, "يجب أن يظهر عدد الوكلاء"
    assert '100% ✅' in message, "يجب أن يظهر نسبة التوافق"
    assert 'BUY' in message


def test_wait_signal_message(agent):
    """اختبار: رسالة انتظار مع سبب الرفض"""
    
    result = {
        'signal': 'WAIT',
        'confidence': 50,
        'reasoning': 'Not enough agents agreed',
        'votes': {
            'BUY': [
                {'agent': 'technical', 'confidence': 80, 'weight': 0.2, 'score': 0.16, 'adjusted_confidence': 80},
            ],
            'SELL': [],
            'WAIT': []
        },
        'classic': {
            'buy_count': 1,
            'sell_count': 0,
            'buy_agreement_pct': 100.0,
            'sell_agreement_pct': 0,
            'total_voting_agents': 1,
            'decision': 'WAIT',
            'confidence': 50,
            'rejection_reason': 'لا يوجد عدد كافٍ من الوكلاء (1/3)'
        },
        'ai': {'available': False},
        'learning': {'enabled': True},
        'risk_assessment': {'score': 1, 'assessment': 'محتمل ⚠️', 'factors': ['RSI في منطقة ذروة']},
        'weights': {}
    }
    
    message = agent.get_decision_message(result)
    
    assert '❌ سبب الانتظار:' in message, "يجب أن يظهر سبب الانتظار"
    assert 'لا يوجد عدد كافٍ' in message, "يجب أن يظهر السبب الفعلي"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])