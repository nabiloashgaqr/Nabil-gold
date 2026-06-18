"""
🤖 Decision Agent - Gold AI Signals
وكيل اتخاذ القرار النهائي المدعوم بالذكاء الاصطناعي والتعلم الذكي
"""

import logging
from typing import Dict, List, Any, Optional
from collections import Counter
from .base_agent import BaseAgent

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
        
        # 1️⃣ تجميع أصوات الوكلاء (مع weights متعلمة)
        votes = self._collect_votes(agents_results)
        
        # 2️⃣ التحليل الكلاسيكي للقرارات
        classic_decision = self._classic_decision(votes)
        
        # 3️⃣ التحليل بالذكاء الاصطناعي (async)
        ai_decision = {}
        if self.ai_service:
            ai_decision = await self._ai_decision(
                votes, price_data, indicators, session_info
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
            'rejection_reason': rejection_reason
        }
    
    async def _ai_decision(
        self,
        votes: Dict,
        price_data: Dict,
        indicators: Dict,
        session_info: Dict
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

{learning_summary}

معلومات الجلسة:
- الجودة: {session_quality}
- مسموح بالتداول: {trading_allowed}

أجب بصيغة JSON فقط وبدون Markdown:
{{
    "final_signal": "BUY" أو "SELL" أو "WAIT",
    "confidence": 0-100,
    "consensus_strength": "Strong" أو "Moderate" أو "Weak",
    "reasoning": "سبب القرار المختصر",
    "risk_reward": "نسبة المخاطرة/العائد",
    "market_bias": "Bullish أو Bearish أو Neutral مع سبب قصير",
    "entry_reason": "لماذا الدخول مناسب أو غير مناسب الآن",
    "opposite_risk": "لماذا لا نأخذ الاتجاه المعاكس",
    "risk_notes": "أهم مخاطر الصفقة",
    "action_plan": "خطة التعامل: دخول/انتظار/إلغاء",
    "quality_notes": ["نقطة قوة 1", "نقطة ضعف أو تحذير 1"]
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

        risk = agents_results.get('risk', {}) or {}
        if signal in {'BUY', 'SELL'} and risk and not risk.get('approved', False):
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

    def _to_trade_decision(self, analysis: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Convert analysis output to the canonical payload expected by DB/Telegram."""
        final_signal = str(analysis.get('signal', 'WAIT')).upper()
        risk = context.get('risk', {}) or {}
        current_price = context.get('current_price')
        signal_payload: Dict[str, Any] = {}

        if final_signal in {'BUY', 'SELL'}:
            entry_info = risk.get('entry', {}) or {}
            entry_zone = entry_info.get('zone', {}) or {}
            sl = risk.get('stop_loss', {}) or {}
            tp = risk.get('take_profit', {}) or {}
            tp1 = tp.get('tp1', {}) or {}
            tp2 = tp.get('tp2', {}) or {}
            entry_price = entry_info.get('price') or current_price
            signal_payload = {
                'type': final_signal,
                'entry': {
                    'price': entry_price,
                    'low': entry_zone.get('low', entry_price),
                    'high': entry_zone.get('high', entry_price),
                },
                'stop_loss': sl.get('price', 0),
                'tp1': tp1.get('price', 0),
                'tp2': tp2.get('price', 0),
                'rr_ratio': tp2.get('rr_ratio', tp1.get('rr_ratio', 0)),
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
            'ai': analysis.get('ai', {}),
            'learning': analysis.get('learning', {}),
            'risk': risk,
            'risk_assessment': analysis.get('risk_assessment', {}),
            'session_info': context.get('session', {}),
            'news': context.get('news', {}),
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