from __future__ import annotations

from decimal import Decimal
from typing import cast

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.models import Clock, Order, Position, TradeAccount
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from catalyst_router.domain import (
    AccountSnapshot,
    BrokerOrderSnapshot,
    MarketClockSnapshot,
    OpenOrderSnapshot,
    OrderPlan,
    PositionSnapshot,
    ReconciliationSnapshot,
    Side,
)

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_PAPER_API_ROOT = f"{ALPACA_PAPER_BASE_URL}/v2"
_ACTIVE_PROTECTIVE_STATUSES = {
    "new",
    "partially_filled",
}


class AlpacaPaperBroker:
    """Alpaca adapter hard-pinned to the paper-trading endpoint."""

    def __init__(self, key: str, secret: str) -> None:
        self._client = TradingClient(
            key,
            secret,
            paper=True,
            url_override=ALPACA_PAPER_BASE_URL,
        )

    def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        account = cast(TradeAccount, self._client.get_account())
        clock = cast(Clock, self._client.get_clock())
        positions = cast(list[Position], self._client.get_all_positions())
        orders = cast(
            list[Order],
            self._client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
            ),
        )
        return ReconciliationSnapshot(
            account=AccountSnapshot(
                equity=Decimal(str(account.equity)),
                buying_power=Decimal(str(account.buying_power)),
                cash=Decimal(str(account.cash)),
                portfolio_value=Decimal(str(account.portfolio_value)),
                trading_blocked=bool(account.trading_blocked),
                options_trading_level=int(account.options_trading_level or 0),
                last_equity=(
                    Decimal(str(account.last_equity)) if account.last_equity is not None else None
                ),
            ),
            clock=MarketClockSnapshot(
                is_open=bool(clock.is_open),
                timestamp=clock.timestamp,
                next_open=clock.next_open,
                next_close=clock.next_close,
            ),
            positions=tuple(
                PositionSnapshot(
                    symbol=position.symbol,
                    asset_class=str(position.asset_class),
                    quantity=Decimal(str(position.qty)),
                    market_value=Decimal(str(position.market_value or 0)),
                    unrealized_pl=Decimal(str(position.unrealized_pl or 0)),
                )
                for position in positions
            ),
            open_orders=tuple(
                OpenOrderSnapshot(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol or "",
                    status=str(order.status),
                    side=str(order.side or ""),
                    quantity=Decimal(str(order.qty)) if order.qty is not None else None,
                )
                for order in orders
            ),
        )

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        try:
            order = cast(Order, self._client.get_order_by_client_id(client_order_id))
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise
        return self._order_snapshot(order)

    def submit_order(self, plan: OrderPlan) -> BrokerOrderSnapshot:
        order = cast(
            Order,
            self._client.submit_order(
                LimitOrderRequest(
                    symbol=plan.symbol,
                    qty=plan.quantity,
                    side=OrderSide.BUY if plan.side is Side.BUY else OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    order_class=OrderClass.BRACKET,
                    extended_hours=False,
                    client_order_id=plan.client_order_id,
                    limit_price=float(plan.limit_price),
                    take_profit=TakeProfitRequest(limit_price=float(plan.take_profit_price)),
                    stop_loss=StopLossRequest(stop_price=float(plan.stop_price)),
                )
            ),
        )
        return self._order_snapshot(order)

    def close_position(self, symbol: str) -> None:
        """Cancels the symbol's resting bracket legs, then closes just that position."""
        orders = cast(
            list[Order],
            self._client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], limit=500)
            ),
        )
        for order in orders:
            try:
                self._client.cancel_order_by_id(str(order.id))
            except APIError as exc:
                if exc.status_code not in {404, 422}:
                    raise
        try:
            self._client.close_position(symbol)
        except APIError as exc:
            if exc.status_code != 404:
                raise

    def flatten(self) -> None:
        self._client.close_all_positions(cancel_orders=True)

    @staticmethod
    def _order_snapshot(order: Order) -> BrokerOrderSnapshot:
        if order.symbol is None or order.side is None or order.qty is None:
            raise RuntimeError("Alpaca order response is incomplete")
        active_legs = [
            leg
            for leg in getattr(order, "legs", None) or []
            if str(leg.status).rsplit(".", 1)[-1].lower() in _ACTIVE_PROTECTIVE_STATUSES
        ]
        return BrokerOrderSnapshot(
            order_id=str(order.id),
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=Side.BUY if order.side == OrderSide.BUY else Side.SELL,
            quantity=int(Decimal(str(order.qty))),
            status=str(order.status),
            has_active_take_profit=any(
                str(leg.type).rsplit(".", 1)[-1].lower() == "limit" for leg in active_legs
            ),
            has_active_stop_loss=any(
                str(leg.type).rsplit(".", 1)[-1].lower() in {"stop", "stop_limit"}
                for leg in active_legs
            ),
        )
