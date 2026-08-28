from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models.bars import BarSet
from alpaca.data.models.quotes import Quote
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from catalyst_router.domain import QuoteSnapshot
from catalyst_router.training import MarketBar

_NEW_YORK = ZoneInfo("America/New_York")


class AlpacaIEXMarketData:
    def __init__(self, key: str, secret: str) -> None:
        self._client = StockHistoricalDataClient(key, secret)

    def recent_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe_minutes: int,
        lookback_days: int,
    ) -> list[MarketBar]:
        now = datetime.now(UTC)
        response = cast(
            BarSet,
            self._client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=list(symbols),
                    timeframe=TimeFrame(timeframe_minutes, TimeFrameUnit.Minute),
                    start=now - timedelta(days=lookback_days),
                    end=now,
                    adjustment=Adjustment.RAW,
                    feed=DataFeed.IEX,
                )
            ),
        )
        interval = timedelta(minutes=timeframe_minutes)
        correction_delay = timedelta(minutes=1)
        bars = []
        for symbol, symbol_bars in response.data.items():
            for bar in symbol_bars:
                local_time = bar.timestamp.astimezone(_NEW_YORK).time()
                if not time(9, 30) <= local_time < time(16, 0):
                    continue
                if bar.timestamp + interval > now - correction_delay:
                    continue
                if bar.vwap is None:
                    continue
                bars.append(
                    MarketBar(
                        symbol=symbol,
                        timestamp=bar.timestamp + interval,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                        vwap=bar.vwap,
                    )
                )
        return bars

    def latest_quote(self, symbol: str) -> QuoteSnapshot:
        response = cast(
            dict[str, Quote],
            self._client.get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
            ),
        )
        quote = response.get(symbol)
        if quote is None:
            raise RuntimeError(f"Alpaca returned no IEX quote for {symbol}")
        return QuoteSnapshot(
            symbol=symbol,
            bid_price=Decimal(str(quote.bid_price)),
            ask_price=Decimal(str(quote.ask_price)),
            timestamp=quote.timestamp,
            feed="iex",
        )
