"""
🤖 Decision Agent - Gold AI Signals
وكيل اتخاذ القرار النهائي المدعوم بالذكاء الاصطناعي والتعلم الذكي
"""

import logging
from typing import Dict, List, Any, Optional
from collections import Counter
from .base_agent import BaseAgent
from services.memory_rules import format_memory_rules_for_prompt
from services.agent_playbooks import format_agent_playbooks_for_prompt

logger = logging.getLogger(__name__)


class DecisionAgent(BaseAgent):
    """
    🤖 وكيل اتخاذ القرار
    
    يجمع آراء جميع الوكلاء:
    - الوكلاء الكلاسيكيين (technical, smc, price_action...)
    - الوكلاء المدعومين بالـ AI
    
    ثم يستخدم AI + التعلم الذكي لاتخاذ القرار النهائي
    
    🧠 نظام التعلم:
    - يقيم أداء الوكلاء يومياً
    - يزيد وزن الفائزين
    - يقلل وزن الخاسرين
    - يتعلم من الأخطاء
    """
    
    def __init__(self, config: Dict, ai_service=None, learning_service=None):
        super().__init__(config)
        self.ai_service = ai_service
        self.learning_service = learning_service
        self.min_confidence = config.get('risk_settings', {}).get('min_confidence', 60)
        self.min_rr_ratio = config.get('risk_settings', {}).get('min_rr_ratio', 1.5)
        
        # متطلبات الإشارة الجديدة
        signal_req = config.get('signal_requirements', {})
        self.min_agents_agree = signal_req.get('min_agents_agree', 3)
        self.min_agreement_pct = signal_req.get('min_agreement_percentage', 60)
        self.allow_all_signals = signal_req.get('allow_all_signals', True)
        
        # الأوزان الافتراضية (تحدث بواسطة التعلم)
        self.default_weights = {
            'technical': 0.20,
            'classical': 0.20,
            'smc': 0.25,
            'price_action': 0.15,
            'multitimeframe': 0.15,
            'news_risk': 0.15
        }
        
        # تحميل الأوزان المتعلمة
        self.current_weights = self._load_weights()
        self.voting_agents = {"technical", "classical", "smc", "price_action", "multitimeframe"}
        
    def _load_weights(self) -> Dict[str, float]:
        """تحميل الأوزان (من learning service أو config)"""
        
        # محاولة تحميل من config
        config_weights = self.config.get('agent_weights', {})
        
        if config_weights:
            return config_weights.copy()
        
        return self.default_weights.copy()
    
    def update_weights(self, new_weights: Dict[str, float]):
        """تحديث الأوزان بناءً على التعلم"""
        self.current_weights = new_weights.copy()
        logger.info(f"🔄 تم تحديث أوزان الوكلاء: {new_weights}")
    
    def get_adjusted_confidence(self, agent_name: str, base_confidence: float) -> float:
        """
        🧠 تعديل الثقة بناءً على أداء الوكيل
        
        إذا كان الوكيل يتعلم جيداً → زيادة الثقة
        إذا كان الوكيل يتراجع → تقليل الثقة
        """
        
        if not self.learning_service:
            return base_confidence
        
        # الحصول على توصية التعلم
        recommendation = self.learning_service.get_agent_recommendation(agent_name)
        
        if recommendation == "INCREASE_CONFIDENCE":
            # زيادة الثقة بنسبة 10%
            return min(base_confidence * 1.1, 95)
        elif recommendation == "DECREASE_CONFIDENCE":
            # تقليل الثقة بنسبة 10%
            return max(base_confidence * 0.9, 50)
        
        return base_confidence
    
    def analyze(self, data: Dict) -> Dict[str, Any]:
        """
        🎯 اتخاذ القرار النهائي (sync version)
        """
        
        agents_results = data.get('all_agents_results', data)
        price_data = data.get('price_data') or data
        indicators = data.get('indicators', {})
        session_info = data.get('session', data.get('session_info', {}))
        memory_rules = agents_results.get('memory_rules', []) if isinstance(agents_results, dict) else []
        daily_bias = agents_results.get('daily_bias', {}) if isinstance(agents_results, dict) else {}
        news_ai = agents_results.get('news_ai', {}) if isinstance(agents_results, dict) else {}
        dynamic_risk = agents_results.get('dynamic_risk', {}) if isinstance(agents_results, dict) else {}
        
        # 1️⃣ تجميع أصوات الوكلاء (مع weights متعلمة)
        votes = self._collect_votes(agents_results)
        
        # 2️⃣ التحليل الكلاسيكي للقرارات
        classic_decision = self._classic_decision(votes)
        
        # 3️⃣ القرار النهائي (بدون AI async)
        final_signal, final_confidence, reasoning = self._final_decision(
            classic_decision, {}, session_info
        )
        
        # 4️⃣ إضافة معلومات التعلم
        learning_info = self._get_learning_info()
        
        result = {
            'agent': 'decision',
            'signal': final_signal,
            'decision': final_signal,
            'confidence': final_confidence,
            'reasoning': reasoning,
            'votes': votes,
            'weights': self.current_weights.copy(),
            'classic': classic_decision,
            'ai': {'available': False, 'reason': 'sync mode'},
            'learning': learning_info,
            'risk_assessment': self._assess_risk(final_signal, indicators),
            'timestamp': self.now_iso()
        }
        return self._apply_safety_filters(result, agents_results)
    
    async def analyze_async(self, data: Dict) -> Dict[str, Any]:
        """
        🎯 اتخاذ القرار النهائي (async version مع AI + Learning)
        """
        
        agents_results = data.get('all_agents_results', data)
        price_data = data.get('price_data') or data
        indicators = data.get('indicators', {})
        session_info = data.get('session', data.get('session_info', {}))
        memory_rules = agents_results.get('memory_rules', []) if isinstance(agents_results, dict) else []
        daily_bias = agents_results.get('daily_bias', {}) if isinstance(agents_results, dict) else {}
        news_ai = agents_results.get('news_ai', {}) if isinstance(agents_results, dict) else {}
        dynamic_risk = agents_results.get('dynamic_risk', {}) if isinstance(agents_results, dict) else {}
        
        # 1️⃣ تجميع أصوات الوكلاء (مع weights متعلمة)
        votes = self._collect_votes(agents_results)
        
        # 2️⃣ التحليل الكلاسيكي للقرارات
        classic_decision = self._classic_decision(votes)
        
        # 3️⃣ التحليل بالذكاء الاصطناعي (async)
        ai_decision = {}
        if self.ai_service:
            ai_decision = await self._ai_decision(
                votes, price_data, indicators, session_info, memory_rules, daily_bias, news_ai, dynamic_risk, agents_results
            )
        
        # 4️⃣ القرار النهائي
        final_signal, final_confidence, reasoning = self._final_decision(
            classic_decision, ai_decision, session_info
        )
        
        # 5️⃣ معلومات التعلم
        learning_info = self._get_learning_info()
        
        result = {
            'agent': 'decision',
            'signal': final_signal,
            'decision': final_signal,
            'confidence': final_confidence,
            'reasoning': reasoning,
            'votes': votes,
            'weights': self.current_weights.copy(),
            'classic': classic_decision,
            'ai': ai_decision,
            'learning': learning_info,
            'risk_assessment': self._assess_risk(final_signal, indicators),
            'timestamp': self.now_iso()
        }
        return self._apply_safety_filters(result, agents_results)
    
    def _collect_votes(self, agents_results: Dict) -> Dict:
        """
        🗳️ تجميع أصوات الوكلاء (مع weights متعلمة)
        """
        
        votes = {
            'BUY': [],
            'SELL': [],
            'WAIT': []
        }
        
        for agent_name, result in agents_results.items():
            if agent_name not in self.voting_agents:
                continue
            # دعم 'signal' و 'direction'
            if isinstance(result, dict):
                signal = result.get('signal', result.get('direction', 'WAIT'))
                confidence = result.get('confidence', 50)
            else:
                continue
            
            # الحصول على weight (متعلم أو افتراضي)
            weight = self.current_weights.get(agent_name, 0.15)
            
            # 🧠 تعديل الثقة بناءً على التعلم
            adjusted_confidence = self.get_adjusted_confidence(agent_name, confidence)
            
            # وزن الثقة مع وزن الوكيل
            weighted_score = (adjusted_confidence / 100) * weight
            
            if signal in votes:
                votes[signal].append({
                    'agent': agent_name,
                    'confidence': confidence,
                    'adjusted_confidence': adjusted_confidence,
                    'weight': weight,
                    'score': weighted_score,
                    'learning_adjusted': confidence != adjusted_confidence
                })
        
        return votes
    
    def _reliability_grade(self, confidence: float, weight: float, agent_name: str) -> str:
        """Human-readable reliability grade for a single agent experimental signal."""
        weighted = confidence * max(weight, 0.01)
        if confidence >= 80 and weighted >= 15:
            return "A"
        if confidence >= 70:
            return "B"
        if confidence >= 60:
            return "C"
        if confidence >= 45:
            return "D"
        return "E"

    def _classic_decision(self, votes: Dict) -> Dict:
        """
        📊 القرار الكلاسيكي (بدون AI)
        
        🔥 المتطلبات الجديدة:
        - 3 وكلاء كحد أدنى يوافقون
        - نسبة توافق فوق 60%
        """
        
        # حساب مجموع النقاط لكل إشارة
        buy_score = sum(v['score'] for v in votes['BUY'])
        sell_score = sum(v['score'] for v in votes['SELL'])
        
        # عدد الوكلاء لكل إشارة
        buy_count = len(votes['BUY'])
        sell_count = len(votes['SELL'])
        
        # حساب نسبة التوافق
        total_agents = buy_count + sell_count + len(votes['WAIT'])
        total_voting = buy_count + sell_count  # فقط من صوتوا BUY أو SELL
        
        buy_agreement_pct = (buy_count / total_voting * 100) if total_voting > 0 else 0
        sell_agreement_pct = (sell_count / total_voting * 100) if total_voting > 0 else 0
        
        # 🔥 المنطق الجديد: 3 وكلاء + 60% توافق
        decision = 'WAIT'
        confidence = 50
        rejection_reason = None
        
        # تحليل BUY
        if buy_count >= self.min_agents_agree and buy_agreement_pct >= self.min_agreement_pct:
            if buy_score > sell_score:
                decision = 'BUY'
                confidence = min(buy_score * 100, 95)
        
        # تحليل SELL
        elif sell_count >= self.min_agents_agree and sell_agreement_pct >= self.min_agreement_pct:
            if sell_score >= buy_score:
                decision = 'SELL'
                confidence = min(sell_score * 100, 95)
        
        # أسباب الرفض
        else:
            if total_voting < self.min_agents_agree:
                rejection_reason = f"لا يوجد عدد كافٍ من الوكلاء ({total_voting}/{self.min_agents_agree})"
            elif max(buy_agreement_pct, sell_agreement_pct) < self.min_agreement_pct:
                max_agreement = max(buy_count, sell_count)
                rejection_reason = f"نسبة التوافق منخفضة ({max_agreement}/{total_voting} = {max(buy_agreement_pct, sell_agreement_pct):.0f}% < {self.min_agreement_pct}%)"
        
        # تحديد أقوى وكيل
        all_votes = votes['BUY'] + votes['SELL'] + votes['WAIT']
        strongest_agent = max(all_votes, key=lambda x: x['score'], default=None)
        directional_votes = votes['BUY'] + votes['SELL']
        strongest_directional = max(directional_votes, key=lambda x: x['score'], default=None)
        experimental_source = None
        exp_cfg = self.config.get('signal_requirements', {}).get('experimental_single_agent', {}) or {}
        exp_enabled = bool(exp_cfg.get('enabled', False))
        min_single_conf = float(exp_cfg.get('min_confidence', 1) or 1)
        if exp_enabled and decision == 'WAIT' and strongest_directional and float(strongest_directional.get('adjusted_confidence', 0)) >= min_single_conf:
            experimental_signal = 'BUY' if strongest_directional in votes['BUY'] else 'SELL'
            reliability = self._reliability_grade(
                float(strongest_directional.get('adjusted_confidence', strongest_directional.get('confidence', 0))),
                float(strongest_directional.get('weight', 0.0)),
                str(strongest_directional.get('agent', 'unknown')),
            )
            decision = experimental_signal
            confidence = float(strongest_directional.get('adjusted_confidence', strongest_directional.get('confidence', 50)))
            rejection_reason = f"EXPERIMENTAL_SINGLE_AGENT: {strongest_directional.get('agent')}"
            experimental_source = {
                'enabled': True,
                'agent': strongest_directional.get('agent'),
                'signal': experimental_signal,
                'confidence': round(float(strongest_directional.get('confidence', 0)), 1),
                'adjusted_confidence': round(float(strongest_directional.get('adjusted_confidence', strongest_directional.get('confidence', 0))), 1),
                'weight': strongest_directional.get('weight'),
                'score': round(float(strongest_directional.get('score', 0)), 3),
                'reliability_grade': reliability,
                'reason': 'تم تفعيل وضع التجربة: إشارة من أقوى وكيل واحد بغض النظر عن نسبة التوافق',
            }
        
        return {
            'decision': decision,
            'confidence': confidence,
            'buy_score': buy_score,
            'sell_score': sell_score,
            'buy_count': buy_count,
            'sell_count': sell_count,
            'buy_agreement_pct': round(buy_agreement_pct, 1),
            'sell_agreement_pct': round(sell_agreement_pct, 1),
            'total_voting_agents': total_voting,
            'strongest_agent': strongest_agent['agent'] if strongest_agent else None,
            'experimental_single_agent': experimental_source,
            'rejection_reason': rejection_reason
        }
    
    def _format_agent_context_for_ai(self, agents_results: Dict[str, Any]) -> str:
        """Format detailed agent outputs for Groq so it cannot answer generically."""
        sections: List[str] = []
        for name in ['technical', 'classical', 'smc', 'price_action', 'multitimeframe', 'daily_bias', 'risk', 'news', 'news_ai', 'dynamic_risk']:
            result = agents_results.get(name, {}) or {}
            if not isinstance(result, dict):
                continue
            sections.append(f"\n## {name}")
            if name == 'technical':
                t = result.get('technical', {}) or {}
                sections.extend([
                    f"signal={result.get('signal')} confidence={result.get('confidence')}",
                    f"trend={t.get('trend')} score={t.get('classic_score')} RSI={t.get('rsi')} RSI7={t.get('rsi_7')} divergence={t.get('rsi_divergence')}",
                    f"MACD={t.get('macd')} hist={t.get('macd_histogram')} slope={t.get('macd_histogram_slope')}",
                    f"EMA ribbon={t.get('ema_ribbon')} EMA values={t.get('ema_values')}",
                    f"Bollinger={t.get('bollinger')} ATR={t.get('atr')} regime={t.get('market_regime')}",
                    f"levels={t.get('key_levels')} reasons={t.get('reasons')}",
                ])
            elif name == 'classical':
                sections.extend([
                    f"direction={result.get('direction')} confidence={result.get('confidence')}",
                    f"support={result.get('support_levels')} resistance={result.get('resistance_levels')}",
                    f"patterns={result.get('patterns_detected')} trendline={result.get('trendline')}",
                    f"scenarios={result.get('scenarios')} signals={result.get('signals')}",
                ])
            elif name == 'smc':
                sections.extend([
                    f"direction={result.get('direction')} confidence={result.get('confidence')}",
                    f"structure={result.get('market_structure')}",
                    f"order_blocks={result.get('order_blocks')}",
                    f"liquidity={result.get('liquidity')}",
                    f"fvg={result.get('fvg')} zone={result.get('zone')} entry_suggestion={result.get('entry_suggestion')}",
                    f"signals={result.get('signals')}",
                ])
            elif name == 'price_action':
                sections.extend([
                    f"direction={result.get('direction')} confidence={result.get('confidence')} role={result.get('role')}",
                    f"patterns={result.get('candle_patterns')}",
                    f"candle_analysis={result.get('candle_analysis')}",
                    f"breakout={result.get('breakout_analysis')} rejection={result.get('rejection')}",
                    f"signals={result.get('signals')}",
                ])
            elif name == 'multitimeframe':
                sections.extend([
                    f"direction={result.get('direction')} confidence={result.get('confidence')} alignment={result.get('alignment')} score={result.get('alignment_score')}",
                    f"setup_type={result.get('setup_type')} counter_trend={result.get('counter_trend')} htf={result.get('trend_direction_from_htf')}",
                    f"weighted_bias={result.get('weighted_bias')} conflicts={result.get('conflicts')} warnings={result.get('warnings')}",
                    f"tf_analysis={result.get('timeframe_analysis')}",
                ])
            elif name == 'risk':
                sections.extend([
                    f"approved={result.get('approved')} rejection={result.get('rejection_reason')} direction={result.get('direction')}",
                    f"entry={result.get('entry')} SL={result.get('stop_loss')} TP={result.get('take_profit')}",
                    f"risk_metrics={result.get('risk_metrics')} grade={result.get('trade_grade')}",
                    f"position_size={result.get('position_size')}",
                ])
            else:
                sections.append(str(result)[:3000])
        return "\n".join(sections)[:18000]

    def _generic_ai_reasoning(self, ai: Dict[str, Any]) -> bool:
        """Detect weak/generic Groq explanations."""
        text = " ".join(str(ai.get(k, '')) for k in ['reasoning', 'entry_reason', 'opposite_risk', 'risk_notes', 'action_plan'])
        generic_phrases = [
            'الوكلاء يوصون', 'لا يوجد سبب', 'لا يوجد مخاطر', 'مخاطر بيع الذهب',
            'الدخول الآن', 'الاتجاه المعاكس', 'لا يوجد قوة محددة', 'لا يوجد ضعف'
        ]
        return any(p in text for p in generic_phrases) or len(text.strip()) < 80

    async def _ai_decision(
        self,
        votes: Dict,
        price_data: Dict,
        indicators: Dict,
        session_info: Dict,
        memory_rules: List[Dict] | None = None,
        daily_bias: Dict | None = None,
        news_ai: Dict | None = None,
        dynamic_risk: Dict | None = None,
        agents_results: Dict[str, Any] | None = None
    ) -> Dict:
        """
        🤖 القرار بالذكاء الاصطناعي
        """
        
        if not self.ai_service:
            return {'available': False, 'error': 'AI service not initialized'}
        
        try:
            # بناء prompt مع كل البيانات
            votes_summary = self._format_votes_for_ai(votes)
            
            session_quality = session_info.get('quality', session_info.get('session_quality', 'UNKNOWN'))
            trading_allowed = session_info.get('trading_allowed', False)
            
            # إضافة معلومات التعلم للـ AI
            learning_summary = ""
            if self.learning_service and self.learning_service.learning_history:
                last = self.learning_service.learning_history[-1]
                learning_summary = f"""
الأوزان الحالية (متعلمة):
{self._format_weights_for_ai(last.adjusted_weights)}
"""
            
            prompt = f"""
أنت خبير التداول في Gold AI Signals.
ادمج تحليلات الوكلاء واتخذ القرار النهائي.

إحصائيات الوكلاء:
- شراء: {len(votes['BUY'])} وكلاء
- بيع: {len(votes['SELL'])} وكلاء
- انتظار: {len(votes['WAIT'])} وكلاء

تفاصيل الأصوات:
{self._format_votes_for_ai(votes)}

تفاصيل تحليل الوكلاء الكاملة، استخدم أرقامها ومستوياتها في شرحك ولا تكتفِ بعبارة "الوكلاء يوصون":
{self._format_agent_context_for_ai(agents_results or {})}

{learning_summary}

معلومات الجلسة:
- الجودة: {session_quality}
- مسموح بالتداول: {trading_allowed}

Daily Bias / الاتجاه الأعلى:
{daily_bias or {}}

تفسير Groq للأخبار:
{news_ai or {}}

Dynamic Risk Management / قيود المخاطرة الديناميكية:
{dynamic_risk or {}}

Agent Playbooks v3.0 / قواعد عمل كل وكيل حسب تخصصه:
{format_agent_playbooks_for_prompt()}

قواعد الذاكرة من أخطاء سابقة (التزم بها قدر الإمكان، وإذا خالفتها اجعل القرار WAIT أو اخفض الثقة):
{format_memory_rules_for_prompt(memory_rules or [])}

أجب بصيغة JSON فقط وبدون Markdown:
{{
    "final_signal": "BUY" أو "SELL" أو "WAIT",
    "confidence": 0-100,
    "consensus_strength": "Strong" أو "Moderate" أو "Weak",
    "reasoning": "سبب القرار المختصر مع ذكر 2-3 أدلة رقمية أو مستويات محددة",
    "risk_reward": "نسبة المخاطرة/العائد من بيانات RiskManagement",
    "market_bias": "Bullish أو Bearish أو Neutral مع السبب من DailyBias/MTF",
    "entry_reason": "سبب الدخول باستخدام أرقام فعلية مثل السعر، SL/TP، R:R، فريم، نمط، OB/FVG، EMA/RSI/MACD",
    "opposite_risk": "سبب رفض الاتجاه المعاكس بأدلة محددة وليس عبارة عامة",
    "risk_notes": "مخاطر محددة: أخبار، قرب دعم/مقاومة، ضعف فريم، تذبذب، تعارض وكلاء",
    "action_plan": "دخول/انتظار/إلغاء + شرط الإلغاء أو invalidation scenario",
    "evidence": ["دليل رقمي أو فني 1", "دليل رقمي أو فني 2", "دليل رقمي أو فني 3"],
    "invalidation": "ما الذي يجعل القرار خاطئاً؟ مستوى أو شرط واضح",
    "alternative_scenario": "متى يصبح الاتجاه المعاكس أفضل؟",
    "quality_notes": ["نقطة قوة محددة", "نقطة ضعف أو تحذير محدد"]
}}
"""

            # استخدم prompt القرار المخصص الذي يحتوي أصوات الوكلاء، بدلاً من prompt عام.
            if hasattr(self.ai_service, '_call_ai'):
                response = await self.ai_service._call_ai(prompt, 'decision')
            else:
                response = await self.ai_service.analyze_chart(
                    symbol=price_data.get('symbol', 'XAUUSD'),
                    price_data=price_data,
                    technical_indicators=indicators,
                    timeframe=price_data.get('timeframe', '1h'),
                    agent_type='decision'
                )
            
            if response.success:
                parsed = self.ai_service.parse_json_response(response.content)
                
                if parsed:
                    return {
                        'available': True,
                        'signal': parsed.get('final_signal', parsed.get('signal', 'WAIT')),
                        'confidence': parsed.get('confidence', 50),
                        'consensus_strength': parsed.get('consensus_strength', 'Unknown'),
                        'reasoning': parsed.get('reasoning', ''),
                        'risk_reward': parsed.get('risk_reward', ''),
                        'market_bias': parsed.get('market_bias', ''),
                        'entry_reason': parsed.get('entry_reason', ''),
                        'opposite_risk': parsed.get('opposite_risk', ''),
                        'risk_notes': parsed.get('risk_notes', ''),
                        'action_plan': parsed.get('action_plan', ''),
                        'evidence': parsed.get('evidence', []),
                        'invalidation': parsed.get('invalidation', ''),
                        'alternative_scenario': parsed.get('alternative_scenario', ''),
                        'quality_notes': parsed.get('quality_notes', []),
                        'provider': response.provider,
                        'model': getattr(response, 'model', ''),
                        'tokens_used': response.tokens_used,
                        'cost': response.cost
                    }
            
            return {'available': False, 'error': response.error or 'AI response parsing failed'}
            
        except Exception as e:
            logger.error(f"❌ خطأ في قرار AI: {e}")
            return {'available': False, 'error': str(e)}
    
    def _format_votes_for_ai(self, votes: Dict) -> str:
        """تنسيق الأصوات لـ AI"""
        
        lines = []
        
        for signal in ['BUY', 'SELL', 'WAIT']:
            agents = votes.get(signal, [])
            if agents:
                lines.append(f"\n{signal}:")
                for agent in agents:
                    lines.append(
                        f"  - {agent['agent']}: "
                        f"ثقة {agent['confidence']}% "
                        f"(وزن {agent['weight']*100:.0f}%)"
                    )
        
        return "\n".join(lines) if lines else "لا توجد أصوات"
    
    def _format_weights_for_ai(self, weights: Dict) -> str:
        """تنسيق الأوزان لـ AI"""
        lines = []
        for name, weight in weights.items():
            lines.append(f"  - {name}: {weight*100:.0f}%")
        return "\n".join(lines)
    
    def _final_decision(
        self,
        classic: Dict,
        ai: Dict,
        session_info: Dict
    ) -> tuple:
        """
        🎯 القرار النهائي
        
        🚀 التحقق من allow_signals:
        - إذا كانت session = Report Session → لا إرسال إشارات
        """
        
        # 🚀 التحقق من allow_signals من session_info
        allow_signals = session_info.get('allow_signals', True)
        current_session = session_info.get('current_session', 'Unknown')
        
        # إذا الجلسة不允许 الإشارات (جلسة التقارير مثلاً)
        if not allow_signals:
            return 'WAIT', 0, f"جلسة التقارير ({current_session}) - لا إرسال إشارات"
        
        # التحقق من جلسة التداول
        if not session_info.get('trading_allowed'):
            return 'WAIT', 0, "خارج ساعات التداول"
        
        session_quality = session_info.get('quality', session_info.get('session_quality', 'LOW'))

        # لا نخفض الحد الأدنى للثقة تحت قيمة config أبداً.
        # في السابق كانت جلسة HIGH تضرب الحد ×0.7، فكانت تسمح بإشارات ضعيفة مثل 41%.
        # الآن الحد الأدنى الحقيقي هو risk_settings.min_confidence، ويمكن رفعه للجلسات الضعيفة فقط.
        quality_multipliers = {
            'BEST': 1.0,
            'HIGH': 1.0,
            'MEDIUM': 1.10,
            'LOW': 1.20,
            'NONE': 1.50,
        }

        min_conf = self.min_confidence * quality_multipliers.get(str(session_quality).upper(), 1.20)

        exp_source = classic.get('experimental_single_agent') or {}
        exp_cfg = self.config.get('signal_requirements', {}).get('experimental_single_agent', {}) or {}
        if exp_source and exp_cfg.get('bypass_groq_min_confidence', True):
            return classic.get('decision', 'WAIT'), round(float(classic.get('confidence', 0)), 1), (
                f"وضع تجريبي: إشارة {classic.get('decision')} من وكيل واحد "
                f"({exp_source.get('agent')}) بدرجة موثوقية {exp_source.get('reliability_grade')}"
            )

        ai_config = self.config.get('ai_service', {})
        ai_required = bool(ai_config.get('enabled', False)) and not bool(ai_config.get('fallback_to_classic', True))
        if ai_required and (not ai.get('available') or ai.get('error')):
            error = ai.get('error') or 'AI unavailable'
            return 'WAIT', 0, f"Groq إجباري لكن فشل AI: {error}"
        
        # دمج الكلاسيكي مع AI
        if ai.get('available'):
            ai_signal = ai.get('signal', 'WAIT')
            ai_conf = ai.get('confidence', 50)
            
            # 70% AI + 30% كلاسيكي
            if ai_signal != 'WAIT' and ai_conf >= min_conf:
                final_signal = ai_signal
                final_confidence = (ai_conf * 0.7) + (classic.get('confidence', 50) * 0.3)
                reasoning = ai.get('reasoning', classic.get('decision', 'N/A'))
            else:
                if ai_required:
                    final_signal = 'WAIT'
                    final_confidence = ai_conf
                    reasoning = f"Groq إجباري: قرار AI غير كافٍ أو WAIT (ثقة AI: {ai_conf}%)"
                else:
                    final_signal = classic.get('decision', 'WAIT')
                    final_confidence = classic.get('confidence', 50)
                    reasoning = f"قرار كلاسيكي - AI غير متوفر أو ثقة منخفضة"
        else:
            if ai_required:
                final_signal = 'WAIT'
                final_confidence = 0
                reasoning = "Groq إجباري لكن AI غير مفعّل أو غير متاح"
            else:
                final_signal = classic.get('decision', 'WAIT')
                final_confidence = classic.get('confidence', 50)
                reasoning = "قرار كلاسيكي - AI غير مفعّل"
        
        # التحقق من الحد الأدنى للثقة
        if final_confidence < min_conf:
            final_signal = 'WAIT'
            reasoning += f" (ثقة منخفضة: {final_confidence:.0f}% < {min_conf:.0f}%)"
        
        return final_signal, round(final_confidence, 1), reasoning
    
    def _get_learning_info(self) -> Dict:
        """معلومات التعلم للقرار"""
        
        info = {
            'enabled': self.learning_service is not None,
            'current_weights': self.current_weights.copy()
        }
        
        if self.learning_service and self.learning_service.learning_history:
            last_report = self.learning_service.learning_history[-1]
            info['last_update'] = last_report.report_date
            info['trades_analyzed'] = last_report.total_trades_analyzed
            info['overall_win_rate'] = last_report.overall_win_rate
        
        return info
    
    def _assess_risk(self, signal: str, indicators: Dict) -> Dict:
        """
        ⚠️ تقييم المخاطر
        """
        
        risk_factors = []
        risk_score = 0
        
        # RSI
        rsi = indicators.get('rsi', 50)
        if rsi > 75 or rsi < 25:
            risk_factors.append("RSI في منطقة ذروة")
            risk_score += 1
        
        # السبريد
        spread = indicators.get('spread', 0)
        if spread > 5:
            risk_factors.append(f"سبريد عالي: {spread}")
            risk_score += 1
        
        # ATR منخفض
        atr = indicators.get('atr', 0)
        if atr < 1.0:
            risk_factors.append("ATR منخفض - تقلب ضعيف")
            risk_score += 1
        
        # تقييم المخاطر
        if risk_score == 0:
            assessment = "مقبول ✅"
        elif risk_score == 1:
            assessment = "محتمل ⚠️"
        else:
            assessment = "عالي ❌"
        
        return {
            'score': risk_score,
            'assessment': assessment,
            'factors': risk_factors
        }
    

    def _apply_safety_filters(self, result: Dict[str, Any], agents_results: Dict[str, Any]) -> Dict[str, Any]:
        """Apply hard blockers after consensus: session, news, and risk approval."""
        warnings = list(result.get('warnings', []) or [])
        signal = str(result.get('signal', 'WAIT')).upper()

        session = agents_results.get('session', {}) or {}
        if session and not session.get('trading_allowed', True):
            warnings.append(f"Session blocked: {session.get('reason', 'outside trading hours')}")
            signal = 'WAIT'
        if session and not session.get('allow_signals', True):
            warnings.append(f"Signals disabled in current session: {session.get('current_session')}")
            signal = 'WAIT'

        news = agents_results.get('news', {}) or {}
        if news and (news.get('can_trade') is False or str(news.get('market_status', '')).upper() == 'DANGER'):
            warnings.append(f"News blocked: {news.get('summary', news.get('market_status', 'DANGER'))}")
            signal = 'WAIT'

        exp_source = (result.get('classic', {}) or {}).get('experimental_single_agent') or {}
        exp_cfg = self.config.get('signal_requirements', {}).get('experimental_single_agent', {}) or {}
        experimental_mode = bool(exp_source)

        # في الوضع التجريبي نبقي فقط: الوقت + الأخبار الخطيرة الأساسية + منع التكرار لاحقاً.
        # لذلك لا نجعل AI News directional أو Daily Bias أو RiskManagement يمنعون الإشارة التجريبية.
        news_ai = agents_results.get('news_ai', {}) or news.get('ai_interpretation', {}) or {}
        if not experimental_mode and news_ai and news_ai.get('available'):
            risk_level = str(news_ai.get('risk_level', '')).upper()
            allowed_direction = str(news_ai.get('allowed_direction', 'BOTH')).upper()
            block_trading = bool(news_ai.get('block_trading', False))
            if block_trading or allowed_direction == 'NONE' or risk_level == 'EXTREME':
                warnings.append(f"AI News blocked trading: {news_ai.get('reasoning', risk_level)}")
                signal = 'WAIT'
            elif signal in {'BUY', 'SELL'} and allowed_direction in {'BUY', 'SELL'} and signal != allowed_direction:
                warnings.append(f"AI News allows only {allowed_direction}: {news_ai.get('reasoning', '')}")
                signal = 'WAIT'

        daily_bias = agents_results.get('daily_bias', {}) or {}
        if not experimental_mode and signal in {'BUY', 'SELL'} and daily_bias.get('enabled', True):
            bias = str(daily_bias.get('bias', 'NEUTRAL')).upper()
            bias_conf = float(daily_bias.get('confidence') or 0)
            db_settings = self.config.get('daily_bias_filter', {}) or {}
            contrarian_min = float(db_settings.get('contrarian_min_confidence', 80) or 80)
            is_contrarian = (bias == 'BULLISH' and signal == 'SELL') or (bias == 'BEARISH' and signal == 'BUY')
            if is_contrarian and float(result.get('confidence') or 0) < contrarian_min:
                warnings.append(
                    f"Daily Bias يمنع صفقة عكس الاتجاه: bias={bias} ({bias_conf}%), signal={signal}, required_conf={contrarian_min}%"
                )
                signal = 'WAIT'

        risk = agents_results.get('risk', {}) or {}
        if signal in {'BUY', 'SELL'} and risk and not risk.get('approved', False):
            if experimental_mode and exp_cfg.get('bypass_risk_rejection', False):
                warnings.append(f"تجريبي: تم تجاوز رفض RiskManagement للمراقبة فقط: {risk.get('rejection_reason', 'not approved')}")
            else:
                warnings.append(f"Risk rejected: {risk.get('rejection_reason', 'not approved')}")
                signal = 'WAIT'

        if signal != result.get('signal'):
            reason = '; '.join(warnings[-3:]) or 'Safety filter blocked signal'
            result['reasoning'] = f"{result.get('reasoning', '')} | {reason}".strip(' |')
            result['confidence'] = 0 if signal == 'WAIT' else result.get('confidence', 0)

        result['signal'] = signal
        result['decision'] = signal
        result['warnings'] = warnings
        return result


    def _calculate_quality_score(self, analysis: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate a human-friendly signal quality score (0-100 + grade)."""
        confidence = float(analysis.get('confidence') or 0)
        risk = context.get('risk', {}) or {}
        news = context.get('news', {}) or {}
        session = context.get('session', {}) or {}
        classic = analysis.get('classic', {}) or {}
        signal = str(analysis.get('signal', 'WAIT')).upper()

        tp = risk.get('take_profit', {}) or {}
        tp2 = tp.get('tp2', {}) or {}
        rr = float(tp2.get('rr_ratio') or 0)

        agreement = 0.0
        if signal == 'BUY':
            agreement = float(classic.get('buy_agreement_pct') or 0)
        elif signal == 'SELL':
            agreement = float(classic.get('sell_agreement_pct') or 0)
        else:
            agreement = max(float(classic.get('buy_agreement_pct') or 0), float(classic.get('sell_agreement_pct') or 0))

        components: Dict[str, float] = {}
        components['confidence'] = min(confidence, 100) * 0.30
        components['agreement'] = min(agreement, 100) * 0.20
        components['risk_reward'] = min(max((rr / 3.0) * 20.0, 0), 20.0)
        components['risk_approved'] = 10.0 if risk.get('approved') else 0.0

        news_status = str(news.get('market_status', 'SAFE')).upper()
        if news_status == 'SAFE' and news.get('can_trade', True):
            components['news'] = 10.0
        elif news_status in {'CAUTION', 'HIGH_VOLATILITY'} and news.get('can_trade', True):
            components['news'] = 5.0
        else:
            components['news'] = 0.0

        session_quality = str(session.get('session_quality', session.get('quality', 'LOW'))).upper()
        session_points = {'BEST': 10.0, 'HIGH': 9.0, 'MEDIUM': 6.0, 'LOW': 3.0}.get(session_quality, 0.0)
        components['session'] = session_points if session.get('trading_allowed', True) else 0.0

        penalty = min(len(analysis.get('warnings', []) or []) * 4.0, 12.0)
        raw_score = max(0.0, min(100.0, sum(components.values()) - penalty))

        if raw_score >= 90:
            grade = 'A+'
            label = 'Elite'
        elif raw_score >= 80:
            grade = 'A'
            label = 'Strong'
        elif raw_score >= 70:
            grade = 'B'
            label = 'Good'
        elif raw_score >= 60:
            grade = 'C'
            label = 'Acceptable'
        else:
            grade = 'D'
            label = 'Weak'

        return {
            'score': round(raw_score, 1),
            'grade': grade,
            'label': label,
            'components': {k: round(v, 1) for k, v in components.items()},
            'penalty': round(penalty, 1),
            'rr_ratio': round(rr, 2),
            'agreement_pct': round(agreement, 1),
        }

    def _forced_observation_signal(self, context: Dict[str, Any]) -> Dict[str, Any] | None:
        """Create a clearly-labeled paper observation signal when all agents/Groq say WAIT.

        This is for monitoring only. It still respects session/news/halt safety because
        it is called after safety filters and checks warnings before forcing.
        """
        exp_cfg = self.config.get('signal_requirements', {}).get('experimental_single_agent', {}) or {}
        if not exp_cfg.get('force_signal_on_wait', False):
            return None

        session = context.get('session', {}) or {}
        news = context.get('news', {}) or {}
        if session and not session.get('trading_allowed', True):
            return None
        if news and (news.get('can_trade') is False or str(news.get('market_status', '')).upper() == 'DANGER'):
            return None

        source = None
        direction = None
        reliability = 'E'
        confidence = float(exp_cfg.get('force_signal_confidence', 45) or 45)

        daily_bias = context.get('daily_bias', {}) or {}
        bias = str(daily_bias.get('bias', '')).upper()
        if bias == 'BULLISH':
            direction, source, reliability = 'BUY', 'daily_bias', 'D'
            confidence = max(confidence, min(float(daily_bias.get('confidence') or confidence), 60))
        elif bias == 'BEARISH':
            direction, source, reliability = 'SELL', 'daily_bias', 'D'
            confidence = max(confidence, min(float(daily_bias.get('confidence') or confidence), 60))

        if direction is None:
            mtf = context.get('multitimeframe', {}) or {}
            mtf_dir = str((mtf.get('weighted_bias', {}) or {}).get('direction') or mtf.get('direction') or '').upper()
            if mtf_dir in {'BUY', 'SELL'}:
                direction, source, reliability = mtf_dir, 'multitimeframe', 'D'
                confidence = max(confidence, min(float(mtf.get('confidence') or confidence), 58))

        if direction is None:
            tech = context.get('technical', {}) or {}
            trend = str((tech.get('technical', {}) or {}).get('trend') or '').upper()
            if trend == 'UP':
                direction, source, reliability = 'BUY', 'technical_trend', 'E'
            elif trend == 'DOWN':
                direction, source, reliability = 'SELL', 'technical_trend', 'E'

        if direction is None:
            # Last-resort observation from current candle body.
            data = context.get('price_data') or context
            candles = data.get('data', []) if isinstance(data, dict) else []
            if candles:
                last = candles[-1]
                try:
                    direction = 'BUY' if float(last.get('close', 0)) >= float(last.get('open', 0)) else 'SELL'
                    source, reliability = 'last_candle', 'E'
                except Exception:
                    return None

        if direction not in {'BUY', 'SELL'}:
            return None

        return {
            'enabled': True,
            'forced': True,
            'agent': source or 'observation_fallback',
            'signal': direction,
            'confidence': round(confidence, 1),
            'adjusted_confidence': round(confidence, 1),
            'weight': 0.0,
            'score': 0.0,
            'reliability_grade': reliability,
            'reason': 'وضع مراقبة قسري: كل الوكلاء/Groq أعطوا WAIT، وتم إنشاء إشارة Paper لمراقبة النظام فقط',
        }

    def _to_trade_decision(self, analysis: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Convert analysis output to the canonical payload expected by DB/Telegram."""
        final_signal = str(analysis.get('signal', 'WAIT')).upper()
        risk = context.get('risk', {}) or {}
        current_price = context.get('current_price')
        signal_payload: Dict[str, Any] = {}
        forced_source = None
        if final_signal == 'WAIT' and not (analysis.get('warnings') or []):
            forced_source = self._forced_observation_signal(context)
            if forced_source:
                final_signal = forced_source['signal']
                analysis['confidence'] = forced_source['confidence']
                analysis['reasoning'] = forced_source['reason']
                analysis.setdefault('classic', {})['experimental_single_agent'] = forced_source

        if final_signal in {'BUY', 'SELL'}:
            entry_info = risk.get('entry', {}) or {}
            entry_zone = entry_info.get('zone', {}) or {}
            sl = risk.get('stop_loss', {}) or {}
            tp = risk.get('take_profit', {}) or {}
            tp1 = tp.get('tp1', {}) or {}
            tp2 = tp.get('tp2', {}) or {}
            entry_price = entry_info.get('price') or current_price
            stop_loss = sl.get('price', 0)
            tp1_price = tp1.get('price', 0)
            tp2_price = tp2.get('price', 0)
            rr_ratio = tp2.get('rr_ratio', tp1.get('rr_ratio', 0))

            exp_cfg = self.config.get('signal_requirements', {}).get('experimental_single_agent', {}) or {}
            exp_source = (analysis.get('classic', {}) or {}).get('experimental_single_agent') or {}
            if exp_source and exp_cfg.get('fallback_levels_enabled', True) and (not stop_loss or not tp1_price or not tp2_price):
                try:
                    atr = float(((context.get('technical', {}) or {}).get('technical', {}) or {}).get('atr') or 0)
                except Exception:
                    atr = 0.0
                atr = atr or float(exp_cfg.get('fallback_atr', 2.0) or 2.0)
                tp1_mult = float(exp_cfg.get('fallback_rr_tp1', 2.0) or 2.0)
                tp2_mult = float(exp_cfg.get('fallback_rr_tp2', 3.5) or 3.5)
                entry_price = entry_price or current_price or 0
                if final_signal == 'BUY':
                    stop_loss = float(entry_price) - atr * 1.5
                    tp1_price = float(entry_price) + atr * tp1_mult
                    tp2_price = float(entry_price) + atr * tp2_mult
                else:
                    stop_loss = float(entry_price) + atr * 1.5
                    tp1_price = float(entry_price) - atr * tp1_mult
                    tp2_price = float(entry_price) - atr * tp2_mult
                rr_ratio = round(abs(tp2_price - float(entry_price)) / max(abs(float(entry_price) - stop_loss), 0.01), 2)
                entry_zone = {'low': float(entry_price) - max(0.2, atr * 0.05), 'high': float(entry_price) + max(0.2, atr * 0.05)}

            signal_payload = {
                'type': final_signal,
                'entry': {
                    'price': entry_price,
                    'low': entry_zone.get('low', entry_price),
                    'high': entry_zone.get('high', entry_price),
                },
                'stop_loss': stop_loss,
                'tp1': tp1_price,
                'tp2': tp2_price,
                'rr_ratio': rr_ratio,
                'position_size': risk.get('position_size', {}),
                'risk_summary': risk.get('summary', ''),
            }

        reasons = [analysis.get('reasoning', '')]
        if risk.get('summary'):
            reasons.append(risk.get('summary'))
        for warning in analysis.get('warnings', []) or []:
            reasons.append(warning)

        quality = self._calculate_quality_score(analysis, context)

        return {
            'decision': final_signal,
            'signal': signal_payload,
            'confidence': analysis.get('confidence', 0),
            'quality': quality,
            'current_price': current_price,
            'reasons': [r for r in reasons if r],
            'warnings': analysis.get('warnings', []),
            'votes': analysis.get('votes', {}),
            'weights': analysis.get('weights', {}),
            'classic': analysis.get('classic', {}),
            'experimental_single_agent': (analysis.get('classic', {}) or {}).get('experimental_single_agent'),
            'ai': analysis.get('ai', {}),
            'learning': analysis.get('learning', {}),
            'risk': risk,
            'risk_assessment': analysis.get('risk_assessment', {}),
            'session_info': context.get('session', {}),
            'news': context.get('news', {}),
            'news_ai': context.get('news_ai', {}),
            'daily_bias': context.get('daily_bias', {}),
            'dynamic_risk': context.get('dynamic_risk', {}),
            'summary': analysis.get('reasoning', ''),
            'timestamp': analysis.get('timestamp', self.now_iso()),
        }

    def decide(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Production-compatible sync decision payload for DB/Telegram."""
        analysis = self.analyze(data)
        return self._to_trade_decision(analysis, data.get('all_agents_results', data))

    async def decide_async(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Production-compatible async decision payload for DB/Telegram."""
        analysis = await self.analyze_async(data)
        return self._to_trade_decision(analysis, data.get('all_agents_results', data))

    def get_decision_message(self, result: Dict) -> str:
        """تنسيق رسالة القرار لتيليجرام"""
        
        signal = result.get('signal', 'WAIT')
        confidence = result.get('confidence', 0)
        reasoning = result.get('reasoning', '')
        votes = result.get('votes', {})
        classic = result.get('classic', {})
        ai = result.get('ai', {})
        learning = result.get('learning', {})
        risk = result.get('risk_assessment', {})
        weights = result.get('weights', {})
        
        signal_emoji = {
            'BUY': '🟢',
            'SELL': '🔴',
            'WAIT': '🟡'
        }.get(signal, '⚪')
        
        lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            f"{signal_emoji} *القرار النهائي*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 الإشارة: *{signal}*",
            f"🎯 الثقة: *{confidence}%*",
            ""
        ]
        
        # 🔥 الإحصائيات الجديدة
        buy_count = len(votes.get('BUY', []))
        sell_count = len(votes.get('SELL', []))
        total_voting = classic.get('total_voting_agents', buy_count + sell_count)
        
        # حساب نسبة التوافق
        if signal == 'BUY':
            agreement_pct = classic.get('buy_agreement_pct', 0)
        elif signal == 'SELL':
            agreement_pct = classic.get('sell_agreement_pct', 0)
        else:
            agreement_pct = max(classic.get('buy_agreement_pct', 0), classic.get('sell_agreement_pct', 0))
        
        lines.append("🔥 متطلبات التوافق:")
        lines.append(f"├ الوكلاء: {total_voting}/{self.min_agents_agree} ✅")
        lines.append(f"├ التوافق: {agreement_pct:.0f}% ✅")
        lines.append("")
        
        # الأوزان (متعلمة)
        if weights:
            lines.append("⚙️ الأوزان المتعلمة:")
            for name, w in sorted(weights.items(), key=lambda x: -x[1])[:5]:
                lines.append(f"├ {name}: {w*100:.0f}%")
            lines.append("")
        
        # الأصوات التفصيلية
        lines.append("🗳️ أصوات الوكلاء:")
        lines.append(f"├ شراء: {buy_count} ({classic.get('buy_agreement_pct', 0):.0f}%)")
        lines.append(f"├ بيع: {sell_count} ({classic.get('sell_agreement_pct', 0):.0f}%)")
        lines.append(f"└ انتظار: {len(votes.get('WAIT', []))}")
        lines.append("")
        
        # AI
        if ai.get('available'):
            lines.extend([
                f"🤖 AI: {ai.get('provider', 'AI')}",
                f"├ القوة: {ai.get('consensus_strength', 'N/A')}",
                f"└ R/R: {ai.get('risk_reward', 'N/A')}",
                ""
            ])
        
        # التعلم
        if learning.get('enabled'):
            lines.append("🧠 التعلم الذكي: ✅ مفعّل")
            if learning.get('overall_win_rate'):
                lines.append(f"├ Win Rate: {learning['overall_win_rate']:.1f}%")
            lines.append("")
        
        # المخاطر
        lines.extend([
            f"⚠️ المخاطر: {risk.get('assessment', 'N/A')}",
            f"📝 السبب: {reasoning[:80]}..."
            if len(reasoning) > 80 else f"📝 السبب: {reasoning}"
        ])
        
        # 🔥 سبب الرفض إن وجد
        rejection = classic.get('rejection_reason')
        if rejection and signal == 'WAIT':
            lines.append(f"❌ سبب الانتظار: {rejection}")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)