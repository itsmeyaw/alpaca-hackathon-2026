from datetime import UTC, datetime, timedelta
from decimal import Decimal

from catalyst_router.domain import (
    AccountSnapshot,
    AgentState,
    MarketClockSnapshot,
    OrderExecution,
    OrderPlan,
    ReconciliationSnapshot,
    Side,
)
from catalyst_router.reporting import build_public_portfolio_point


def test_closed_market_exposure_is_reported_as_overnight_risk() -> None:
    now = datetime.now(UTC)
    snapshot = ReconciliationSnapshot(
        account=AccountSnapshot(
            equity=Decimal("100000"),
            buying_power=Decimal("100000"),
            cash=Decimal("90000"),
            portfolio_value=Decimal("100000"),
            trading_blocked=False,
            options_trading_level=3,
            last_equity=Decimal("100000"),
        ),
        clock=MarketClockSnapshot(
            is_open=False,
            timestamp=now,
            next_open=now + timedelta(hours=12),
            next_close=now + timedelta(hours=18),
        ),
        positions=(),
        open_orders=(),
        captured_at=now,
    )
    execution = OrderExecution(
        plan=OrderPlan(
            client_order_id="cr-aapl",
            intent_id="intent-aapl",
            symbol="AAPL",
            side=Side.BUY,
            quantity=10,
            limit_price=Decimal("100"),
            stop_price=Decimal("98"),
            take_profit_price=Decimal("104"),
            risk_amount=Decimal("2000"),
            exposure_group="us-equity:long",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        ),
        request_hash="a" * 64,
    )

    point = build_public_portfolio_point(
        snapshot,
        AgentState(equity_peak=Decimal("100000"), competition_start_equity=Decimal("100000")),
        (execution,),
    )

    assert point.total_open_risk_rate == Decimal("0.02")
    assert point.overnight_open_risk_rate == Decimal("0.02")
