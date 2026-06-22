"""
🔬 اختبارات AI Prompts المحسّنة
"""

import pytest
from unittest.mock import Mock, MagicMock, AsyncMock
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ai_service import AIService, AIProvider, AIResponse


@pytest.fixture
def mock_config():
    """إعدادات الاختبار"""
    return {
        'ai_service': {
            'enabled': True,
            'provider': 'openai',
            'model': 'gpt-4o-mini',
            'max_tokens': 800,
            'temperature': 0.25,
            'api_key': 'test_key'
        },
        'ai_providers': {
            'openai': {'models': ['gpt-4o-mini']},
            'anthropic': {'models': ['claude-3-5-sonnet-20241022']},
            'grok': {'models': ['grok-2-mini']},
            'gemini': {'models': ['gemini-2.0-flash-exp']}
        }
    }


@pytest.fixture
def ai_service(mock_config):
    return AIService(mock_config)


class TestAIPrompts:
    """اختبارات Prompts المحسّنة"""
    
    def test_technical_agent_prompt_structure(self, ai_service):
        """اختبار: prompt الوكيل الفني يجب أن يحتوي على عناصر محددة"""
        
        prompt = ai_service._build_analysis_prompt(
            symbol='XAUUSD',
            price_data={
                'current_price': 2350.50,
                'open': 2348.00,
                'high': 2352.00,
                'low': 2346.00,
                'close': 2350.50,
                'change_pct': 0.11
            },
            indicators={
                'ema_20': 2348.50,
                'ema_50': 2345.00,
                'ema_200': 2330.00,
                'rsi': 58,
                'macd': 2.5,
                'macd_signal': 1.8,
                'macd_histogram': 0.7,
                'atr': 12.5,
                'support': 2345.00,
                'resistance': 2360.00,
                'trend': 'Bullish'
            },
            timeframe='15m',
            agent_type='technical'
        )
        
        # التحقق من وجود العناصر الأساسية
        assert 'XAU/USD' in prompt or 'XAUUSD' in prompt or 'Current price' in prompt
        assert '2350' in prompt  # قد يظهر كـ 2350.5 أو 2350.50
        assert 'EMA' in prompt or 'ema' in prompt
        assert 'RSI' in prompt or 'rsi' in prompt
        assert 'ATR' in prompt or 'atr' in prompt
        assert 'JSON' in prompt
        assert 'signal' in prompt
        assert 'confidence' in prompt
    
    def test_smc_agent_prompt_structure(self, ai_service):
        """اختبار: prompt SMC يجب أن يحتوي على بنية السوق"""
        
        prompt = ai_service._build_analysis_prompt(
            symbol='XAUUSD',
            price_data={'current_price': 2350.50},
            indicators={'trend': 'Bullish'},
            timeframe='1H',
            agent_type='smc'
        )
        
        assert 'SMC' in prompt or 'smart money' in prompt.lower() or 'البنية' in prompt
        assert 'Bullish' in prompt or 'Neutral' in prompt
        assert 'signal' in prompt
        assert 'confidence' in prompt
    
    def test_decision_agent_prompt_requirements(self, ai_service):
        """اختبار: prompt القرار يجب أن يتضمن الشروط الجديدة"""
        
        prompt = ai_service._build_analysis_prompt(
            symbol='XAUUSD',
            price_data={'current_price': 2350.50},
            indicators={'rsi': 60, 'atr': 15},
            timeframe='15m',
            agent_type='decision'
        )
        
        # الشروط الجديدة
        assert '3' in prompt or 'ثلاثة' in prompt, "يجب أن يذكر 3 وكلاء"
        assert '60%' in prompt or 'توافق' in prompt, "يجب أن يذكر نسبة التوافق"
        assert 'final_signal' in prompt
        assert 'consensus_strength' in prompt
    
    def test_multiframe_prompt_structure(self, ai_service):
        """اختبار: prompt متعدد الإطارات"""
        
        prompt = ai_service._build_analysis_prompt(
            symbol='XAUUSD',
            price_data={'current_price': 2350.50},
            indicators={'trend': 'Bullish'},
            timeframe='15m',
            agent_type='multitimeframe'
        )
        
        assert '4H' in prompt or 'h4' in prompt.lower()
        assert '1H' in prompt or 'h1' in prompt.lower()
        assert 'alignment' in prompt
        assert 'signal' in prompt
    
    def test_news_risk_prompt(self, ai_service):
        """اختبار: prompt تحليل المخاطر"""
        
        prompt = ai_service._build_analysis_prompt(
            symbol='XAUUSD',
            price_data={'current_price': 2350.50},
            indicators={'rsi': 55},
            timeframe='1H',
            agent_type='news_risk'
        )
        
        assert 'risk' in prompt.lower() or 'المخاطر' in prompt
        assert 'confidence_adjustment' in prompt
        assert 'gold_sentiment' in prompt or 'sentiment' in prompt
    
    def test_risk_management_prompt(self, ai_service):
        """اختبار: prompt إدارة المخاطر"""
        
        prompt = ai_service._build_analysis_prompt(
            symbol='XAUUSD',
            price_data={'current_price': 2350.50},
            indicators={'atr': 15},
            timeframe='15m',
            agent_type='risk_management'
        )
        
        assert '10,000' in prompt or '10000' in prompt or 'Account' in prompt
        assert '1%' in prompt or '2%' in prompt or 'مخاطرة' in prompt
        assert 'stop_loss' in prompt.lower() or 'SL' in prompt
        assert 'take_profit' in prompt.lower() or 'TP' in prompt


class TestSystemPrompts:
    """اختبارات System Prompts"""
    
    def test_system_prompts_loaded(self, ai_service):
        """اختبار: تحميل System Prompts"""
        
        assert ai_service.system_prompts is not None
        assert len(ai_service.system_prompts) > 0
        assert 'technical' in ai_service.system_prompts
        assert 'decision' in ai_service.system_prompts
    
    def test_decision_prompt_exists(self, ai_service):
        """اختبار: وجود prompt القرار"""
        
        assert 'decision' in ai_service.system_prompts
        assert 'decision' in ai_service.system_prompts['decision'].lower() or 'القرارات' in ai_service.system_prompts['decision']


class TestAIServiceConfig:
    """اختبارات إعدادات AI"""
    
    def test_token_cost_config(self, ai_service):
        """اختبار: إعدادات تكلفة الـ tokens"""
        
        assert ai_service.token_costs is not None
        assert 'openai' in ai_service.token_costs
        assert 'anthropic' in ai_service.token_costs
        assert ai_service.token_costs['openai']['input'] > 0
    
    def test_max_tokens_increased(self, ai_service):
        """اختبار: زيادة max_tokens للتحليل المحسّن"""
        
        assert ai_service.max_tokens >= 800, "max_tokens يجب أن يكون 800+ للتوضيح"
    
    def test_temperature_reduced(self, ai_service):
        """اختبار: تقليل temperature للدقة"""
        
        assert ai_service.temperature <= 0.3, "temperature يجب أن يكون 0.3 أو أقل"


class TestJSONResponseParsing:
    """اختبارات تحليل JSON"""
    
    def test_parse_valid_json(self, ai_service):
        """اختبار: تحليل JSON صحيح"""
        
        json_str = '{"signal": "BUY", "confidence": 85, "reasoning": "RSI bullish divergence"}'
        result = ai_service.parse_json_response(json_str)
        
        assert result is not None
        assert result['signal'] == 'BUY'
        assert result['confidence'] == 85
    
    def test_parse_json_with_code_block(self, ai_service):
        """اختبار: تحليل JSON مع code block"""
        
        json_str = '''```json
{
    "signal": "SELL",
    "confidence": 75
}
```'''
        result = ai_service.parse_json_response(json_str)
        
        assert result is not None
        assert result['signal'] == 'SELL'
        assert result['confidence'] == 75
    
    def test_parse_invalid_json(self, ai_service):
        """اختبار: التعامل مع JSON غير صالح"""
        
        json_str = "这不是 JSON"
        result = ai_service.parse_json_response(json_str)
        
        assert result is None
    
    def test_parse_decision_response(self, ai_service):
        """اختبار: تحليل استجابة القرار"""
        
        json_str = '''{
            "final_signal": "BUY",
            "confidence": 88,
            "consensus_strength": "Strong",
            "reasoning": "3 agents agreed with high confidence",
            "risk_assessment": "مقبول",
            "trade_quality": "High"
        }'''
        result = ai_service.parse_json_response(json_str)
        
        assert result is not None
        assert result.get('final_signal') == 'BUY'
        assert result.get('consensus_strength') == 'Strong'
        assert result.get('trade_quality') == 'High'


class TestAIResponse:
    """اختبارات استجابة AI"""
    
    def test_ai_response_dataclass(self):
        """اختبار: هيكل استجابة AI"""
        
        response = AIResponse(
            success=True,
            content='{"signal": "BUY"}',
            provider="openai",
            model="gpt-4o-mini",
            tokens_used=500,
            cost=0.075
        )
        
        assert response.success is True
        assert response.provider == "openai"
        assert response.tokens_used == 500
    
    def test_ai_response_error(self):
        """اختبار: استجابة خطأ"""
        
        response = AIResponse(
            success=False,
            content="",
            error="API Key not configured",
            provider="openai"
        )
        
        assert response.success is False
        assert response.error is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])