from datetime import UTC, datetime
from decimal import Decimal

from catalyst_router.decision import DecisionEngine
from catalyst_router.domain import Route, SignalFrame


def frame(**updates: object) -> SignalFrame:
    values: dict[str, object] = {
        "symbol": "AAPL",
        "observed_at": datetime(2026, 8, 27, 15, tzinfo=UTC),
        "expected_horizon_minutes": 180,
        "has_credible_event": True,
        "event_confidence": Decimal("0.90"),
        "event_novelty": Decimal("0.80"),
        "momentum_score": Decimal("0.70"),
        "reversion_score": Decimal("0.10"),
        "regime_score": Decimal("0.40"),
        "spread_bps": Decimal("4"),
        "expected_edge_bps": Decimal("30"),
        "estimated_cost_bps": Decimal("6"),
        "data_quality_passed": True,
        "exposure_group": "technology:long",
    }
    values.update(updates)
    return SignalFrame.model_validate(values)


def test_routes_confirmed_event_to_catalyst_continuation() -> None:
    decision = DecisionEngine().evaluate(frame())

    assert decision.route is Route.CATALYST_CONTINUATION
    assert decision.confidence > Decimal("0.7")


def test_routes_unexplained_displacement_to_reversion() -> None:
    decision = DecisionEngine().evaluate(
        frame(
            has_credible_event=False,
            event_confidence=Decimal("0"),
            event_novelty=Decimal("0"),
            momentum_score=Decimal("0.1"),
            reversion_score=Decimal("0.82"),
        )
    )

    assert decision.route is Route.LIQUIDITY_REVERSION


def test_routes_bad_data_and_insufficient_edge_to_no_trade() -> None:
    engine = DecisionEngine()

    assert engine.evaluate(frame(data_quality_passed=False)).route is Route.NO_TRADE
    assert (
        engine.evaluate(
            frame(expected_edge_bps=Decimal("10"), estimated_cost_bps=Decimal("7"))
        ).route
        is Route.NO_TRADE
    )
