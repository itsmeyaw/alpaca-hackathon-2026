from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from alpaca.data.enums import OptionsFeed
from alpaca.data.historical import OptionHistoricalDataClient
from alpaca.data.models.quotes import Quote
from alpaca.data.models.snapshots import OptionsSnapshot
from alpaca.data.requests import OptionLatestQuoteRequest, OptionSnapshotRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.models import OptionContract, OptionContractsResponse
from alpaca.trading.requests import GetOptionContractsRequest

from catalyst_router.adapters.alpaca import ALPACA_PAPER_BASE_URL
from catalyst_router.domain import QuoteSnapshot, Side
from catalyst_router.options import (
    OptionCandidate,
    OptionContractSelector,
    OptionSelection,
    OptionType,
)


class AlpacaOptionMarketData:
    def __init__(self, key: str, secret: str) -> None:
        self._trading = TradingClient(
            key,
            secret,
            paper=True,
            url_override=ALPACA_PAPER_BASE_URL,
        )
        self._options = OptionHistoricalDataClient(key, secret)
        self._selector = OptionContractSelector()

    def select(
        self,
        underlying_symbol: str,
        direction: Side,
        underlying_price: Decimal,
        *,
        now: datetime | None = None,
    ) -> OptionSelection:
        selected_at = now or datetime.now(UTC)
        contract_type = ContractType.CALL if direction is Side.BUY else ContractType.PUT
        contracts = self._contracts(
            underlying_symbol,
            contract_type,
            selected_at,
            underlying_price,
        )
        snapshots: dict[str, OptionsSnapshot] = {}
        symbols = [contract.symbol for contract in contracts]
        for offset in range(0, len(symbols), 100):
            snapshots.update(
                cast(
                    dict[str, OptionsSnapshot],
                    self._options.get_option_snapshot(
                        OptionSnapshotRequest(
                            symbol_or_symbols=symbols[offset : offset + 100],
                            feed=OptionsFeed.INDICATIVE,
                        )
                    ),
                )
            )
        candidates = tuple(
            candidate
            for contract in contracts
            if (
                candidate := self._candidate(
                    contract,
                    snapshots.get(contract.symbol),
                )
            )
            is not None
        )
        return self._selector.select(
            underlying_symbol,
            direction,
            candidates,
            now=selected_at,
        )

    def latest_quote(self, symbol: str) -> QuoteSnapshot:
        response = cast(
            dict[str, Quote],
            self._options.get_option_latest_quote(
                OptionLatestQuoteRequest(
                    symbol_or_symbols=symbol,
                    feed=OptionsFeed.INDICATIVE,
                )
            ),
        )
        quote = response.get(symbol)
        if quote is None:
            raise RuntimeError(f"Alpaca returned no indicative option quote for {symbol}")
        bid_price = Decimal(str(quote.bid_price))
        ask_price = Decimal(str(quote.ask_price))
        if bid_price <= 0 or ask_price <= 0:
            raise RuntimeError(f"Alpaca returned an incomplete option quote for {symbol}")
        return QuoteSnapshot(
            symbol=symbol,
            bid_price=bid_price,
            ask_price=ask_price,
            timestamp=quote.timestamp,
            feed="indicative",
        )

    def _contracts(
        self,
        underlying_symbol: str,
        contract_type: ContractType,
        now: datetime,
        underlying_price: Decimal,
    ) -> list[OptionContract]:
        contracts: list[OptionContract] = []
        page_token = None
        while True:
            response = cast(
                OptionContractsResponse,
                self._trading.get_option_contracts(
                    GetOptionContractsRequest(
                        underlying_symbols=[underlying_symbol],
                        status=AssetStatus.ACTIVE,
                        expiration_date_gte=(now + timedelta(days=14)).date(),
                        expiration_date_lte=(now + timedelta(days=30)).date(),
                        type=contract_type,
                        strike_price_gte=str(underlying_price * Decimal("0.70")),
                        strike_price_lte=str(underlying_price * Decimal("1.30")),
                        limit=10_000,
                        page_token=page_token,
                    )
                ),
            )
            contracts.extend(response.option_contracts or [])
            page_token = response.next_page_token
            if page_token is None:
                return contracts

    @staticmethod
    def _candidate(
        contract: OptionContract,
        snapshot: OptionsSnapshot | None,
    ) -> OptionCandidate | None:
        if (
            snapshot is None
            or snapshot.latest_quote is None
            or snapshot.greeks is None
            or contract.open_interest is None
        ):
            return None
        quote = snapshot.latest_quote
        if quote.bid_price <= 0 or quote.ask_price <= 0:
            return None
        return OptionCandidate(
            contract_symbol=contract.symbol,
            underlying_symbol=contract.underlying_symbol,
            option_type=(OptionType.CALL if contract.type is ContractType.CALL else OptionType.PUT),
            expiration_date=contract.expiration_date,
            strike_price=Decimal(str(contract.strike_price)),
            multiplier=int(Decimal(contract.size)),
            active=contract.status is AssetStatus.ACTIVE,
            tradable=contract.tradable,
            delta=Decimal(str(snapshot.greeks.delta)),
            bid_price=Decimal(str(quote.bid_price)),
            ask_price=Decimal(str(quote.ask_price)),
            bid_size=int(quote.bid_size),
            ask_size=int(quote.ask_size),
            quote_timestamp=quote.timestamp,
            feed="indicative",
            open_interest=int(Decimal(contract.open_interest)),
        )
