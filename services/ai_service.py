"""
🤖 AI Service - Gold AI Signals
خدمة الذكاء الاصطناعي للتحليل الذكي
يدعم: OpenAI (ChatGPT), Anthropic (Claude), Grok (xAI), Google (Gemini)

🚀 VERSION 2.0 - Prompts محسّنة مع سياق أعمق وتحليل أدق
"""

import os
import json
import logging
import asyncio
from typing import Dict, Optional
from dataclasses import dataclass
from enum import Enum

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
            'technical': """You are a professional gold technical analyst. Reply in JSON only and in English. Keep SL/TP levels accurate.""",
            'smc': """You are a professional SMC analyst. Identify market structure and supply/demand blocks precisely. Reply in concise professional English only. Do not use any other language.""",
            'classical': """You are an expert in Japanese candlestick patterns. Assess pattern reliability before recommending. Reply in concise professional English only. Do not use any other language.""",
            'price_action': """You are a professional Price Action analyst. Identify supply and demand zones clearly. Reply in concise professional English only. Do not use any other language.""",
            'multitimeframe': """You are a multi-timeframe analyst. Compare timeframes and pick the best entry. Reply in concise professional English only. Do not use any other language.""",
            'news_risk': """You are a geopolitical risk analyst. Assess the impact of news on gold. Reply in concise professional English only. Do not use any other language.""",
            'risk_management': """You are a risk-management expert. Compute the optimal position size. Reply in concise professional English only. Do not use any other language.""",
            'decision': """You are a decision-making expert. Integrate all analyses and decide wisely. Reply in concise professional English only. Do not use any other language."""
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
        price_info = f"""💰 Price data:
- Current price: {current_price}
- Open: {price_data.get('open', 'N/A')}
- High: {price_data.get('high', 'N/A')}
- Low: {price_data.get('low', 'N/A')}
- Close: {price_data.get('close', 'N/A')}
- Change: {price_data.get('change_pct', 'N/A')}%

📊 Technical indicators:
- EMA 20: {indicators.get('ema_20', indicators.get('ema_50', 'N/A'))}
- EMA 50: {indicators.get('ema_50', 'N/A')}
- EMA 200: {indicators.get('ema_200', 'N/A')}
- RSI (14): {indicators.get('rsi', 'N/A')}
- MACD: {indicators.get('macd', 'N/A')}
- MACD Signal: {indicators.get('macd_signal', 'N/A')}
- MACD Histogram: {indicators.get('macd_histogram', 'N/A')}
- ATR: {indicators.get('atr', 'N/A')}
- Support: {indicators.get('support', 'N/A')}
- Resistance: {indicators.get('resistance', 'N/A')}
- Trend: {indicators.get('trend', 'N/A')}
- Volatility: {indicators.get('volatility', 'N/A')}

⏰ Timeframe: {timeframe}"""
        
        # 🚀 PROMPTS محسّنة حسب نوع الوكيل
        
        prompts = {
            'technical': f"""
🎯 You are a professional gold (XAU/USD) technical analyst.

{price_info}

🔍 Full analysis:
- Relative strength (RSI, MACD)
- Trend direction (EMAs)
- Support and resistance levels
- Volatility (ATR)

📋 Reply in JSON only (accurate and concise), in English:
{{
    "signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "reasoning": "reason for the recommendation",
    "entry_zone": "entry zone",
    "stop_loss": "SL (ATR-based)",
    "take_profit_1": "TP1 (ATR × 2)",
    "take_profit_2": "TP2 (ATR × 3.5)",
    "risk_reward": "1:X"
}}
""",
            
            'smc': f"""
🎯 You are a professional Smart Money Concepts analyst.

{price_info}

🔍 SMC analysis:
- Higher Highs / Higher Lows
- Order Blocks (supply/demand blocks)
- Liquidity Zones
- Break of Structure

📋 Reply in JSON only, in English:
{{
    "structure": "Bullish|Bearish|Neutral",
    "trend_stage": "Accumulation|Distribution|Continuation",
    "order_blocks": ["demand block", "supply block"],
    "liquidity_zones": ["liquidity zone"],
    "signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "reasoning": "reason for the analysis"
}}
""",
            
            'classical': f"""
🎯 You are a professional Japanese candlestick-pattern analyst.

{price_info}

🔍 Reversal patterns:
- Hammer, Engulfing, Morning Star
- Shooting Star, Dark Cloud, Evening Star

📋 Reply in JSON only, in English:
{{
    "candlestick_pattern": "pattern name",
    "pattern_direction": "Bullish|Bearish|Neutral",
    "reliability": "High|Medium|Low",
    "signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "reasoning": "pattern explanation"
}}
""",
            
            'price_action': f"""
🎯 You are a professional Price Action analyst.

{price_info}

🔍 Price action analysis:
- Supply & Demand Zones
- Swing Highs/Lows
- Fair Value Gaps
- Break of Candle Structure

📋 Reply in JSON only, in English:
{{
    "key_zones": {{"demand": "##", "supply": "##"}},
    "swing_analysis": "swing analysis",
    "signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "reasoning": "reason for the recommendation",
    "entry_trigger": "entry trigger"
}}
""",
            
            'multitimeframe': f"""
🎯 You are a multi-timeframe analyst.

{price_info}

🔍 Timeframe comparison:
- 4H: overall trend
- 1H: intermediate trend
- 15m/5m: entry point

📋 Reply in JSON only, in English:
{{
    "h4_trend": "Bullish|Bearish|Neutral",
    "h1_trend": "Bullish|Bearish|Neutral",
    "m15_trend": "Bullish|Bearish|Neutral",
    "alignment": "Fully_Aligned|Not_Aligned",
    "signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "reasoning": "reason for alignment/divergence"
}}
""",
            
            'news_risk': f"""
🎯 You are a professional geopolitical risk analyst.

{price_info}

🔍 Influencing factors:
- USD and Fed news
- Inflation and economic data
- Geopolitical events

📋 Reply in JSON only, in English:
{{
    "risk_level": "High|Medium|Low",
    "key_factors": ["factor 1", "factor 2"],
    "gold_sentiment": "Bullish|Bearish|Neutral",
    "confidence_adjustment": "-20 to +20",
    "recommendation": "BUY|SELL|WAIT"
}}
""",
            
            'risk_management': f"""
🎯 You are a risk-management expert.

{price_info}

📋 Settings:
- Account: $10,000
- Risk: 1-2%
- ATR: {indicators.get('atr', 'N/A')}

📋 Reply in JSON only, in English:
{{
    "risk_per_trade_percent": "1% or 2%",
    "position_size": "position size",
    "stop_loss_pips": "SL points",
    "take_profit_1_pips": "TP1 points",
    "risk_reward_ratio": "1:X",
    "assessment": "Safe|Moderate|High_Risk"
}}
""",
            
            'decision': f"""
🎯 You are a final-decision expert.

{price_info}

🎯 Requirements:
- At least 3 agents agree
- 60% agreement

📋 Reply in JSON only (accurate), in English:
{{
    "final_signal": "BUY|SELL|WAIT",
    "confidence": 0-100,
    "consensus_strength": "Strong|Moderate|Weak",
    "reasoning": "reason for the decision",
    "risk_assessment": "Acceptable|High|Rejected",
    "entry_zone": "entry zone",
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
        """استدعاء GroqCloud API (OpenAI-compatible).

        Groq is the final decision gate in One-Agent + Groq mode, so a single
        transient failure here means a missed trading signal entirely. Retries
        with exponential backoff on timeouts/connection errors/429/5xx, the
        same pattern MarketDataService uses for Twelve Data. Auth/bad-request
        errors (401/403/400) are not retried since retrying cannot fix them.
        """

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

        max_attempts = 3
        last_error = ""

        for attempt in range(max_attempts):
            try:
                response = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=35,
                )

                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {response.status_code}"
                    logger.warning(
                        f"⚠️ Groq attempt {attempt + 1}/{max_attempts} failed ({last_error}), retrying..."
                    )
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return AIResponse(success=False, content="", error=last_error, provider="groq", model=self.model)

                result = response.json()

                if not response.ok:
                    # Non-retryable client error (401/403/400/...): fail fast.
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

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_error = str(e)
                logger.warning(
                    f"⚠️ Groq attempt {attempt + 1}/{max_attempts} failed (timeout/connection): {e}"
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error(f"❌ خطأ Groq بعد {max_attempts} محاولات: {e}")
                return AIResponse(success=False, content="", error=last_error, provider="groq", model=self.model)

            except Exception as e:
                # Non-network errors (bad JSON, unexpected shape, etc.) are not retried.
                logger.error(f"❌ خطأ Groq: {e}")
                return AIResponse(success=False, content="", error=str(e), provider="groq", model=self.model)

        return AIResponse(success=False, content="", error=last_error or "unknown error", provider="groq", model=self.model)

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