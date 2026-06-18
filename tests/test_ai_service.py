"""
🧪 اختبارات خدمة AI
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ai_service import AIService, AIProvider, AIResponse, get_ai_service


@pytest.fixture
def config():
    """إعدادات وهمية"""
    return {
        'ai_service': {
            'enabled': True,
            'provider': 'openai',
            'model': 'gpt-4o-mini',
            'max_tokens': 500,
            'temperature': 0.3,
            'api_key': 'test_api_key'
        }
    }


@pytest.fixture
def ai_service(config):
    """خدمة AI"""
    return AIService(config)


class TestAIProvider:
    """اختبارات مزودي AI"""
    
    def test_all_providers(self):
        """اختبار جميع المزودين"""
        providers = [
            AIProvider.OPENAI,
            AIProvider.ANTHROPIC,
            AIProvider.GROK,
            AIProvider.GEMINI
        ]
        
        assert len(providers) == 4
        assert AIProvider.OPENAI.value == "openai"
        assert AIProvider.ANTHROPIC.value == "anthropic"
        assert AIProvider.GROK.value == "grok"
        assert AIProvider.GEMINI.value == "gemini"


class TestAIResponse:
    """اختبارات استجابة AI"""
    
    def test_successful_response(self):
        """اختبار استجابة ناجحة"""
        response = AIResponse(
            success=True,
            content='{"signal": "BUY", "confidence": 85}',
            provider="openai",
            model="gpt-4o-mini",
            tokens_used=200,
            cost=0.03
        )
        
        assert response.success is True
        assert response.content is not None
        assert response.provider == "openai"
        assert response.tokens_used == 200
    
    def test_failed_response(self):
        """اختبار استجابة فاشلة"""
        response = AIResponse(
            success=False,
            content="",
            error="API key invalid",
            provider="openai"
        )
        
        assert response.success is False
        assert response.error == "API key invalid"


class TestAIService:
    """اختبارات خدمة AI"""
    
    def test_init_with_config(self, ai_service):
        """اختبار التهيئة مع الإعدادات"""
        assert ai_service.provider == AIProvider.OPENAI
        assert ai_service.model == "gpt-4o-mini"
        assert ai_service.max_tokens == 500
    
    def test_init_without_api_key(self):
        """اختبار التهيئة بدون API Key"""
        config = {
            'ai_service': {
                'enabled': True,
                'provider': 'openai'
            }
        }
        service = AIService(config)
        
        # يجب أن يكون API Key فارغ أو None
        assert service.api_key == ""
    
    def test_token_costs(self, ai_service):
        """اختبار تكاليف الـ tokens"""
        assert 'openai' in ai_service.token_costs
        assert 'anthropic' in ai_service.token_costs
        assert ai_service.token_costs['openai']['input'] > 0
    
    def test_build_analysis_prompt(self, ai_service):
        """اختبار بناء prompt التحليل"""
        price_data = {
            'current_price': 2350.50,
            'open': 2348.00,
            'high': 2352.00,
            'low': 2345.00,
            'close': 2350.00,
            'change_pct': 0.5
        }
        
        indicators = {
            'ema_50': 2348.00,
            'ema_200': 2340.00,
            'rsi': 65,
            'macd': 5.2,
            'macd_signal': 4.5,
            'macd_histogram': 0.7,
            'atr': 12.5,
            'support': 2340.00,
            'resistance': 2360.00,
            'trend': 'BULLISH'
        }
        
        prompt = ai_service._build_analysis_prompt(
            symbol="XAUUSD",
            price_data=price_data,
            indicators=indicators,
            timeframe="1h",
            agent_type="technical"
        )
        
        # التحقق من وجود البيانات (التنسيق قد يختلف)
        assert "2350" in prompt or "2350.5" in prompt
        assert "65" in prompt  # RSI
        assert "BUY" in prompt or "SELL" in prompt or "WAIT" in prompt
        assert "XAU/USD" in prompt or "XAUUSD" in prompt
    
    def test_build_smc_prompt(self, ai_service):
        """اختبار بناء prompt SMC"""
        prompt = ai_service._build_analysis_prompt(
            symbol="XAUUSD",
            price_data={'current_price': 2350},
            indicators={'rsi': 50},
            timeframe="1h",
            agent_type="smc"
        )
        
        assert "Market Structure" in prompt or "SMC" in prompt
        assert "structure" in prompt.lower()
    
    def test_build_decision_prompt(self, ai_service):
        """اختبار بناء prompt القرار"""
        prompt = ai_service._build_analysis_prompt(
            symbol="XAUUSD",
            price_data={'current_price': 2350},
            indicators={'rsi': 55},
            timeframe="1h",
            agent_type="decision"
        )
        
        assert "decision" in prompt.lower() or "القرار" in prompt
    
    def test_parse_json_response_valid(self, ai_service):
        """اختبار تحليل JSON صحيح"""
        content = '''
        {
            "signal": "BUY",
            "confidence": 85,
            "reasoning": "اتجاه صاعد مع RSI محايد",
            "entry_zone": "2348-2350",
            "stop_loss": "2340",
            "take_profit_1": "2360",
            "take_profit_2": "2375",
            "risk_reward": "1.8"
        }
        '''
        
        result = ai_service.parse_json_response(content)
        
        assert result is not None
        assert result['signal'] == 'BUY'
        assert result['confidence'] == 85
        assert result['risk_reward'] == '1.8'
    
    def test_parse_json_response_with_markdown(self, ai_service):
        """اختبار تحليل JSON مع markdown"""
        content = '''
        ```json
        {"signal": "SELL", "confidence": 70}
        ```
        '''
        
        result = ai_service.parse_json_response(content)
        
        assert result is not None
        assert result['signal'] == 'SELL'
    
    def test_parse_json_response_invalid(self, ai_service):
        """اختبار تحليل JSON غير صحيح"""
        content = "هذا ليس JSON صحيح"
        
        result = ai_service.parse_json_response(content)
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_analyze_chart_no_api_key(self):
        """اختبار التحليل بدون API Key"""
        config = {
            'ai_service': {
                'enabled': True,
                'provider': 'openai',
                'api_key': None
            }
        }
        service = AIService(config)
        
        result = await service.analyze_chart(
            symbol="XAUUSD",
            price_data={'current_price': 2350},
            technical_indicators={'rsi': 50},
            timeframe="1h",
            agent_type="technical"
        )
        
        assert result.success is False
        assert "API Key" in result.error
    
    def test_get_ai_service_singleton(self, config):
        """اختبار Singleton"""
        service1 = get_ai_service(config)
        service2 = get_ai_service(config)
        
        # يجب أن يكون نفس الـ instance
        assert service1 is service2


class TestAIIntegration:
    """اختبارات التكامل"""
    
    def test_full_analysis_flow(self, ai_service):
        """اختبار سير التحليل الكامل"""
        # إعداد البيانات
        price_data = {
            'current_price': 2350.50,
            'open': 2348.00,
            'high': 2352.00,
            'low': 2345.00,
            'close': 2350.00
        }
        
        indicators = {
            'ema_50': 2348.00,
            'ema_200': 2340.00,
            'rsi': 68,
            'macd_histogram': 1.2,
            'atr': 12.5
        }
        
        # بناء prompt
        prompt = ai_service._build_analysis_prompt(
            "XAUUSD", price_data, indicators, "1h", "technical"
        )
        
        # التحقق من المحتوى
        assert "2350" in prompt or "2350.5" in prompt
        assert "68" in prompt
        assert "1.2" in prompt
    
    def test_ai_response_format(self):
        """اختبار تنسيق استجابة AI"""
        response = AIResponse(
            success=True,
            content='{"signal": "BUY", "confidence": 80, "reasoning": "test"}',
            provider="openai",
            model="gpt-4o-mini",
            tokens_used=150,
            cost=0.0225
        )
        
        # التحقق من جميع الحقول
        assert response.success
        assert response.provider == "openai"
        assert response.model == "gpt-4o-mini"
        assert response.tokens_used == 150
        assert response.cost == 0.0225


if __name__ == "__main__":
    pytest.main([__file__, "-v"])