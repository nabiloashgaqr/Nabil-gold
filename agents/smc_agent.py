"""SMC / Smart Money Concepts Agent.

وكيل مفاهيم الأموال الذكية: يحلل بنية السوق، BOS/CHoCH، Order Blocks،
مناطق السيولة، Liquidity Sweeps، Fair Value Gaps و Premium/Discount Zones.
المنطق خوارزمي محافظ ومناسب للتشغيل داخل GitHub Actions بدون خدمات خارجية.
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, List, Tuple

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
            confidence = self._confidence(score, liquidity.get("recent_sweep", {}).get("occurred", False), direction)
            entry_suggestion = self._entry_suggestion(direction, current_price, atr, order_blocks, liquidity, dealing_range)

            return {
                "agent": self.name,
                "direction": direction,
                "confidence": confidence,
                "market_structure": market_structure,
                "order_blocks": order_blocks,
                "liquidity": liquidity,
                "fvg": fvg,
                "zone": zone,
                "dealing_range": dealing_range,
                "signals": signals,
                "entry_suggestion": entry_suggestion,
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

        return {
            "trend": trend,
            "last_bos": last_bos,
            "last_choch": last_choch,
            "structure_points": structure_points[-10:],
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
        """Find equal highs/lows and recent liquidity sweeps."""
        highs = swings.get("highs", [])
        lows = swings.get("lows", [])
        equal_highs = self._cluster_liquidity([self._f(p.get("price")) for p in highs], tolerance)
        equal_lows = self._cluster_liquidity([self._f(p.get("price")) for p in lows], tolerance)

        buy_side = self._unique_levels(equal_highs + [self._f(p.get("price")) for p in highs[-4:]])
        sell_side = self._unique_levels(equal_lows + [self._f(p.get("price")) for p in lows[-4:]])
        recent_sweep = self._recent_sweep(candles, tolerance)

        return {
            "buy_side": buy_side[-6:],
            "sell_side": sell_side[:6],
            "equal_highs": equal_highs,
            "equal_lows": equal_lows,
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
        return zone, {"high": round(range_high, 2), "low": round(range_low, 2), "midpoint": round(midpoint, 2)}

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

        sweep = liquidity.get("recent_sweep", {}) or {}
        if sweep.get("occurred") and sweep.get("type") == "sell_side":
            add = 3.4 if sweep.get("confirmation") == "STRONG" else 2.6
            score += add
            signals.append(f"Sell-side liquidity sweep detected ({sweep.get('confirmation')}) - bullish after sweep")
        elif sweep.get("occurred") and sweep.get("type") == "buy_side":
            sub = 3.4 if sweep.get("confirmation") == "STRONG" else 2.6
            score -= sub
            signals.append(f"Buy-side liquidity sweep detected ({sweep.get('confirmation')}) - bearish after sweep")

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
            score += 1.0
            signals.append("Bullish structure with discount/equilibrium pricing")
        elif trend == "BEARISH" and zone in {"PREMIUM", "EQUILIBRIUM"}:
            score -= 1.0
            signals.append("Bearish structure with premium/equilibrium pricing")
        elif zone == "PREMIUM":
            score -= 0.4
        elif zone == "DISCOUNT":
            score += 0.4

        return score, signals

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

    def _recent_sweep(self, candles: List[Candle], tolerance: float) -> Dict[str, Any]:
        """Detect whether a recent candle swept previous highs/lows and closed back inside."""
        if len(candles) < 20:
            return {"occurred": False, "type": None, "level": None, "time": None}
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
            if high > prev_high + tolerance and close < prev_high:
                confirmation = "STRONG" if close_position <= 0.35 else "MODERATE"
                return {"occurred": True, "type": "buy_side", "level": round(prev_high, 2), "time": candle.get("time"), "confirmation": confirmation, "sweep_distance": round(high - prev_high, 2)}
            if low < prev_low - tolerance and close > prev_low:
                confirmation = "STRONG" if close_position >= 0.65 else "MODERATE"
                return {"occurred": True, "type": "sell_side", "level": round(prev_low, 2), "time": candle.get("time"), "confirmation": confirmation, "sweep_distance": round(prev_low - low, 2)}
        return {"occurred": False, "type": None, "level": None, "time": None}

    def _cluster_liquidity(self, levels: List[float], tolerance: float) -> List[float]:
        """Return clustered equal highs/lows with at least two touches."""
        if not levels:
            return []
        ordered = sorted(levels)
        clusters: List[List[float]] = [[ordered[0]]]
        for level in ordered[1:]:
            if abs(level - mean(clusters[-1])) <= tolerance:
                clusters[-1].append(level)
            else:
                clusters.append([level])
        return [round(mean(cluster), 2) for cluster in clusters if len(cluster) >= 2]

    def _unique_levels(self, levels: List[float]) -> List[float]:
        """Deduplicate rounded price levels."""
        return sorted({round(level, 2) for level in levels if level > 0})


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
            "confidence": 0,
            "market_structure": {"trend": "RANGING", "last_bos": None, "last_choch": None, "structure_points": []},
            "order_blocks": [],
            "liquidity": {"buy_side": [], "sell_side": [], "equal_highs": [], "equal_lows": [], "recent_sweep": {"occurred": False, "type": None, "level": None, "time": None}},
            "fvg": [],
            "zone": "EQUILIBRIUM",
            "dealing_range": {"high": 0.0, "low": 0.0, "midpoint": 0.0},
            "signals": [],
            "entry_suggestion": {},
            "summary": summary,
        }
