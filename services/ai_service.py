"""
🤖 AI Service - Gold AI Signals
خدمة الذكاء الاصطناعي للتحليل الذكي
يدعم: OpenAI (ChatGPT), Anthropic (Claude), Grok (xAI), Google (Gemini)

🚀 VERSION 2.0 - Prompts محسّنة مع سياق أعمق وتحليل أدق
"""

import os
import json
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class AIProvider(Enum):
    """مزودي AI المدعومين"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROK = "grok"  # xAI / Grok
    GROQ = "groq"  # GroqCloud
    GEMINI = "gemini"


@dataclass
class AIResponse:
    """استجابة AI"""
    success: bool
    content: str
    error: Optional[str] = None
    provider: str = ""
    model: str = ""
    tokens_used: int = 0
    cost: float = 0.0


class AIService:
    """
    🤖 خدمة الذكاء الاصطناعي - الإصدار 2.0
    
    ✅ Prompts محسّنة مع:
    - سياق أعمق للسوق
    - تحليل متعددة الأبعاد
    - Risk/Reward محسوب
    - مستويات واضحة للدخول والخروج
    """
    
    def __init__(self, config: Dict):
        self.config = config
        self.ai_config = config.get('ai_service', {})
        self.provider = AIProvider(self.ai_config.get('provider', 'openai'))
        self.model = self.ai_config.get('model', 'gpt-4o-mini')
        self.max_tokens = self.ai_config.get('max_tokens', 800)  # زيادة للتوضيح
        self.temperature = self.ai_config.get('temperature', 0.25)  # أقل للتحديد
        
        # تحميل API Key من البيئة أو Secrets
        self.api_key = self._load_api_key()
        
        # تكلفة الـ tokens لكل مزود
        self.token_costs = {
            'openai': {'input': 0.00015, 'output': 0.0006},
            'anthropic': {'input': 0.0008, 'output': 0.004},
            'grok': {'input': 0.0005, 'output': 0.0015},
            'groq': {'input': 0.00005, 'output': 0.00008},
            'gemini': {'input': 0.000125, 'output': 0.0005}
        }
        
        # 🚀 Prompts محسّنة
        self.system_prompts = self._load_system_prompts()
    
    def _load_api_key(self) -> str:
        """تحميل API Key من Environment أو Secrets"""
        provider_keys = {
            'openai': 'OPENAI_API_KEY',
            'anthropic': 'ANTHROPIC_API_KEY',
            'grok': 'GROK_API_KEY',
            'groq': 'GROQ_API_KEY',
            'gemini': 'GEMINI_API_KEY'
        }
        
        key_name = provider_keys.get(self.provider.value, 'OPENAI_API_KEY')
        api_key = os.environ.get(key_name)
        
        if not api_key:
            api_key = self.ai_config.get('api_key')
            # config.json may store a pointer like "ENV:GROQ_API_KEY" instead of
            # a literal key (same convention as database.py / market_data.py).
            # Without this, a missing real secret would silently fall through
            # to using the literal "ENV:..." string as the API key itself.
            if isinstance(api_key, str) and api_key.startswith('ENV:'):
                api_key = os.environ.get(api_key.replace('ENV:', '', 1))
        
        return api_key or ""
    
    def _load_system_prompts(self) -> Dict[str, str]:
        """تحميل System Prompts للوكلاء المختلفين"""
        return {
            'technical': """أنت محلل فني محترف للذهب. أجب بـ JSON فقط. حافظ على الدقة في مستويات SL/TP.""",
            'smc': """أنت محلل SMC محترف. حدد البنية السوقية وكتل الطلب/العرض بدقة.""",
            'classical': """أنت خبير أنماط الشموع اليابانية. قيّم موثوقية النمط قبل التوصية.""",
            'price_action': """أنت محلل Price Action محترف. حدد مناطق العرض والطلب بوضوح.""",
            'multitimeframe': """أنت محلل متعدد الإطارات. قارن بين الإطارات واختر الأفضل للدخول.""",
            'news_risk': """أنت محلل مخاطر جيوسياسية. قيّم تأثير الأخبار على الذهب.""",
            'risk_management': """أنت خبير إدارة مخاطر. احسب حجم الصفقة الأمثل.""",
            'decision': """أنت خبير اتخاذ القرارات. ادمج كل التحليلات وقرر بحكمة."""
        }
    
    async def analyze_chart(
        self,
        symbol: str,
        price_data: Dict,
        technical_indicators: Dict,
        timeframe: str,
        agent_type: str
    ) -> AIResponse:
        """🔍 تحليل الشارت باستخدام AI محسّن"""
        
        prompt = self._build_analysis_prompt(
            symbol, price_data, technical_indicators, timeframe, agent_type
        )
        
        return await self._call_ai(prompt, agent_type)
    
    def _build_analysis_prompt(
        self,
        symbol: str,
        price_data: Dict,
        indicators: Dict,
        timeframe: str,
        agent_type: str
    ) -> str:
        """🚀 بناء prompt محسّن مع سياق عميق"""
        
        # تنسيق بيانات السعر
        current_price = price_data.get('current_price', price_data.get('close', 'N/A'))
        price_info = f"""💰 بيانات السعر:
- السعر الحالي: {current_price}
- Open: {price_data.get('open', 'N/A')}
- High: {price_data.get('high', 'N/A')}
- Low: {price_data.get('low', 'N/A')}
- Close: {price_data.get('close', 'N/A')}
- التغيير: {price_data.get('change_pct', 'N/A')}%

📊 المؤشرات الفنية:
- EMA 20: {indicators.get('ema_20', indicators.get('ema_50', 'N/A'))}
- EMA 50: {indicators.get('ema_50', 'N/A')}
- EMA 200: {indicators.get('ema_200', 'N/A')}
- RSI (14): {indicators.get('rsi', 'N/A')}
- MACD: {indicators.get('macd', 'N/A')}
- MACD Signal: {indicators.get('macd_signal', 'N/A')}
- MACD Histogram: {indicators.get('macd_histogram', 'N/A')}
- ATR: {indicators.get('atr', 'N/A')}
- الدعم: {indicators.get('support', 'N/A')}
- المقاومة: {indicators.get('resistance', 'N/A')}
- الاتجاه: {indicators.get('trend', 'N/A')}
- Volatility: {indicators.get('volatility', 'N/A')}

⏰ الإطار الزمني: {timeframe}"""
        
        # 🚀 PROMPTS محسّنة حسب نوع الوكيل
        
        prompts = {
            'technical': f"""
🎯 أنت محلل فني محترف للذهب (XAU/USD).

{price_info}

🔍 تحليل شامل:
- القوة النسبية (RSI, MACD)
- اتجاه الترند (EMAs)
- مستويات الدعم والمقاومة
- التذبذب (ATR)

📋 أجب بـ JSON فقط (دقيق ومختصر):
{{
    "signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "reasoning": "سبب التوصية",
    "entry_zone": "منطقة الدخول",
    "stop_loss": "SL (ATR-based)",
    "take_profit_1": "TP1 (ATR × 2)",
    "take_profit_2": "TP2 (ATR × 3.5)",
    "risk_reward": "1:X"
}}
""",
            
            'smc': f"""
🎯 أنت محلل Smart Money Concepts محترف.

{price_info}

🔍 تحليل SMC:
- Higher Highs / Higher Lows
- Order Blocks (كتل الطلب/العرض)
- Liquidity Zones (مناطق السيولة)
- Break of Structure (كسر البنية)

📋 أجب بـ JSON فقط:
{{
    "structure": "Bullish|Bearish|Neutral",
    "trend_stage": "Accumulation|Distribution|Continuation",
    "order_blocks": ["كتلة الطلب", "كتلة العرض"],
    "liquidity_zones": ["منطقة السيولة"],
    "signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "reasoning": "سبب التحليل"
}}
""",
            
            'classical': f"""
🎯 أنت محلل أنماط الشموع اليابانية محترف.

{price_info}

🔍 أنماط الانعكاس:
- Hammer, Engulfing, Morning Star
- Shooting Star, Dark Cloud, Evening Star

📋 أجب بـ JSON فقط:
{{
    "candlestick_pattern": "اسم النمط",
    "pattern_direction": "Bullish|Bearish|Neutral",
    "reliability": "High|Medium|Low",
    "signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "reasoning": "تفسير النمط"
}}
""",
            
            'price_action': f"""
🎯 أنت محلل Price Action محترف.

{price_info}

🔍 تحليل حركة السعر:
- Supply & Demand Zones
- Swing Highs/Lows
- Fair Value Gaps
- Break of Candle Structure

📋 أجب بـ JSON فقط:
{{
    "key_zones": {{"demand": "##", "supply": "##"}},
    "swing_analysis": "تحليل التأرجح",
    "signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "reasoning": "سبب التوصية",
    "entry_trigger": "محفز الدخول"
}}
""",
            
            'multitimeframe': f"""
🎯 أنت محلل متعدد الإطارات الزمنية.

{price_info}

🔍 مقارنة الإطارات:
- 4H: الاتجاه العام
- 1H: الاتجاه المتوسط
- 15m/5m: نقطة الدخول

📋 أجب بـ JSON فقط:
{{
    "h4_trend": "Bullish|Bearish|Neutral",
    "h1_trend": "Bullish|Bearish|Neutral",
    "m15_trend": "Bullish|Bearish|Neutral",
    "alignment": "Fully_Aligned|Not_Aligned",
    "signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "reasoning": "سبب التوافق/الاختلاف"
}}
""",
            
            'news_risk': f"""
🎯 أنت محلل مخاطر جيوسياسية محترف.

{price_info}

🔍 العوامل المؤثرة:
- أخبار USD والـ Fed
- التضخم والبيانات الاقتصادية
- الأحداث الجيوسياسية

📋 أجب بـ JSON فقط:
{{
    "risk_level": "High|Medium|Low",
    "key_factors": ["عامل 1", "عامل 2"],
    "gold_sentiment": "Bullish|Bearish|Neutral",
    "confidence_adjustment": -20 إلى +20,
    "recommendation": "BUY|SELL|WAIT"
}}
""",
            
            'risk_management': f"""
🎯 أنت خبير إدارة المخاطر.

{price_info}

📋 الإعدادات:
- حساب: 10,000 دولار
- مخاطرة: 1-2%
- ATR: {indicators.get('atr', 'N/A')}

📋 أجب بـ JSON فقط:
{{
    "risk_per_trade_percent": "1% أو 2%",
    "position_size": "حجم الصفقة",
    "stop_loss_pips": "نقاط SL",
    "take_profit_1_pips": "نقاط TP1",
    "risk_reward_ratio": "1:X",
    "assessment": "Safe|Moderate|High_Risk"
}}
""",
            
            'decision': f"""
🎯 أنت خبير اتخاذ القرارات النهائية.

{price_info}

🎯 المتطلبات:
- 3 وكلاء كحد أدنى يوافقون
- 60% نسبة توافق

📋 أجب بـ JSON فقط (دقيق):
{{
    "final_signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "consensus_strength": "Strong|Moderate|Weak",
    "reasoning": "سبب القرار",
    "risk_assessment": "مقبول|عالي|مرفوض",
    "entry_zone": "منطقة الدخول",
    "stop_loss": "SL",
    "take_profits": {{"tp1": "##", "tp2": "##"}},
    "trade_quality": "High|Medium|Low"
}}
"""
        }
        
        return prompts.get(agent_type, prompts['technical'])
    
    async def _call_ai(self, prompt: str, agent_type: str) -> AIResponse:
        """استدعاء AI API"""
        
        if not self.api_key:
            return AIResponse(
                success=False,
                content="",
                error="API Key not configured",
                provider=self.provider.value
            )
        
        try:
            if self.provider == AIProvider.OPENAI:
                return await self._call_openai(prompt, agent_type)
            elif self.provider == AIProvider.ANTHROPIC:
                return await self._call_anthropic(prompt, agent_type)
            elif self.provider == AIProvider.GROK:
                return await self._call_grok(prompt, agent_type)
            elif self.provider == AIProvider.GROQ:
                return await self._call_groq(prompt, agent_type)
            elif self.provider == AIProvider.GEMINI:
                return await self._call_gemini(prompt, agent_type)
            else:
                return AIResponse(
                    success=False,
                    content="",
                    error=f"Unknown provider: {self.provider}",
                    provider=self.provider.value
                )
                
        except Exception as e:
            logger.error(f"❌ خطأ في استدعاء AI: {e}")
            return AIResponse(
                success=False,
                content="",
                error=str(e),
                provider=self.provider.value
            )
    
    async def _call_openai(self, prompt: str, agent_type: str) -> AIResponse:
        """استدعاء OpenAI API (ChatGPT)"""
        
        try:
            import openai
            
            client = openai.OpenAI(api_key=self.api_key)
            
            system_prompt = self.system_prompts.get(agent_type, self.system_prompts['technical'])
            
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                response_format={"type": "json_object"}  # OpenAI native JSON mode
            )
            
            content = response.choices[0].message.content
            
            tokens = response.usage.total_tokens
            cost = (tokens / 1000) * self.token_costs['openai']['input']
            
            return AIResponse(
                success=True,
                content=content,
                provider="openai",
                model=self.model,
                tokens_used=tokens,
                cost=cost
            )
            
        except Exception as e:
            logger.error(f"❌ خطأ OpenAI: {e}")
            return AIResponse(
                success=False,
                content="",
                error=str(e),
                provider="openai"
            )
    
    async def _call_anthropic(self, prompt: str, agent_type: str) -> AIResponse:
        """استدعاء Anthropic API (Claude)"""
        
        try:
            import anthropic
            
            client = anthropic.Anthropic(api_key=self.api_key)
            
            system_prompt = self.system_prompts.get(agent_type, self.system_prompts['technical'])
            
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            
            content = response.content[0].text
            
            tokens = response.usage.input_tokens + response.usage.output_tokens
            cost = (tokens / 1000) * self.token_costs['anthropic']['input']
            
            return AIResponse(
                success=True,
                content=content,
                provider="anthropic",
                model=self.model,
                tokens_used=tokens,
                cost=cost
            )
            
        except Exception as e:
            logger.error(f"❌ خطأ Anthropic: {e}")
            return AIResponse(
                success=False,
                content="",
                error=str(e),
                provider="anthropic"
            )
    
    async def _call_groq(self, prompt: str, agent_type: str) -> AIResponse:
        """استدعاء GroqCloud API (OpenAI-compatible)."""

        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            system_prompt = self.system_prompts.get(agent_type, self.system_prompts['technical'])
            data = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "response_format": {"type": "json_object"},
            }

            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=35,
            )
            result = response.json()

            if not response.ok:
                return AIResponse(
                    success=False,
                    content="",
                    error=result.get("error", {}).get("message", str(result)),
                    provider="groq",
                    model=self.model,
                )

            content = result['choices'][0]['message']['content']
            tokens = int(result.get('usage', {}).get('total_tokens', 0) or 0)
            cost = (tokens / 1000) * self.token_costs['groq']['input']

            return AIResponse(
                success=True,
                content=content,
                provider="groq",
                model=self.model,
                tokens_used=tokens,
                cost=cost,
            )

        except Exception as e:
            logger.error(f"❌ خطأ Groq: {e}")
            return AIResponse(
                success=False,
                content="",
                error=str(e),
                provider="groq",
                model=self.model,
            )

    async def _call_grok(self, prompt: str, agent_type: str) -> AIResponse:
        """استدعاء Grok API (xAI)"""
        
        try:
            import requests
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            system_prompt = self.system_prompts.get(agent_type, self.system_prompts['technical'])
            
            data = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": self.max_tokens,
                "temperature": self.temperature
            }
            
            response = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers=headers,
                json=data
            )
            
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            tokens = result.get('usage', {}).get('total_tokens', 0)
            cost = (tokens / 1000) * self.token_costs['grok']['input']
            
            return AIResponse(
                success=True,
                content=content,
                provider="grok",
                model=self.model,
                tokens_used=tokens,
                cost=cost
            )
            
        except Exception as e:
            logger.error(f"❌ خطأ Grok: {e}")
            return AIResponse(
                success=False,
                content="",
                error=str(e),
                provider="grok"
            )
    
    async def _call_gemini(self, prompt: str, agent_type: str) -> AIResponse:
        """استدعاء Google Gemini API"""
        
        try:
            import requests
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            
            system_prompt = self.system_prompts.get(agent_type, self.system_prompts['technical'])
            
            data = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }],
                "generationConfig": {
                    "maxOutputTokens": self.max_tokens,
                    "temperature": self.temperature
                },
                "systemInstruction": {
                    "parts": [{"text": system_prompt}]
                }
            }
            
            response = requests.post(
                f"{url}?key={self.api_key}",
                json=data
            )
            
            result = response.json()
            content = result['candidates'][0]['content']['parts'][0]['text']
            
            tokens = len(prompt.split()) * 1.3
            cost = (tokens / 1000) * self.token_costs['gemini']['input']
            
            return AIResponse(
                success=True,
                content=content,
                provider="gemini",
                model=self.model,
                tokens_used=int(tokens),
                cost=cost
            )
            
        except Exception as e:
            logger.error(f"❌ خطأ Gemini: {e}")
            return AIResponse(
                success=False,
                content="",
                error=str(e),
                provider="gemini"
            )
    
    def parse_json_response(self, content: str) -> Optional[Dict]:
        """تحليل استجابة JSON من AI"""
        try:
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            
            return json.loads(content)
        except Exception as e:
            logger.error(f"❌ خطأ في تحليل JSON: {e}")
            logger.debug(f"المحتوى: {content[:200]}")
            return None


# Singleton instance
_ai_service: Optional[AIService] = None


def get_ai_service(config: Dict) -> AIService:
    """الحصول على instance خدمة AI"""
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService(config)
    return _ai_service