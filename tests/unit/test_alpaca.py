from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from catalyst_router.adapters import alpaca
from catalyst_router.domain import OrderPlan, Side


def test_broker_is_pinned_to_paper_trading_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeTradingClient:
        def __init__(self, key: str, secret: str, **kwargs: Any) -> None:
            captured.update(key=key, secret=secret, **kwargs)

    monkeypatch.setattr(alpaca, "TradingClient", FakeTradingClient)

    alpaca.AlpacaPaperBroker("paper-key", "paper-secret")

    assert alpaca.ALPACA_PAPER_API_ROOT == "https://paper-api.alpaca.markets/v2"
    assert captured == {
        "key": "paper-key",
        "secret": "paper-secret",
        "paper": True,
        "url_override": "https://paper-api.alpaca.markets",
    }


def test_broker_submits_a_day_bracket_with_stable_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: dict[str, object] = {}

    class FakeTradingClient:
        def __init__(self, key: str, secret: str, **kwargs: Any) -> None:
            del key, secret, kwargs

        def submit_order(self, request: object) -> object:
            submitted["request"] = request
            return SimpleNamespace(
                id=UUID("00000000-0000-0000-0000-000000000001"),
                client_order_id="cr-stable",
                symbol="AAPL",
                side=OrderSide.BUY,
                qty="10",
                status="accepted",
            )

    monkeypatch.setattr(alpaca, "TradingClient", FakeTradingClient)
    broker = alpaca.AlpacaPaperBroker("paper-key", "paper-secret")
    plan = OrderPlan(
        client_order_id="cr-stable",
        intent_id="intent-1",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10,
        limit_price=Decimal("100"),
        stop_price=Decimal("98"),
        take_profit_price=Decimal("104"),
        risk_amount=Decimal("20"),
        exposure_group="us-equity:long",
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
        expires_at=datetime(2026, 8, 28, 1, tzinfo=UTC),
    )

    result = broker.submit_order(plan)

    request = cast(LimitOrderRequest, submitted["request"])
    assert request.client_order_id == "cr-stable"
    assert request.order_class is OrderClass.BRACKET
    assert request.time_in_force is TimeInForce.DAY
    assert request.extended_hours is False
    assert result.order_id == "00000000-0000-0000-0000-000000000001"
