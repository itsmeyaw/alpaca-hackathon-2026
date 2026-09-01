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


def test_broker_submits_a_protected_short_bracket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: dict[str, object] = {}

    class FakeTradingClient:
        def __init__(self, key: str, secret: str, **kwargs: Any) -> None:
            del key, secret, kwargs

        def submit_order(self, request: object) -> object:
            submitted["request"] = request
            return SimpleNamespace(
                id=UUID("00000000-0000-0000-0000-000000000002"),
                client_order_id="cr-short",
                symbol="AAPL",
                side=OrderSide.SELL,
                qty="10",
                status="accepted",
            )

    monkeypatch.setattr(alpaca, "TradingClient", FakeTradingClient)
    broker = alpaca.AlpacaPaperBroker("paper-key", "paper-secret")
    plan = OrderPlan(
        client_order_id="cr-short",
        intent_id="intent-short",
        symbol="AAPL",
        side=Side.SELL,
        quantity=10,
        limit_price=Decimal("100"),
        stop_price=Decimal("102"),
        take_profit_price=Decimal("96"),
        risk_amount=Decimal("20"),
        exposure_group="us-equity:short",
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
        expires_at=datetime(2026, 8, 28, 1, tzinfo=UTC),
    )

    result = broker.submit_order(plan)

    request = cast(LimitOrderRequest, submitted["request"])
    assert request.side is OrderSide.SELL
    assert request.take_profit is not None
    assert request.take_profit.limit_price == 96.0
    assert request.stop_loss is not None
    assert request.stop_loss.stop_price == 102.0
    assert result.side is Side.SELL


def test_broker_reports_active_nested_protective_legs(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTradingClient:
        def __init__(self, key: str, secret: str, **kwargs: Any) -> None:
            del key, secret, kwargs

        def get_order_by_client_id(self, client_order_id: str) -> object:
            return SimpleNamespace(
                id=UUID("00000000-0000-0000-0000-000000000003"),
                client_order_id=client_order_id,
                symbol="AAPL",
                side=OrderSide.BUY,
                qty="10",
                status="filled",
                legs=[
                    SimpleNamespace(type="limit", status="new"),
                    SimpleNamespace(type="stop", status="new"),
                ],
            )

    monkeypatch.setattr(alpaca, "TradingClient", FakeTradingClient)

    order = alpaca.AlpacaPaperBroker("paper-key", "paper-secret").get_order_by_client_id(
        "cr-protected"
    )

    assert order is not None
    assert order.has_active_take_profit
    assert order.has_active_stop_loss


def test_broker_reports_a_held_oco_sibling_as_active_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A filled bracket leaves the working exit "new" and its OCO sibling "held"."""

    class FakeTradingClient:
        def __init__(self, key: str, secret: str, **kwargs: Any) -> None:
            del key, secret, kwargs

        def get_order_by_client_id(self, client_order_id: str) -> object:
            return SimpleNamespace(
                id=UUID("00000000-0000-0000-0000-000000000005"),
                client_order_id=client_order_id,
                symbol="AMZN",
                side=OrderSide.BUY,
                qty="39",
                status="filled",
                legs=[
                    SimpleNamespace(type="limit", status="new"),
                    SimpleNamespace(type="stop", status="held"),
                ],
            )

    monkeypatch.setattr(alpaca, "TradingClient", FakeTradingClient)

    order = alpaca.AlpacaPaperBroker("paper-key", "paper-secret").get_order_by_client_id(
        "cr-held-stop"
    )

    assert order is not None
    assert order.has_active_take_profit
    assert order.has_active_stop_loss


@pytest.mark.parametrize("status", ["accepted", "pending_new", "held", "new", "partially_filled"])
def test_broker_accepts_live_protective_leg_statuses(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    class FakeTradingClient:
        def __init__(self, key: str, secret: str, **kwargs: Any) -> None:
            del key, secret, kwargs

        def get_order_by_client_id(self, client_order_id: str) -> object:
            return SimpleNamespace(
                id=UUID("00000000-0000-0000-0000-000000000006"),
                client_order_id=client_order_id,
                symbol="AAPL",
                side=OrderSide.BUY,
                qty="10",
                status="filled",
                legs=[
                    SimpleNamespace(type="limit", status=status),
                    SimpleNamespace(type="stop", status="new"),
                ],
            )

    monkeypatch.setattr(alpaca, "TradingClient", FakeTradingClient)

    order = alpaca.AlpacaPaperBroker("paper-key", "paper-secret").get_order_by_client_id(
        "cr-protected"
    )

    assert order is not None
    assert order.has_active_take_profit
    assert order.has_active_stop_loss


@pytest.mark.parametrize(
    "status",
    [
        "canceled",
        "expired",
        "rejected",
        "pending_cancel",
        "done_for_day",
        "suspended",
        "unknown",
    ],
)
def test_broker_rejects_inactive_or_unknown_protective_leg_statuses(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    class FakeTradingClient:
        def __init__(self, key: str, secret: str, **kwargs: Any) -> None:
            del key, secret, kwargs

        def get_order_by_client_id(self, client_order_id: str) -> object:
            return SimpleNamespace(
                id=UUID("00000000-0000-0000-0000-000000000004"),
                client_order_id=client_order_id,
                symbol="AAPL",
                side=OrderSide.BUY,
                qty="10",
                status="filled",
                legs=[
                    SimpleNamespace(type="limit", status=status),
                    SimpleNamespace(type="stop", status="new"),
                ],
            )

    monkeypatch.setattr(alpaca, "TradingClient", FakeTradingClient)

    order = alpaca.AlpacaPaperBroker("paper-key", "paper-secret").get_order_by_client_id(
        "cr-unprotected"
    )

    assert order is not None
    assert not order.has_active_take_profit
    assert order.has_active_stop_loss
