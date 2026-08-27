from __future__ import annotations

from decimal import Decimal
from typing import cast

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.models import Clock, Order, Position, TradeAccount
from alpaca.trading.requests import GetOrdersRequest

from catalyst_router.domain import (
    AccountSnapshot,
    MarketClockSnapshot,
    OpenOrderSnapshot,
    PositionSnapshot,
    ReconciliationSnapshot,
)


class AlpacaPaperBroker:
    """Read-only Alpaca paper adapter. Order submission is intentionally absent."""

    def __init__(self, key: str, secret: str) -> None:
        self._client = TradingClient(key, secret, paper=True)

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
