"""SMC / Smart Money Concepts Agent.

وكيل مفاهيم الأموال الذكية: يحلل بنية السوق، BOS/CHoCH، Order Blocks،
مناطق السيولة، Liquidity Sweeps، Fair Value Gaps و Premium/Discount Zones.
المنطق خوارزمي محافظ ومناسب للتشغيل داخل GitHub Actions بدون خدمات خارجية.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

from agents.base_agent import BaseAgent
from utils.indicators import calculate_atr, detect_swing_points

Candle = Dict[str, Any]


class SMCAgent(BaseAgent):
    """Analyze Smart Money Concepts and return a structured directional bias."""

    name = "smc"

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run full SMC analysis on the primary timeframe candles."""
        try:
            candles = market_data.get("data", [])
            if len(candles) < 60:
                return self._empty("Not enough data for SMC analysis")

            timeframe = str(market_data.get("timeframe", "15m"))
            current_price = self._f(candles[-1].get("close"))
            atr = self._last(calculate_atr(candles, 14), 1.5)
            tolerance = max(atr * 0.15, 0.60)
            recent = candles[-120:] if len(candles) > 120 else candles
            swings = detect_swing_points(recent, lookback=3)

            market_structure = self._market_structure(recent, swings, timeframe)
            order_blocks = self._detect_order_blocks(recent, atr, timeframe)
            liquidity = self._detect_liquidity(recent, swings, tolerance)
            fvg = self._detect_fvg(recent)
            zone, dealing_range = self._premium_discount_zone(recent, swings, current_price)

            score, signals = self._score_smc(
                trend=market_structure["trend"],
                current_price=current_price,
                order_blocks=order_blocks,
                liquidity=liquidity,
                fvg=fvg,
                zone=zone,
                atr=atr,
            )

            direction = "BUY" if score >= 4 else "SELL" if score <= -4 else "NEUTRAL"
            signal = direction if direction in {"BUY", "SELL"} else "WAIT"
            objective_direction = self._context_objective_direction(market_structure, liquidity, zone)
            confidence = self._confidence(score, liquidity.get("recent_sweep", {}).get("occurred", False), direction)
            entry_suggestion = self._entry_suggestion(direction if direction in {"BUY", "SELL"} else (objective_direction or "NEUTRAL"), current_price, atr, order_blocks, liquidity, dealing_range)
            setup_candidates = []
            for candidate_direction in self._candidate_direction_pool(direction, objective_direction):
                setup_candidates.extend(
                    self._build_setup_candidates(
                        symbol=str(market_data.get("symbol", "XAU/USD")),
                        timeframe=timeframe,
                        direction=candidate_direction,
                        current_price=current_price,
                        atr=atr,
                        confidence=confidence,
                        market_structure=market_structure,
                        liquidity=liquidity,
                        zone_context=zone,
                        objective_direction=objective_direction,
                        order_blocks=order_blocks,
                        fvg=fvg,
                        dealing_range=dealing_range,
                        entry_suggestion=entry_suggestion,
                        candles=recent,
                    )
                )
            setup_candidates = self._merge_setup_candidates(setup_candidates)
            setup_structure = setup_candidates[0] if setup_candidates else {
                "setup_type": "NONE",
                "setup_state": "DETECTED",
                "lead_agent": "smc",
                "setup_quality": {"grade": "D", "score": 0},
                "poi_type": None,
                "sweep_side": (liquidity.get("recent_sweep", {}) or {}).get("type"),
                "displacement_score": 0.0,
                "target_liquidity": None,
            }

            return {
                "agent": self.name,
                "direction": direction,
                "signal": signal,
                "confidence": confidence,
                "market_structure": market_structure,
                "order_blocks": order_blocks,
                "liquidity": liquidity,
                "fvg": fvg,
                "zone": zone,
                "dealing_range": dealing_range,
                "signals": signals,
                "entry_suggestion": entry_suggestion,
                "setup_candidates": setup_candidates,
                "setup_structure": setup_structure,
                "summary": self._summary(direction, confidence, market_structure, liquidity, zone, signals),
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("SMC analysis failed")
            return self._empty(f"SMC failed: {exc}")

    def _market_structure(self, candles: List[Candle], swings: Dict[str, List[Dict[str, Any]]], timeframe: str) -> Dict[str, Any]:
        """Classify HH/HL/LH/LL and detect latest BOS/CHoCH."""
        highs = swings.get("highs", [])
        lows = swings.get("lows", [])
        combined: List[Dict[str, Any]] = []
        for point in highs:
            combined.append({"kind": "HIGH", **point})
        for point in lows:
            combined.append({"kind": "LOW", **point})
        combined.sort(key=lambda item: int(item.get("index", 0)))

        structure_points: List[Dict[str, Any]] = []
        previous_high: float | None = None
        previous_low: float | None = None
        for point in combined:
            price = self._f(point.get("price"))
            if point["kind"] == "HIGH":
                label = "H" if previous_high is None else "HH" if price > previous_high else "LH"
                previous_high = price
            else:
                label = "L" if previous_low is None else "HL" if price > previous_low else "LL"
                previous_low = price
            structure_points.append({"type": label, "price": round(price, 2), "time": point.get("time")})

        last_highs = [p for p in structure_points if p["type"] in {"H", "HH", "LH"}][-3:]
        last_lows = [p for p in structure_points if p["type"] in {"L", "HL", "LL"}][-3:]
        trend = "RANGING"
        if last_highs and last_lows:
            high_type = last_highs[-1]["type"]
            low_type = last_lows[-1]["type"]
            if high_type == "HH" and low_type == "HL":
                trend = "BULLISH"
            elif high_type == "LH" and low_type == "LL":
                trend = "BEARISH"
            else:
                # Fallback slope from last closes if swings are mixed.
                closes = [self._f(c.get("close")) for c in candles[-20:]]
                if len(closes) >= 2 and closes[-1] > closes[0] * 1.002:
                    trend = "BULLISH"
                elif len(closes) >= 2 and closes[-1] < closes[0] * 0.998:
                    trend = "BEARISH"

        current_close = self._f(candles[-1].get("close"))
        last_swing_high = highs[-1] if highs else None
        last_swing_low = lows[-1] if lows else None
        last_bos = None
        last_choch = None
        if last_swing_high and current_close > self._f(last_swing_high.get("price")):
            last_bos = {"type": "bullish", "level": round(self._f(last_swing_high.get("price")), 2), "timeframe": timeframe}
            if trend == "BEARISH":
                last_choch = {"type": "bullish", "level": last_bos["level"], "timeframe": timeframe}
                trend = "BULLISH"
        elif last_swing_low and current_close < self._f(last_swing_low.get("price")):
            last_bos = {"type": "bearish", "level": round(self._f(last_swing_low.get("price")), 2), "timeframe": timeframe}
            if trend == "BULLISH":
                last_choch = {"type": "bearish", "level": last_bos["level"], "timeframe": timeframe}
                trend = "BEARISH"

        recent_span = self._avg_range(candles[-20:]) or max(abs(current_close - self._f(candles[max(len(candles) - 20, 0)].get("close"))), 0.5)
        structure_strength = 0.0
        if last_bos:
            structure_strength = abs(current_close - float(last_bos.get("level") or current_close)) / max(recent_span, 0.01)
        elif trend in {"BULLISH", "BEARISH"}:
            structure_strength = abs(current_close - self._f(candles[max(len(candles) - 20, 0)].get("close"))) / max(recent_span, 0.01)
        structure_quality = "STRONG" if structure_strength >= 1.5 else "MODERATE" if structure_strength >= 0.75 else "WEAK"

        return {
            "trend": trend,
            "last_bos": last_bos,
            "last_choch": last_choch,
            "structure_points": structure_points[-10:],
            "structure_strength": round(structure_strength, 2),
            "structure_quality": structure_quality,
        }

    def _detect_order_blocks(self, candles: List[Candle], atr: float, timeframe: str) -> List[Dict[str, Any]]:
        """Detect bullish/bearish order blocks before impulsive moves."""
        blocks: List[Dict[str, Any]] = []
        if len(candles) < 12:
            return blocks

        for index in range(2, len(candles) - 4):
            candle = candles[index]
            open_price = self._f(candle.get("open"))
            close_price = self._f(candle.get("close"))
            high = self._f(candle.get("high"))
            low = self._f(candle.get("low"))
            next_3 = candles[index + 1 : index + 4]
            future_close = self._f(next_3[-1].get("close"))
            impulse = abs(future_close - close_price)
            if impulse < max(atr * 1.20, 1.20):
                continue

            bearish_candle = close_price < open_price
            bullish_candle = close_price > open_price
            if bearish_candle and future_close > high + atr * 0.30:
                zone = {"top": round(open_price, 2), "bottom": round(low, 2)}
                block_type = "bullish"
            elif bullish_candle and future_close < low - atr * 0.30:
                zone = {"top": round(high, 2), "bottom": round(open_price, 2)}
                block_type = "bearish"
            else:
                continue

            mitigation = self._mitigation_status(candles[index + 4 :], zone)
            mitigated = mitigation["status"] in {"MITIGATED", "INVALIDATED"}
            displacement_quality = "STRONG" if impulse >= atr * 2.0 else "MODERATE" if impulse >= atr * 1.5 else "WEAK"
            strength = "strong" if displacement_quality == "STRONG" and mitigation["status"] == "FRESH" else "medium" if displacement_quality in {"STRONG", "MODERATE"} and mitigation["status"] != "INVALIDATED" else "weak"
            equilibrium = (zone["top"] + zone["bottom"]) / 2
            blocks.append(
                {
                    "type": block_type,
                    "zone": zone,
                    "equilibrium": round(equilibrium, 2),
                    "mitigated": mitigated,
                    "mitigation_status": mitigation["status"],
                    "touches": mitigation["touches"],
                    "invalidated": mitigation["status"] == "INVALIDATED",
                    "strength": strength,
                    "displacement_quality": displacement_quality,
                    "displacement_atr": round(impulse / max(atr, 0.01), 2),
                    "timeframe": timeframe,
                    "created_at": candle.get("time"),
                    "impulse_points": round(impulse * 10, 1),
                }
            )

        # Prefer recent unmitigated zones but keep context zones too.
        unmitigated = [b for b in blocks if not b["mitigated"]]
        selected = (unmitigated[-4:] if unmitigated else blocks[-4:])
        return selected

    def _detect_liquidity(self, candles: List[Candle], swings: Dict[str, List[Dict[str, Any]]], tolerance: float) -> Dict[str, Any]:
        """Find equal highs/lows, session/day liquidity pools and recent sweeps."""
        highs = swings.get("highs", [])
        lows = swings.get("lows", [])
        equal_highs_detail = self._cluster_liquidity_details([self._f(p.get("price")) for p in highs], tolerance)
        equal_lows_detail = self._cluster_liquidity_details([self._f(p.get("price")) for p in lows], tolerance)
        equal_highs = [cluster["level"] for cluster in equal_highs_detail]
        equal_lows = [cluster["level"] for cluster in equal_lows_detail]

        previous_day_levels = self._previous_day_levels(candles)
        session_liquidity = self._session_liquidity(candles)
        buy_side = self._unique_levels(
            equal_highs
            + [self._f(p.get("price")) for p in highs[-4:]]
            + [self._f(previous_day_levels.get("high"))]
            + [self._f(session_liquidity.get("high"))]
        )
        sell_side = self._unique_levels(
            equal_lows
            + [self._f(p.get("price")) for p in lows[-4:]]
            + [self._f(previous_day_levels.get("low"))]
            + [self._f(session_liquidity.get("low"))]
        )
        recent_sweep = self._recent_sweep(candles, tolerance, previous_day_levels, session_liquidity)

        return {
            "buy_side": buy_side[-8:],
            "sell_side": sell_side[:8],
            "equal_highs": equal_highs,
            "equal_lows": equal_lows,
            "equal_highs_detail": equal_highs_detail,
            "equal_lows_detail": equal_lows_detail,
            "previous_day_levels": previous_day_levels,
            "session_liquidity": session_liquidity,
            "recent_sweep": recent_sweep,
        }

    def _detect_fvg(self, candles: List[Candle]) -> List[Dict[str, Any]]:
        """Detect Fair Value Gaps using the standard 3-candle imbalance rule."""
        gaps: List[Dict[str, Any]] = []
        for index in range(2, len(candles)):
            c1 = candles[index - 2]
            c3 = candles[index]
            c1_high = self._f(c1.get("high"))
            c1_low = self._f(c1.get("low"))
            c3_high = self._f(c3.get("high"))
            c3_low = self._f(c3.get("low"))
            if c1_high < c3_low:
                zone = {"top": round(c3_low, 2), "bottom": round(c1_high, 2)}
                size = zone["top"] - zone["bottom"]
                filled = any(self._f(c.get("low")) <= zone["bottom"] for c in candles[index + 1 :])
                partial = any(self._f(c.get("low")) <= zone["top"] for c in candles[index + 1 :]) and not filled
                strength = "strong" if size >= self._avg_range(candles[max(0, index-20):index]) * 0.7 else "medium" if size > 0 else "weak"
                gaps.append({"type": "bullish", "zone": zone, "size": round(size, 2), "strength": strength, "filled": filled, "partial_fill": partial, "created_at": c3.get("time")})
            elif c1_low > c3_high:
                zone = {"top": round(c1_low, 2), "bottom": round(c3_high, 2)}
                size = zone["top"] - zone["bottom"]
                filled = any(self._f(c.get("high")) >= zone["top"] for c in candles[index + 1 :])
                partial = any(self._f(c.get("high")) >= zone["bottom"] for c in candles[index + 1 :]) and not filled
                strength = "strong" if size >= self._avg_range(candles[max(0, index-20):index]) * 0.7 else "medium" if size > 0 else "weak"
                gaps.append({"type": "bearish", "zone": zone, "size": round(size, 2), "strength": strength, "filled": filled, "partial_fill": partial, "created_at": c3.get("time")})
        return gaps[-6:]

    def _premium_discount_zone(
        self,
        candles: List[Candle],
        swings: Dict[str, List[Dict[str, Any]]],
        current_price: float,
    ) -> Tuple[str, Dict[str, float]]:
        """Calculate premium/discount against the latest meaningful range."""
        highs = swings.get("highs", [])
        lows = swings.get("lows", [])
        if highs and lows:
            range_high = max(self._f(p.get("price")) for p in highs[-5:])
            range_low = min(self._f(p.get("price")) for p in lows[-5:])
        else:
            recent = candles[-50:]
            range_high = max(self._f(c.get("high")) for c in recent)
            range_low = min(self._f(c.get("low")) for c in recent)
        midpoint = (range_high + range_low) / 2
        equilibrium_band = max((range_high - range_low) * 0.05, 0.50)
        if current_price > midpoint + equilibrium_band:
            zone = "PREMIUM"
        elif current_price < midpoint - equilibrium_band:
            zone = "DISCOUNT"
        else:
            zone = "EQUILIBRIUM"
        range_span = max(range_high - range_low, 0.01)
        position_pct = (current_price - range_low) / range_span
        return zone, {
            "high": round(range_high, 2),
            "low": round(range_low, 2),
            "midpoint": round(midpoint, 2),
            "premium_boundary": round(midpoint + equilibrium_band, 2),
            "discount_boundary": round(midpoint - equilibrium_band, 2),
            "current_position_pct": round(position_pct, 3),
        }

    def _score_smc(
        self,
        trend: str,
        current_price: float,
        order_blocks: List[Dict[str, Any]],
        liquidity: Dict[str, Any],
        fvg: List[Dict[str, Any]],
        zone: str,
        atr: float,
    ) -> Tuple[float, List[str]]:
        """Translate SMC evidence into a directional score."""
        score = 0.0
        signals: List[str] = []

        if trend == "BULLISH":
            score += 2.0
            signals.append("Market structure is bullish")
        elif trend == "BEARISH":
            score -= 2.0
            signals.append("Market structure is bearish")

        structure_quality = str((liquidity.get("market_structure") or {}).get("structure_quality") or "")
        sweep = liquidity.get("recent_sweep", {}) or {}
        if sweep.get("occurred") and sweep.get("type") == "sell_side":
            add = 3.8 if sweep.get("confirmation") == "STRONG" else 2.8 if sweep.get("confirmation") == "MODERATE" else 1.8
            score += add
            source = str(sweep.get("reference_type") or "liquidity").replace("_", " ")
            signals.append(f"Sweep below {source} detected ({sweep.get('confirmation')}) - bullish reversal context")
        elif sweep.get("occurred") and sweep.get("type") == "buy_side":
            sub = 3.8 if sweep.get("confirmation") == "STRONG" else 2.8 if sweep.get("confirmation") == "MODERATE" else 1.8
            score -= sub
            source = str(sweep.get("reference_type") or "liquidity").replace("_", " ")
            signals.append(f"Sweep above {source} detected ({sweep.get('confirmation')}) - bearish reversal context")

        for block in order_blocks:
            zone_obj = block.get("zone", {})
            if not self._price_in_or_near_zone(current_price, zone_obj, atr * 0.35):
                continue
            if block.get("invalidated"):
                continue
            status = block.get("mitigation_status", "FRESH")
            strength_points = 2.4 if block.get("strength") == "strong" else 1.5 if block.get("strength") == "medium" else 0.7
            if status == "TESTED":
                strength_points *= 0.85
            if block.get("type") == "bullish" and not block.get("mitigated"):
                score += strength_points
                signals.append(f"Price reacting near {status} bullish Order Block ({block.get('displacement_quality')})")
            elif block.get("type") == "bearish" and not block.get("mitigated"):
                score -= strength_points
                signals.append(f"Price reacting near {status} bearish Order Block ({block.get('displacement_quality')})")

        active_fvg = [gap for gap in fvg if not gap.get("filled")]
        for gap in active_fvg[-3:]:
            if self._price_in_or_near_zone(current_price, gap.get("zone", {}), atr * 0.25):
                fvg_points = 1.4 if gap.get("strength") == "strong" else 1.0
                if gap.get("partial_fill"):
                    fvg_points *= 0.75
                if gap.get("type") == "bullish":
                    score += fvg_points
                    signals.append(f"Price near {gap.get('strength')} bullish FVG")
                elif gap.get("type") == "bearish":
                    score -= fvg_points
                    signals.append(f"Price near {gap.get('strength')} bearish FVG")

        if trend == "BULLISH" and zone in {"DISCOUNT", "EQUILIBRIUM"}:
            score += 1.2
            signals.append("Bullish structure with discount/equilibrium pricing")
        elif trend == "BEARISH" and zone in {"PREMIUM", "EQUILIBRIUM"}:
            score -= 1.2
            signals.append("Bearish structure with premium/equilibrium pricing")
        elif zone == "PREMIUM":
            score -= 0.5
        elif zone == "DISCOUNT":
            score += 0.5

        pd = liquidity.get("previous_day_levels", {}) or {}
        if sweep.get("occurred") and sweep.get("reference_type") == "previous_day_high":
            score -= 0.8
            signals.append("Previous-day high liquidity was swept")
        elif sweep.get("occurred") and sweep.get("reference_type") == "previous_day_low":
            score += 0.8
            signals.append("Previous-day low liquidity was swept")
        session_ref = liquidity.get("session_liquidity", {}) or {}
        if session_ref.get("label") and sweep.get("reference_type") in {"session_high", "session_low"}:
            signals.append(f"Session liquidity reference: {session_ref.get('label')}")

        return score, signals

    def _context_objective_direction(
        self,
        market_structure: Dict[str, Any],
        liquidity: Dict[str, Any],
        zone: str,
    ) -> str | None:
        trend = str((market_structure or {}).get("trend") or "").upper()
        recent_sweep = (liquidity.get("recent_sweep") or {}) if isinstance(liquidity, dict) else {}
        sweep_type = str(recent_sweep.get("type") or "")
        zone = str(zone or "").upper()
        if trend == "BULLISH" and sweep_type == "sell_side":
            return "BUY"
        if trend == "BEARISH" and sweep_type == "buy_side":
            return "SELL"
        if trend == "BULLISH" and zone == "DISCOUNT":
            return "BUY"
        if trend == "BEARISH" and zone == "PREMIUM":
            return "SELL"
        return None

    @staticmethod
    def _candidate_direction_pool(score_direction: str, objective_direction: str | None) -> List[str]:
        directions: List[str] = []
        if score_direction in {"BUY", "SELL"}:
            directions.append(score_direction)
        if objective_direction in {"BUY", "SELL"} and objective_direction not in directions:
            directions.append(objective_direction)
        return directions

    @staticmethod
    def _merge_setup_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in sorted(candidates or [], key=lambda c: float(c.get("thesis_dominance_score") or 0), reverse=True):
            key = str(candidate.get("state_key") or candidate.get("id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(candidate)
        return merged

    def _entry_suggestion(
        self,
        direction: str,
        current_price: float,
        atr: float,
        order_blocks: List[Dict[str, Any]],
        liquidity: Dict[str, Any],
        dealing_range: Dict[str, float],
    ) -> Dict[str, Any]:
        """Build SMC-style entry, SL and target suggestion."""
        if direction not in {"BUY", "SELL"}:
            return {
                "type": "NEUTRAL",
                "reason": "Wait for a clear liquidity sweep or a return to an Order Block/FVG",
                "entry": round(current_price, 2),
                "sl": None,
                "tp": None,
            }

        relevant_blocks = [b for b in order_blocks if b.get("type") == ("bullish" if direction == "BUY" else "bearish")]
        block = relevant_blocks[-1] if relevant_blocks else None
        buffer = max(atr * 0.25, 0.50)
        if direction == "BUY":
            entry = current_price
            sl = dealing_range.get("low", current_price - atr * 1.8) - buffer
            if block:
                zone = block.get("zone", {})
                entry = min(current_price, mean([self._f(zone.get("top")), self._f(zone.get("bottom"))]))
                sl = self._f(zone.get("bottom")) - buffer
            targets = [x for x in liquidity.get("buy_side", []) if x > current_price]
            tp = min(targets) if targets else current_price + atr * 2.8
            reason = "Buy after liquidity sweep / bullish structure from Discount or Order Block"
        else:
            entry = current_price
            sl = dealing_range.get("high", current_price + atr * 1.8) + buffer
            if block:
                zone = block.get("zone", {})
                entry = max(current_price, mean([self._f(zone.get("top")), self._f(zone.get("bottom"))]))
                sl = self._f(zone.get("top")) + buffer
            targets = [x for x in liquidity.get("sell_side", []) if x < current_price]
            tp = max(targets) if targets else current_price - atr * 2.8
            reason = "Sell after liquidity sweep / bearish structure from Premium or Order Block"

        return {"type": direction, "reason": reason, "entry": round(entry, 2), "sl": round(sl, 2), "tp": round(tp, 2)}

    def _build_setup_candidates(
        self,
        symbol: str,
        timeframe: str,
        direction: str,
        current_price: float,
        atr: float,
        confidence: int,
        market_structure: Dict[str, Any],
        liquidity: Dict[str, Any],
        order_blocks: List[Dict[str, Any]],
        fvg: List[Dict[str, Any]],
        dealing_range: Dict[str, float],
        entry_suggestion: Dict[str, Any],
        candles: List[Candle],
        zone_context: str = "",
        objective_direction: str | None = None,
    ) -> List[Dict[str, Any]]:
        """Return ranked SMC setup candidates with PRIMARY/STANDBY roles.

        Stage A foundation:
        - score every eligible POI by quality
        - estimate probability that price will revisit it
        - derive thesis dominance
        - keep one PRIMARY pending thesis and (optionally) one STANDBY thesis
        """
        if direction not in {"BUY", "SELL"}:
            return []
        sweep = liquidity.get("recent_sweep", {}) or {}
        recent_candles = candles or []
        raw_candidates = self._poi_candidates(direction, current_price, atr, order_blocks, fvg, dealing_range)
        if not raw_candidates:
            return []

        selection_cfg = (self.config.get("smc_engine", {}) or {}).get("selection", {}) or {}
        max_candidates = int(selection_cfg.get("max_candidates", 5) or 5)
        target_liquidity = self._target_liquidity(direction, current_price, liquidity)
        setup_candidates: List[Dict[str, Any]] = []

        for idx, poi in enumerate(raw_candidates[:max_candidates], start=1):
            trigger = self._trigger_signal(direction, poi, recent_candles, current_price, atr)
            poi = dict(poi)
            poi["trigger"] = trigger
            setup_type = self._setup_type_from_context(direction, sweep, market_structure, poi)
            setup_state = self._setup_state_from_context(current_price, atr, poi, sweep)
            quality = self._setup_quality(
                confidence=confidence,
                sweep=sweep,
                poi=poi,
                market_structure=market_structure,
                current_price=current_price,
                atr=atr,
                setup_state=setup_state,
            )
            poi_quality_score = float(quality.get("score") or 0)
            return_probability_score = self._return_probability_score(
                poi=poi,
                direction=direction,
                current_price=current_price,
                atr=atr,
                market_structure=market_structure,
                liquidity=liquidity,
                all_candidates=raw_candidates,
            )
            thesis_dominance_score = self._thesis_dominance_score(
                poi_quality_score=poi_quality_score,
                return_probability_score=return_probability_score,
                trigger_score=float(trigger.get("score") or 0),
                trigger_ready=bool(trigger.get("market_ready")),
                sweep=sweep,
                poi=poi,
            )
            expected_revisit_window = self._expected_revisit_window(return_probability_score)
            created_at = str(
                poi.get("created_at")
                or sweep.get("time")
                or ((market_structure.get("last_bos") or {}).get("time"))
                or ""
            )
            zone = poi.get("zone") or {}
            zone_top = round(self._f(zone.get("top")), 2) if zone else 0.0
            zone_bottom = round(self._f(zone.get("bottom")), 2) if zone else 0.0
            state_key = (
                f"SMC_STATE::{symbol}::{timeframe}::{direction}::{setup_type}::"
                f"{poi.get('poi_type') or 'none'}::{sweep.get('type') or 'nosweep'}::{zone_top:.2f}:{zone_bottom:.2f}"
            )
            candidate_id = f"SMC::{symbol}::{timeframe}::{direction}::{created_at or 'now'}::{setup_type}::{idx}"
            midpoint = (zone_top + zone_bottom) / 2.0 if zone_top and zone_bottom else current_price
            candidate_entry = midpoint if midpoint > 0 else self._f(entry_suggestion.get("entry"), current_price)
            objective_alignment = (
                "ALIGNED_WITH_OBJECTIVE"
                if objective_direction in {"BUY", "SELL"} and objective_direction == direction
                else "COUNTER_OBJECTIVE"
                if objective_direction in {"BUY", "SELL"} and objective_direction != direction
                else "NEUTRAL_OBJECTIVE"
            )
            candidate = {
                "id": candidate_id,
                "state_key": state_key,
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "setup_type": setup_type,
                "setup_state": setup_state,
                "lead_agent": "smc",
                "setup_quality": quality,
                "quality_grade": quality.get("grade"),
                "quality_score": quality.get("score"),
                "poi_type": poi.get("poi_type"),
                "poi_zone": zone,
                "poi_low": round(self._f((zone or {}).get("bottom")), 2) if zone else None,
                "poi_high": round(self._f((zone or {}).get("top")), 2) if zone else None,
                "poi_rank_score": round(float(poi.get("rank_score") or 0), 1),
                "poi_rank_reasons": list(poi.get("rank_reasons") or []),
                "poi_quality_score": round(poi_quality_score, 1),
                "return_probability_score": round(return_probability_score, 1),
                "thesis_dominance_score": round(thesis_dominance_score, 1),
                "expected_revisit_window": expected_revisit_window,
                "trigger_state": trigger.get("state"),
                "trigger_score": trigger.get("score"),
                "trigger_ready": bool(trigger.get("market_ready")),
                "entry_timing": trigger.get("timing"),
                "execution_hint": trigger.get("execution_hint"),
                "entry_price": round(candidate_entry, 2),
                "stop_loss": round(self._candidate_stop_loss(direction, poi, atr, entry_suggestion), 2),
                "target_price": round(self._f(target_liquidity or entry_suggestion.get("tp")), 2) if (target_liquidity or entry_suggestion.get("tp")) else None,
                "target_liquidity": round(self._f(target_liquidity), 2) if target_liquidity else None,
                "sweep_side": sweep.get("type"),
                "sweep_confirmation": sweep.get("confirmation"),
                "displacement_score": round(float(poi.get("displacement_score") or 0.0), 2),
                "displacement_quality": poi.get("displacement_quality"),
                "confidence": confidence,
                "entry_reason": entry_suggestion.get("reason"),
                "objective_direction": objective_direction,
                "objective_alignment": objective_alignment,
                "source": "smc",
                "is_active": True,
                "created_at": created_at,
                "details": {
                    "market_trend": market_structure.get("trend"),
                    "structure_quality": market_structure.get("structure_quality"),
                    "recent_sweep": sweep,
                    "zone_context": zone_context,
                    "objective_direction": objective_direction,
                    "objective_alignment": objective_alignment,
                    "poi": poi,
                    "trigger": trigger,
                    "dealing_range": dealing_range,
                    "selection": {
                        "poi_quality_score": round(poi_quality_score, 1),
                        "return_probability_score": round(return_probability_score, 1),
                        "thesis_dominance_score": round(thesis_dominance_score, 1),
                        "expected_revisit_window": expected_revisit_window,
                    },
                },
            }
            candidate["priority_score"] = self._setup_candidate_priority_score(
                candidate,
                current_price=current_price,
                zone_context=zone_context,
                objective_direction=objective_direction,
            )
            setup_candidates.append(candidate)

        setup_candidates = self._expand_objective_same_box_ladder(
            setup_candidates,
            direction=direction,
            current_price=current_price,
            atr=atr,
            objective_direction=objective_direction,
        )
        setup_candidates.sort(
            key=lambda c: (
                float(c.get("priority_score") or 0),
                float(c.get("thesis_dominance_score") or 0),
                float(c.get("return_probability_score") or 0),
            ),
            reverse=True,
        )
        primary = setup_candidates[0] if setup_candidates else None
        standby_min = float(selection_cfg.get("standby_min_dominance_score", 42) or 42)
        standby_rel = float(selection_cfg.get("standby_min_relative_to_primary", 0.72) or 0.72)
        primary_score = float(primary.get("thesis_dominance_score") or 0) if primary else 0.0

        for rank, candidate in enumerate(setup_candidates, start=1):
            role = "REJECTED"
            if rank == 1:
                role = "PRIMARY"
            elif rank == 2:
                score = float(candidate.get("thesis_dominance_score") or 0)
                if score >= standby_min and (primary_score <= 0 or score >= primary_score * standby_rel):
                    role = "STANDBY"
            candidate["selection_rank"] = rank
            candidate["selection_role"] = role
            candidate["details"] = dict(candidate.get("details") or {})
            candidate["details"]["selection"] = {
                **(candidate["details"].get("selection") or {}),
                "selection_rank": rank,
                "selection_role": role,
                "primary_score": round(primary_score, 1),
                "standby_min_dominance_score": standby_min,
            }
        return setup_candidates

    def _setup_candidate_priority_score(
        self,
        candidate: Dict[str, Any],
        *,
        current_price: float,
        zone_context: str,
        objective_direction: str | None,
    ) -> float:
        direction = str(candidate.get("direction") or "").upper()
        setup_type = str(candidate.get("setup_type") or "").upper()
        mitigation = str((((candidate.get("details") or {}).get("poi") or {}).get("mitigation_status") or "")).upper()
        entry_price = self._f(candidate.get("entry_price"), current_price)
        trend = str((candidate.get("details") or {}).get("market_trend") or "").upper()
        trigger_state = str(candidate.get("trigger_state") or "").upper()
        score = (
            float(candidate.get("thesis_dominance_score") or 0) * 0.55
            + float(candidate.get("return_probability_score") or 0) * 0.25
            + float(candidate.get("trigger_score") or 0) * 0.20
        )
        if objective_direction in {"BUY", "SELL"} and objective_direction == direction:
            score += 10.0
            if (direction == "BUY" and entry_price < current_price) or (direction == "SELL" and entry_price > current_price):
                score += 8.0
            if mitigation == "FRESH":
                score += 4.0
            if setup_type in {"STRUCTURE_CONTINUATION", "ORDER_BLOCK_PULLBACK", "LIQUIDITY_REVERSAL"}:
                score += 4.0
        elif objective_direction in {"BUY", "SELL"} and objective_direction != direction:
            score -= 8.0
            if setup_type == "LIQUIDITY_REVERSAL" and trigger_state == "REJECTION_CONFIRMED":
                score += 6.0
        if trend == ("BULLISH" if direction == "BUY" else "BEARISH"):
            score += 3.0
        zone_context = str(zone_context or "").upper()
        if (direction == "BUY" and zone_context == "DISCOUNT") or (direction == "SELL" and zone_context == "PREMIUM"):
            score += 2.0
        return round(score, 2)

    def _expand_objective_same_box_ladder(
        self,
        candidates: List[Dict[str, Any]],
        *,
        direction: str,
        current_price: float,
        atr: float,
        objective_direction: str | None,
    ) -> List[Dict[str, Any]]:
        if direction not in {"BUY", "SELL"} or objective_direction != direction or not candidates:
            return candidates
        ordered = sorted(candidates, key=lambda c: float(c.get("priority_score") or 0), reverse=True)
        anchor = next(
            (
                c for c in ordered
                if str(c.get("objective_alignment") or "") == "ALIGNED_WITH_OBJECTIVE"
                and str((((c.get("details") or {}).get("poi") or {}).get("mitigation_status") or "")).upper() == "FRESH"
                and str(c.get("poi_type") or "") in {"order_block", "fvg"}
            ),
            None,
        )
        if not anchor:
            return candidates
        zone = anchor.get("poi_zone") or {}
        top = self._f(zone.get("top"), 0.0)
        bottom = self._f(zone.get("bottom"), 0.0)
        if top <= 0 or bottom <= 0:
            return candidates
        high = max(top, bottom)
        low = min(top, bottom)
        width = high - low
        if width < max(atr * 0.8, 1.2):
            return candidates
        separation = min(max(atr * 0.05, 0.05), width * 0.12)
        mid = (high + low) / 2.0
        if width <= separation * 2:
            return candidates
        primary = dict(anchor)
        standby = dict(anchor)
        ladder_parent_id = str(anchor.get("id") or anchor.get("state_key") or "SMC_LADDER")
        if direction == "BUY":
            primary_zone = {"top": round(high, 2), "bottom": round(mid + separation, 2)}
            standby_zone = {"top": round(mid - separation, 2), "bottom": round(low, 2)}
            primary_entry = round(high, 2)
            standby_entry = round(mid - separation, 2)
        else:
            primary_zone = {"top": round(mid - separation, 2), "bottom": round(low, 2)}
            standby_zone = {"top": round(high, 2), "bottom": round(mid + separation, 2)}
            primary_entry = round(low, 2)
            standby_entry = round(mid + separation, 2)
        primary.update({
            "id": f"{ladder_parent_id}::MAIN",
            "state_key": f"{str(anchor.get('state_key') or ladder_parent_id)}::MAIN",
            "poi_zone": primary_zone,
            "poi_low": round(min(primary_zone['top'], primary_zone['bottom']), 2),
            "poi_high": round(max(primary_zone['top'], primary_zone['bottom']), 2),
            "entry_price": primary_entry,
            "selection_role": "PRIMARY",
            "selection_rank": 1,
            "priority_score": round(float(anchor.get("priority_score") or 0) + 4.0, 2),
        })
        standby.update({
            "id": f"{ladder_parent_id}::ADD",
            "state_key": f"{str(anchor.get('state_key') or ladder_parent_id)}::ADD",
            "poi_zone": standby_zone,
            "poi_low": round(min(standby_zone['top'], standby_zone['bottom']), 2),
            "poi_high": round(max(standby_zone['top'], standby_zone['bottom']), 2),
            "entry_price": standby_entry,
            "selection_role": "STANDBY",
            "selection_rank": 2,
            "priority_score": round(float(anchor.get("priority_score") or 0) + 1.5, 2),
            "thesis_dominance_score": round(max(0.0, float(anchor.get("thesis_dominance_score") or 0) - 4.0), 1),
            "return_probability_score": round(max(0.0, float(anchor.get("return_probability_score") or 0) - 2.0), 1),
        })
        for leg_name, candidate in (("PRIMARY", primary), ("STANDBY", standby)):
            candidate["details"] = dict(candidate.get("details") or {})
            candidate["details"]["selection"] = {
                **(candidate["details"].get("selection") or {}),
                "same_box_ladder": True,
                "ladder_parent_id": ladder_parent_id,
                "ladder_leg": leg_name,
            }
        remainder = [c for c in candidates if str(c.get("id") or "") != str(anchor.get("id") or "")]
        return [primary, standby] + remainder

    def _primary_poi(
        self,
        direction: str,
        current_price: float,
        atr: float,
        order_blocks: List[Dict[str, Any]],
        fvg: List[Dict[str, Any]],
        dealing_range: Dict[str, float],
        market_structure: Dict[str, Any],
        sweep: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        candidates = self._poi_candidates(direction, current_price, atr, order_blocks, fvg, dealing_range)
        if not candidates:
            return None
        ranked = sorted(
            [self._rank_poi_candidate(candidate, direction, current_price, atr, market_structure, sweep) for candidate in candidates],
            key=lambda candidate: float(candidate.get("rank_score") or 0),
            reverse=True,
        )
        best = ranked[0]
        best["alternatives"] = [
            {
                "poi_type": candidate.get("poi_type"),
                "rank_score": round(float(candidate.get("rank_score") or 0), 1),
                "rank_reasons": candidate.get("rank_reasons", [])[:3],
                "zone": candidate.get("zone", {}),
            }
            for candidate in ranked[1:4]
        ]
        return best

    def _poi_candidates(
        self,
        direction: str,
        current_price: float,
        atr: float,
        order_blocks: List[Dict[str, Any]],
        fvg: List[Dict[str, Any]],
        dealing_range: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        relevant_type = "bullish" if direction == "BUY" else "bearish"
        candidates: List[Dict[str, Any]] = []
        for block in order_blocks:
            if block.get("type") != relevant_type:
                continue
            zone = block.get("zone", {}) or {}
            if not zone:
                continue
            candidates.append({
                "poi_type": "order_block",
                "zone": zone,
                "strength": block.get("strength"),
                "created_at": block.get("created_at"),
                "mitigation_status": block.get("mitigation_status"),
                "displacement_score": float(block.get("displacement_atr") or 0) * 10.0,
                "displacement_quality": block.get("displacement_quality"),
                "near_price": self._price_in_or_near_zone(current_price, zone, atr * 0.35),
                "source_ref": block,
            })
        for gap in fvg:
            if gap.get("type") != relevant_type or gap.get("filled"):
                continue
            zone = gap.get("zone", {}) or {}
            if not zone:
                continue
            candidates.append({
                "poi_type": "fvg",
                "zone": zone,
                "strength": gap.get("strength"),
                "created_at": gap.get("created_at"),
                "mitigation_status": "PARTIAL" if gap.get("partial_fill") else "FRESH",
                "displacement_score": float(gap.get("size") or 0) * 5.0,
                "displacement_quality": str(gap.get("strength") or "medium").upper(),
                "near_price": self._price_in_or_near_zone(current_price, zone, atr * 0.25),
                "source_ref": gap,
            })
        if dealing_range.get("high") and dealing_range.get("low"):
            midpoint = (self._f(dealing_range.get("high")) + self._f(dealing_range.get("low"))) / 2
            zone = {
                "top": round(midpoint + max(atr * 0.20, 0.30), 2),
                "bottom": round(midpoint - max(atr * 0.20, 0.30), 2),
            }
            candidates.append({
                "poi_type": "equilibrium",
                "zone": zone,
                "strength": "medium",
                "created_at": None,
                "mitigation_status": "FRESH",
                "displacement_score": 0.0,
                "displacement_quality": "NONE",
                "near_price": self._price_in_or_near_zone(current_price, zone, atr * 0.15),
                "source_ref": {},
            })
        return candidates

    def _rank_poi_candidate(
        self,
        candidate: Dict[str, Any],
        direction: str,
        current_price: float,
        atr: float,
        market_structure: Dict[str, Any],
        sweep: Dict[str, Any],
    ) -> Dict[str, Any]:
        pref = (self.config.get("smc_engine", {}) or {}).get("poi_preference", {}) or {}
        score = 0.0
        reasons: List[str] = []
        mitigation = str(candidate.get("mitigation_status") or "FRESH").upper()
        if mitigation == "FRESH":
            score += float(pref.get("fresh_bonus", 18) or 18)
            reasons.append("fresh_poi")
        elif mitigation == "TESTED":
            score += float(pref.get("tested_bonus", 8) or 8)
            reasons.append("tested_poi")
        elif mitigation in {"MITIGATED", "PARTIAL"}:
            score += float(pref.get("mitigated_penalty", -14) or -14)
            reasons.append("mitigated_penalty")
        elif mitigation == "INVALIDATED":
            score += float(pref.get("invalidated_penalty", -60) or -60)
            reasons.append("invalidated_penalty")

        poi_type = str(candidate.get("poi_type") or "")
        if poi_type == "order_block":
            score += float(pref.get("order_block_bonus", 10) or 10)
            reasons.append("order_block_preferred")
        elif poi_type == "fvg":
            score += float(pref.get("fvg_bonus", 6) or 6)
            reasons.append("fvg_supported")
        elif poi_type == "equilibrium":
            score += float(pref.get("equilibrium_bonus", 2) or 2)

        strength = str(candidate.get("strength") or "")
        if strength == "strong":
            score += float(pref.get("strong_bonus", 10) or 10)
            reasons.append("strong_poi")
        elif strength == "medium":
            score += float(pref.get("medium_bonus", 5) or 5)

        displacement = float(candidate.get("displacement_score") or 0)
        score += min(18.0, displacement * 0.45)
        if displacement > 0:
            reasons.append("displacement_quality")

        zone = candidate.get("zone", {}) or {}
        top = self._f(zone.get("top"))
        bottom = self._f(zone.get("bottom"))
        if top > 0 and bottom > 0:
            midpoint = (top + bottom) / 2
            distance = abs(current_price - midpoint)
            scale = max(atr * float(pref.get("proximity_scale_atr", 1.5) or 1.5), 0.50)
            proximity_score = max(0.0, float(pref.get("proximity_bonus_cap", 14) or 14) * (1.0 - min(distance, scale) / scale))
            score += proximity_score
            if proximity_score > 0:
                reasons.append("proximity_bonus")

        trend = str(market_structure.get("trend") or "")
        if (direction == "BUY" and trend == "BULLISH") or (direction == "SELL" and trend == "BEARISH"):
            score += 6.0
            reasons.append("trend_aligned")

        sweep_type = str(sweep.get("type") or "")
        if (direction == "BUY" and sweep_type == "sell_side") or (direction == "SELL" and sweep_type == "buy_side"):
            score += 12.0
            reasons.append("liquidity_sweep_aligned")
            if str(sweep.get("confirmation") or "").upper() == "STRONG":
                score += 4.0
                reasons.append("strong_sweep")

        candidate = dict(candidate)
        candidate["rank_score"] = round(score, 1)
        candidate["rank_reasons"] = reasons
        return candidate

    def _return_probability_score(
        self,
        poi: Dict[str, Any],
        direction: str,
        current_price: float,
        atr: float,
        market_structure: Dict[str, Any],
        liquidity: Dict[str, Any],
        all_candidates: List[Dict[str, Any]],
    ) -> float:
        cfg = (self.config.get("smc_engine", {}) or {}).get("selection", {}) or {}
        zone = poi.get("zone", {}) or {}
        top = self._f(zone.get("top"))
        bottom = self._f(zone.get("bottom"))
        if top <= 0 or bottom <= 0:
            return 0.0
        midpoint = (top + bottom) / 2.0
        distance = abs(current_price - midpoint)
        scale = max(atr * float(cfg.get("return_probability_distance_scale_atr", 2.5) or 2.5), 0.50)
        score = max(0.0, 65.0 * (1.0 - min(distance, scale * 2.0) / (scale * 2.0)))

        # Penalize if there are stronger/closer intermediate POIs in the path.
        direction_sign = -1 if direction == "SELL" else 1
        intermediate_count = 0
        for other in all_candidates:
            if other is poi:
                continue
            other_zone = other.get("zone", {}) or {}
            other_top = self._f(other_zone.get("top"))
            other_bottom = self._f(other_zone.get("bottom"))
            if other_top <= 0 or other_bottom <= 0:
                continue
            other_mid = (other_top + other_bottom) / 2.0
            if direction == "SELL" and current_price < other_mid < midpoint:
                intermediate_count += 1
            elif direction == "BUY" and current_price > other_mid > midpoint:
                intermediate_count += 1
        score -= intermediate_count * float(cfg.get("intermediate_poi_penalty", 10) or 10)

        # Session / previous-day liquidity relevance.
        sweep = liquidity.get("recent_sweep", {}) or {}
        if sweep.get("occurred"):
            ref_type = str(sweep.get("reference_type") or "")
            if ref_type.startswith("previous_day_"):
                score += float(cfg.get("previous_day_liquidity_bonus", 8) or 8)
            elif ref_type.startswith("session_"):
                score += float(cfg.get("session_liquidity_bonus", 6) or 6)
        session_label = str((liquidity.get("session_liquidity") or {}).get("label") or "")
        if session_label in {"London / Europe Midday", "London + New York Afternoon", "New York Evening"}:
            score += float(cfg.get("session_bonus", 6) or 6)

        structure_quality = str(market_structure.get("structure_quality") or "")
        if structure_quality == "STRONG":
            score += 8.0
        elif structure_quality == "MODERATE":
            score += 4.0

        mitigation = str(poi.get("mitigation_status") or "").upper()
        if mitigation == "FRESH":
            score += 8.0
        elif mitigation == "TESTED":
            score += 3.0
        elif mitigation in {"MITIGATED", "PARTIAL"}:
            score -= 8.0
        return round(max(0.0, min(100.0, score)), 1)

    def _thesis_dominance_score(
        self,
        *,
        poi_quality_score: float,
        return_probability_score: float,
        trigger_score: float,
        trigger_ready: bool,
        sweep: Dict[str, Any],
        poi: Dict[str, Any],
    ) -> float:
        score = poi_quality_score * 0.45 + return_probability_score * 0.35 + float(trigger_score or 0) * 0.20
        if trigger_ready:
            score += 6.0
        if str((sweep or {}).get("confirmation") or "").upper() == "STRONG":
            score += 4.0
        if str(poi.get("strength") or "") == "strong":
            score += 3.0
        return round(max(0.0, min(100.0, score)), 1)

    def _expected_revisit_window(self, return_probability_score: float) -> str:
        if return_probability_score >= 70:
            return "NEAR"
        if return_probability_score >= 45:
            return "MEDIUM"
        return "LOW"

    def _candidate_stop_loss(self, direction: str, poi: Dict[str, Any], atr: float, entry_suggestion: Dict[str, Any]) -> float:
        zone = poi.get("zone", {}) or {}
        top = self._f(zone.get("top"))
        bottom = self._f(zone.get("bottom"))
        buffer = max(atr * 0.25, 0.50)
        if direction == "BUY" and bottom > 0:
            return bottom - buffer
        if direction == "SELL" and top > 0:
            return top + buffer
        return self._f(entry_suggestion.get("sl"), 0.0)

    def _setup_type_from_context(
        self,
        direction: str,
        sweep: Dict[str, Any],
        market_structure: Dict[str, Any],
        poi: Dict[str, Any],
    ) -> str:
        sweep_type = str(sweep.get("type") or "")
        if (direction == "BUY" and sweep_type == "sell_side") or (direction == "SELL" and sweep_type == "buy_side"):
            return "LIQUIDITY_REVERSAL"
        if poi.get("poi_type") == "order_block":
            return "ORDER_BLOCK_PULLBACK"
        if market_structure.get("trend") in {"BULLISH", "BEARISH"}:
            return "STRUCTURE_CONTINUATION"
        return "SMC_CONTEXT"

    def _setup_state_from_context(
        self,
        current_price: float,
        atr: float,
        poi: Dict[str, Any],
        sweep: Dict[str, Any],
    ) -> str:
        if poi.get("near_price"):
            return "ENTRY_ARMED"
        if sweep.get("occurred"):
            return "SWEEP_CONFIRMED"
        if poi.get("poi_type"):
            return "POI_MARKED"
        return "DETECTED"

    def _setup_quality(
        self,
        confidence: int,
        sweep: Dict[str, Any],
        poi: Dict[str, Any],
        market_structure: Dict[str, Any],
        current_price: float,
        atr: float,
        setup_state: str,
    ) -> Dict[str, Any]:
        score = float(confidence)
        if sweep.get("occurred"):
            score += 10.0
            if str(sweep.get("confirmation") or "").upper() == "STRONG":
                score += 6.0
        if poi.get("poi_type") == "order_block":
            score += 10.0
        elif poi.get("poi_type") == "fvg":
            score += 6.0
        if str(poi.get("strength") or "") == "strong":
            score += 8.0
        if market_structure.get("trend") in {"BULLISH", "BEARISH"}:
            score += 4.0
        if setup_state == "ENTRY_ARMED":
            score += 6.0
        score += min(12.0, float(poi.get("rank_score") or 0) * 0.12)
        trigger = poi.get("trigger") or {}
        if str(trigger.get("state") or "") == "REJECTION_CONFIRMED":
            score += 8.0
        elif trigger.get("market_ready"):
            score += 4.0
        score = max(0.0, min(100.0, score))
        if score >= 88:
            grade = "A+"
        elif score >= 80:
            grade = "A"
        elif score >= 70:
            grade = "B"
        elif score >= 60:
            grade = "C"
        else:
            grade = "D"
        return {"grade": grade, "score": round(score, 1)}

    def _target_liquidity(self, direction: str, current_price: float, liquidity: Dict[str, Any]) -> float | None:
        if direction == "BUY":
            targets = [self._f(level) for level in liquidity.get("buy_side", []) if self._f(level) > current_price]
            return min(targets) if targets else None
        targets = [self._f(level) for level in liquidity.get("sell_side", []) if self._f(level) < current_price]
        return max(targets) if targets else None

    def _trigger_signal(
        self,
        direction: str,
        poi: Dict[str, Any],
        candles: List[Candle],
        current_price: float,
        atr: float,
    ) -> Dict[str, Any]:
        zone = poi.get("zone", {}) or {}
        top = self._f(zone.get("top"))
        bottom = self._f(zone.get("bottom"))
        if not candles or top <= 0 or bottom <= 0:
            return {"state": "AWAIT_TOUCH", "score": 0, "market_ready": False, "timing": "WAIT", "execution_hint": "LIMIT"}
        low = min(top, bottom)
        high = max(top, bottom)
        last = candles[-1]
        open_price = self._f(last.get("open"))
        close_price = self._f(last.get("close"))
        candle_high = self._f(last.get("high"))
        candle_low = self._f(last.get("low"))
        body = abs(close_price - open_price)
        midpoint = (high + low) / 2.0
        trigger_cfg = (self.config.get("smc_engine", {}) or {}).get("trigger_logic", {}) or {}
        wick_body_ratio = float(trigger_cfg.get("rejection_wick_body_ratio", 0.75) or 0.75)
        confirm_close_position = float(trigger_cfg.get("confirm_close_position", 0.55) or 0.55)
        market_min_score = float(trigger_cfg.get("market_entry_min_trigger_score", 70) or 70)
        touched = candle_low <= high and candle_high >= low
        near = self._price_in_or_near_zone(current_price, zone, atr * 0.25)
        score = 0.0
        reasons: List[str] = []
        if touched:
            score += 35.0
            reasons.append("zone_touched")
        elif near:
            score += 18.0
            reasons.append("near_poi")
        if direction == "BUY":
            wick = min(open_price, close_price) - candle_low
            close_position = (close_price - candle_low) / max(candle_high - candle_low, 0.0001)
            if close_price > open_price:
                score += 22.0
                reasons.append("bullish_close")
            if close_price >= midpoint:
                score += 12.0
                reasons.append("closed_above_midpoint")
            if close_position >= confirm_close_position:
                score += 8.0
                reasons.append("strong_close_position")
            if body > 0 and wick >= body * wick_body_ratio:
                score += 10.0
                reasons.append("lower_wick_rejection")
        else:
            wick = candle_high - max(open_price, close_price)
            close_position = (candle_high - close_price) / max(candle_high - candle_low, 0.0001)
            if close_price < open_price:
                score += 22.0
                reasons.append("bearish_close")
            if close_price <= midpoint:
                score += 12.0
                reasons.append("closed_below_midpoint")
            if close_position >= confirm_close_position:
                score += 8.0
                reasons.append("strong_close_position")
            if body > 0 and wick >= body * wick_body_ratio:
                score += 10.0
                reasons.append("upper_wick_rejection")
        market_ready = touched and score >= market_min_score
        if market_ready:
            state = "REJECTION_CONFIRMED"
            timing = "MARKET_READY"
            execution_hint = "MARKET"
        elif touched:
            state = "TOUCH_NO_REJECTION"
            timing = "WAIT_CONFIRMATION"
            execution_hint = "LIMIT"
        elif near:
            state = "AT_POI_WAIT_TRIGGER"
            timing = "WAIT_TRIGGER"
            execution_hint = "LIMIT"
        else:
            state = "AWAY_FROM_POI"
            timing = "WAIT_PULLBACK"
            execution_hint = "LIMIT"
        return {
            "state": state,
            "score": round(score, 1),
            "market_ready": market_ready,
            "timing": timing,
            "execution_hint": execution_hint,
            "reasons": reasons,
        }

    def _recent_sweep(
        self,
        candles: List[Candle],
        tolerance: float,
        previous_day_levels: Dict[str, Any] | None = None,
        session_liquidity: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Detect recent sweeps against swing, previous-day, and session liquidity."""
        if len(candles) < 20:
            return {"occurred": False, "type": None, "level": None, "time": None}
        previous_day_levels = previous_day_levels or {}
        session_liquidity = session_liquidity or {}
        for offset in range(1, min(6, len(candles) - 12) + 1):
            candle = candles[-offset]
            previous = candles[-offset - 12 : -offset]
            if not previous:
                continue
            prev_high = max(self._f(c.get("high")) for c in previous)
            prev_low = min(self._f(c.get("low")) for c in previous)
            high = self._f(candle.get("high"))
            low = self._f(candle.get("low"))
            close = self._f(candle.get("close"))
            candle_range = max(high - low, 0.0001)
            close_position = (close - low) / candle_range
            reference_highs = [
                ("recent_highs", prev_high),
                ("previous_day_high", self._f(previous_day_levels.get("high"))),
                ("session_high", self._f(session_liquidity.get("high"))),
            ]
            reference_lows = [
                ("recent_lows", prev_low),
                ("previous_day_low", self._f(previous_day_levels.get("low"))),
                ("session_low", self._f(session_liquidity.get("low"))),
            ]
            for ref_type, level in reference_highs:
                if level <= 0:
                    continue
                if high > level + tolerance and close < level:
                    sweep_distance = high - level
                    confirmation = "STRONG" if close_position <= 0.35 and sweep_distance >= tolerance * 1.4 else "MODERATE" if close_position <= 0.50 else "WEAK"
                    return {
                        "occurred": True,
                        "type": "buy_side",
                        "level": round(level, 2),
                        "time": candle.get("time"),
                        "confirmation": confirmation,
                        "reference_type": ref_type,
                        "reference_label": ref_type.replace("_", " "),
                        "sweep_distance": round(sweep_distance, 2),
                    }
            for ref_type, level in reference_lows:
                if level <= 0:
                    continue
                if low < level - tolerance and close > level:
                    sweep_distance = level - low
                    confirmation = "STRONG" if close_position >= 0.65 and sweep_distance >= tolerance * 1.4 else "MODERATE" if close_position >= 0.50 else "WEAK"
                    return {
                        "occurred": True,
                        "type": "sell_side",
                        "level": round(level, 2),
                        "time": candle.get("time"),
                        "confirmation": confirmation,
                        "reference_type": ref_type,
                        "reference_label": ref_type.replace("_", " "),
                        "sweep_distance": round(sweep_distance, 2),
                    }
        return {"occurred": False, "type": None, "level": None, "time": None}

    def _cluster_liquidity(self, levels: List[float], tolerance: float) -> List[float]:
        """Return clustered equal highs/lows with at least two touches."""
        return [cluster["level"] for cluster in self._cluster_liquidity_details(levels, tolerance)]

    def _cluster_liquidity_details(self, levels: List[float], tolerance: float) -> List[Dict[str, Any]]:
        if not levels:
            return []
        ordered = sorted(levels)
        clusters: List[List[float]] = [[ordered[0]]]
        for level in ordered[1:]:
            if abs(level - mean(clusters[-1])) <= tolerance:
                clusters[-1].append(level)
            else:
                clusters.append([level])
        details: List[Dict[str, Any]] = []
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            touches = len(cluster)
            quality = "STRONG" if touches >= 4 else "MODERATE" if touches == 3 else "WEAK"
            details.append({"level": round(mean(cluster), 2), "touches": touches, "quality": quality})
        return details

    def _unique_levels(self, levels: List[float]) -> List[float]:
        """Deduplicate rounded price levels."""
        return sorted({round(level, 2) for level in levels if level > 0})

    def _previous_day_levels(self, candles: List[Candle]) -> Dict[str, Any]:
        """Return previous local-day high/low using Asia/Jerusalem session date."""
        if not candles:
            return {"high": None, "low": None, "date": None}
        buckets: Dict[str, List[Candle]] = {}
        for candle in candles:
            dt = self._parse_dt(candle.get("time"))
            if dt is None:
                continue
            local = dt.astimezone(ZoneInfo("Asia/Jerusalem"))
            buckets.setdefault(local.date().isoformat(), []).append(candle)
        if len(buckets) < 2:
            return {"high": None, "low": None, "date": None}
        days = sorted(buckets)
        prev_day = days[-2]
        prev = buckets[prev_day]
        return {
            "date": prev_day,
            "high": round(max(self._f(c.get("high")) for c in prev), 2),
            "low": round(min(self._f(c.get("low")) for c in prev), 2),
        }

    def _session_liquidity(self, candles: List[Candle]) -> Dict[str, Any]:
        """Return high/low of the current local session bucket."""
        if not candles:
            return {"high": None, "low": None, "label": None}
        latest_dt = self._parse_dt(candles[-1].get("time"))
        if latest_dt is None:
            return {"high": None, "low": None, "label": None}
        latest_local = latest_dt.astimezone(ZoneInfo("Asia/Jerusalem"))
        hour = latest_local.hour
        if 3 <= hour < 10:
            start_h, end_h, label = 3, 10, "Asia Morning"
        elif 10 <= hour < 15:
            start_h, end_h, label = 10, 15, "London / Europe Midday"
        elif 15 <= hour < 19:
            start_h, end_h, label = 15, 19, "London + New York Afternoon"
        elif 19 <= hour < 24:
            start_h, end_h, label = 19, 24, "New York Evening"
        else:
            start_h, end_h, label = 0, 3, "Late New York Night"
        session_candles: List[Candle] = []
        for candle in candles:
            dt = self._parse_dt(candle.get("time"))
            if dt is None:
                continue
            local = dt.astimezone(ZoneInfo("Asia/Jerusalem"))
            if local.date() != latest_local.date():
                continue
            if start_h <= local.hour < end_h:
                session_candles.append(candle)
        if not session_candles:
            return {"high": None, "low": None, "label": label}
        return {
            "label": label,
            "high": round(max(self._f(c.get("high")) for c in session_candles), 2),
            "low": round(min(self._f(c.get("low")) for c in session_candles), 2),
        }

    def _mitigation_status(self, future_candles: List[Candle], zone: Dict[str, float]) -> Dict[str, Any]:
        """Classify an order block as FRESH, TESTED, MITIGATED or INVALIDATED."""
        top = self._f(zone.get("top"))
        bottom = self._f(zone.get("bottom"))
        touches = 0
        deep_touches = 0
        invalidated = False
        midpoint = (top + bottom) / 2
        for candle in future_candles:
            high = self._f(candle.get("high"))
            low = self._f(candle.get("low"))
            close = self._f(candle.get("close"))
            if low <= top and high >= bottom:
                touches += 1
                if low <= midpoint <= high:
                    deep_touches += 1
            # If price closes fully beyond the zone, mark invalidated.
            if close < bottom or close > top:
                # invalidation direction is imperfect without OB type, so require a deep touch first
                if deep_touches and (close < bottom - abs(top-bottom)*0.25 or close > top + abs(top-bottom)*0.25):
                    invalidated = True
                    break
        if invalidated:
            status = "INVALIDATED"
        elif deep_touches:
            status = "MITIGATED"
        elif touches:
            status = "TESTED"
        else:
            status = "FRESH"
        return {"status": status, "touches": touches, "deep_touches": deep_touches}

    def _avg_range(self, candles: List[Candle]) -> float:
        if not candles:
            return 0.0
        return mean(max(self._f(c.get("high")) - self._f(c.get("low")), 0.0) for c in candles)

    def _zone_touched(self, future_candles: List[Candle], zone: Dict[str, float]) -> bool:
        """Return True if later price traded into the zone."""
        top = self._f(zone.get("top"))
        bottom = self._f(zone.get("bottom"))
        for candle in future_candles:
            high = self._f(candle.get("high"))
            low = self._f(candle.get("low"))
            if low <= top and high >= bottom:
                return True
        return False

    def _price_in_or_near_zone(self, price: float, zone: Dict[str, Any], buffer: float) -> bool:
        top = self._f(zone.get("top"))
        bottom = self._f(zone.get("bottom"))
        if top <= 0 or bottom <= 0:
            return False
        return bottom - buffer <= price <= top + buffer

    def _confidence(self, score: float, has_sweep: bool, direction: str) -> int:
        if direction == "NEUTRAL":
            return min(48, int(25 + abs(score) * 4))
        confidence = int(42 + abs(score) * 8)
        # Golden rule: no full confidence without liquidity sweep/mitigation context.
        cap = 90 if has_sweep else 80
        return max(45, min(cap, confidence))

    def _summary(
        self,
        direction: str,
        confidence: int,
        market_structure: Dict[str, Any],
        liquidity: Dict[str, Any],
        zone: str,
        signals: List[str],
    ) -> str:
        sweep = liquidity.get("recent_sweep", {})
        sweep_text = "with a recent liquidity sweep" if sweep.get("occurred") else "without a clear recent liquidity sweep"
        reasons = ", ".join(signals[:3]) if signals else "No sufficient SMC signals"
        return f"SMC: structure {market_structure.get('trend')}, zone {zone}, {sweep_text}. Decision {direction} at {confidence}% — {reasons}"

    def _parse_dt(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            text = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _last(self, values: List[float | None], default: float) -> float:
        for value in reversed(values):
            if value is not None:
                return float(value)
        return default

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _empty(self, summary: str) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "direction": "NEUTRAL",
            "signal": "WAIT",
            "confidence": 0,
            "market_structure": {"trend": "RANGING", "last_bos": None, "last_choch": None, "structure_points": []},
            "order_blocks": [],
            "liquidity": {
                "buy_side": [],
                "sell_side": [],
                "equal_highs": [],
                "equal_lows": [],
                "equal_highs_detail": [],
                "equal_lows_detail": [],
                "previous_day_levels": {"high": None, "low": None, "date": None},
                "session_liquidity": {"high": None, "low": None, "label": None},
                "recent_sweep": {"occurred": False, "type": None, "level": None, "time": None},
            },
            "fvg": [],
            "zone": "EQUILIBRIUM",
            "dealing_range": {"high": 0.0, "low": 0.0, "midpoint": 0.0},
            "signals": [],
            "entry_suggestion": {},
            "setup_candidates": [],
            "setup_structure": {"setup_type": "NONE", "setup_state": "DETECTED", "lead_agent": "smc", "setup_quality": {"grade": "D", "score": 0}},
            "summary": summary,
        }
