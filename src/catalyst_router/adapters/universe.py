from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

from alpaca.data.enums import DataFeed, MarketType, MostActivesBy
from alpaca.data.historical import ScreenerClient, StockHistoricalDataClient
from alpaca.data.models.screener import MostActives, Movers
from alpaca.data.models.snapshots import Snapshot
from alpaca.data.requests import MarketMoversRequest, MostActivesRequest, StockSnapshotRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.models import Asset, Clock
from alpaca.trading.requests import GetAssetsRequest

from catalyst_router.adapters.alpaca import ALPACA_PAPER_BASE_URL
from catalyst_router.universe import (
    UniverseCandidate,
    UniverseSelector,
    UniverseSnapshot,
    UniverseUnavailable,
)


class AlpacaUniverseSource:
    def __init__(
        self,
        key: str,
        secret: str,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._trading = TradingClient(
            key,
            secret,
            paper=True,
            url_override=ALPACA_PAPER_BASE_URL,
        )
        self._screener = ScreenerClient(key, secret)
        self._stocks = StockHistoricalDataClient(key, secret)
        self._selector = UniverseSelector(target_size=20)
        self._now = now or (lambda: datetime.now(UTC))

    def session_date(self) -> date:
        clock = cast(Clock, self._trading.get_clock())
        return clock.timestamp.date() if clock.is_open else clock.next_open.date()

    def build(self, session_date: date) -> UniverseSnapshot:
        clock = cast(Clock, self._trading.get_clock())
        if not clock.is_open or clock.timestamp.date() != session_date:
            raise UniverseUnavailable("daily universe waits for the regular session to open")
        active = cast(
            MostActives,
            self._screener.get_most_actives(MostActivesRequest(top=100, by=MostActivesBy.VOLUME)),
        )
        movers = cast(
            Movers,
            self._screener.get_market_movers(
                MarketMoversRequest(top=50, market_type=MarketType.STOCKS)
            ),
        )
        ranks: dict[str, dict[str, int]] = {}
        for rank, stock in enumerate(active.most_actives, start=1):
            ranks.setdefault(stock.symbol.upper(), {})["most_active"] = rank
        for rank, mover in enumerate(movers.gainers, start=1):
            ranks.setdefault(mover.symbol.upper(), {})["gainer"] = rank
        for rank, mover in enumerate(movers.losers, start=1):
            ranks.setdefault(mover.symbol.upper(), {})["loser"] = rank
        symbols = sorted(ranks)
        assets = cast(
            list[Asset],
            self._trading.get_all_assets(
                GetAssetsRequest(
                    status=AssetStatus.ACTIVE,
                    asset_class=AssetClass.US_EQUITY,
                    attributes="options_enabled",
                )
            ),
        )
        by_symbol = {asset.symbol: asset for asset in assets if asset.symbol in ranks}
        snapshots = cast(
            dict[str, Snapshot],
            self._stocks.get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX)
            ),
        )
        candidates = []
        for symbol in symbols:
            candidate = self._candidate(
                symbol,
                ranks[symbol],
                by_symbol.get(symbol),
                snapshots.get(symbol),
            )
            if candidate is not None:
                candidates.append(candidate)
        return self._selector.select(
            session_date=session_date,
            selected_at=self._now(),
            candidates=tuple(candidates),
        )

    @staticmethod
    def _candidate(
        symbol: str,
        ranks: dict[str, int],
        asset: Asset | None,
        snapshot: Snapshot | None,
    ) -> UniverseCandidate | None:
        if asset is None or snapshot is None:
            return None
        quote = snapshot.latest_quote
        daily = snapshot.previous_daily_bar
        if (
            quote is None
            or daily is None
            or quote.bid_price <= 0
            or quote.ask_price <= 0
            or quote.ask_price < quote.bid_price
            or daily.close <= 0
        ):
            return None
        bid = Decimal(str(quote.bid_price))
        ask = Decimal(str(quote.ask_price))
        midpoint = (bid + ask) / Decimal("2")
        return UniverseCandidate(
            symbol=symbol,
            most_active_rank=ranks.get("most_active"),
            gainer_rank=ranks.get("gainer"),
            loser_rank=ranks.get("loser"),
            price=midpoint,
            prior_day_dollar_volume=Decimal(str(daily.close * daily.volume)),
            spread_bps=(ask - bid) / midpoint * Decimal("10000"),
            active=asset.status is AssetStatus.ACTIVE,
            tradable=asset.tradable,
            # The attributes query filters server-side; Alpaca does not consistently echo the
            # requested attribute in each returned Asset model.
            options_enabled=True,
        )
