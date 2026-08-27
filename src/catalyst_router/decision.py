from decimal import Decimal

from catalyst_router.domain import Route, RouteDecision, SignalFrame


class DecisionEngine:
    """Pure, versioned incumbent routing policy."""

    def __init__(self, policy_profile: str = "BASE") -> None:
        self.policy_profile = policy_profile

    def evaluate(self, frame: SignalFrame) -> RouteDecision:
        if not frame.data_quality_passed:
            return self._no_trade(frame, "market data failed quality gates")
        if frame.spread_bps + frame.estimated_cost_bps >= frame.expected_edge_bps:
            return self._no_trade(frame, "expected edge does not clear estimated costs")

        if (
            frame.has_credible_event
            and frame.event_confidence >= Decimal("0.72")
            and frame.event_novelty >= Decimal("0.60")
            and abs(frame.momentum_score) >= Decimal("0.55")
        ):
            confidence = min(
                Decimal("1"),
                (frame.event_confidence + frame.event_novelty + abs(frame.momentum_score))
                / Decimal("3"),
            )
            return RouteDecision(
                route=Route.CATALYST_CONTINUATION,
                symbol=frame.symbol,
                confidence=confidence,
                reasons=("credible novel event", "price momentum confirms event direction"),
                policy_profile=self.policy_profile,
                observed_at=frame.observed_at,
            )

        if not frame.has_credible_event and frame.reversion_score >= Decimal("0.75"):
            return RouteDecision(
                route=Route.LIQUIDITY_REVERSION,
                symbol=frame.symbol,
                confidence=frame.reversion_score,
                reasons=("extreme unexplained displacement", "expected reversion clears costs"),
                policy_profile=self.policy_profile,
                observed_at=frame.observed_at,
            )

        if frame.regime_score >= Decimal("0.85"):
            return RouteDecision(
                route=Route.REGIME_TREND,
                symbol=frame.symbol,
                confidence=frame.regime_score,
                reasons=("broad regime evidence exceeds threshold",),
                policy_profile=self.policy_profile,
                observed_at=frame.observed_at,
            )

        return self._no_trade(frame, "signals are ambiguous or below policy thresholds")

    def _no_trade(self, frame: SignalFrame, reason: str) -> RouteDecision:
        return RouteDecision(
            route=Route.NO_TRADE,
            symbol=frame.symbol,
            confidence=Decimal("0"),
            reasons=(reason,),
            policy_profile=self.policy_profile,
            observed_at=frame.observed_at,
        )
