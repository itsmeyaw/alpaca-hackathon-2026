from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.data.enums import OptionsFeed
from alpaca.trading.enums import AssetStatus, ContractType

from catalyst_router.adapters import options as options_adapter
from catalyst_router.adapters import universe as universe_adapter
from catalyst_router.domain import Side


def test_universe_adapter_maps_server_filtered_option_assets_and_gate_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 14, tzinfo=UTC)

    class TradingClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def get_clock(self) -> object:
            return SimpleNamespace(is_open=True, timestamp=now, next_open=now)

        def get_all_assets(self, _: object) -> list[object]:
            return [
                SimpleNamespace(
                    symbol="AAPL",
                    status=AssetStatus.ACTIVE,
                    tradable=True,
                    attributes=None,
                )
            ]

    class ScreenerClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def get_most_actives(self, _: object) -> object:
            return SimpleNamespace(most_actives=[SimpleNamespace(symbol="AAPL")])

        def get_market_movers(self, _: object) -> object:
            return SimpleNamespace(gainers=[], losers=[])

    class Stocks:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def get_stock_snapshot(self, _: object) -> dict[str, object]:
            return {
                "AAPL": SimpleNamespace(
                    latest_quote=SimpleNamespace(bid_price=99.95, ask_price=100.05),
                    previous_daily_bar=SimpleNamespace(close=100.0, volume=1_000_000),
                )
            }

    monkeypatch.setattr(universe_adapter, "TradingClient", TradingClient)
    monkeypatch.setattr(universe_adapter, "ScreenerClient", ScreenerClient)
    monkeypatch.setattr(universe_adapter, "StockHistoricalDataClient", Stocks)

    source = universe_adapter.AlpacaUniverseSource("key", "secret", now=lambda: now)
    result = source.build(date(2026, 9, 3))

    assert result.symbols == ("AAPL",)
    evidence = result.candidate_evidence["AAPL"]
    assert evidence.options_enabled
    assert evidence.prior_day_dollar_volume == Decimal("100000000.0")
    assert evidence.spread_bps < Decimal("15")


def test_option_adapter_joins_contract_metadata_to_indicative_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 15, tzinfo=UTC)
    captured: dict[str, Any] = {}

    class TradingClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def get_option_contracts(self, request: object) -> object:
            captured["contracts"] = request
            return SimpleNamespace(
                option_contracts=[
                    SimpleNamespace(
                        symbol="AAPL260925C00100000",
                        underlying_symbol="AAPL",
                        type=ContractType.CALL,
                        expiration_date=date(2026, 9, 25),
                        strike_price=100.0,
                        size="100",
                        status=AssetStatus.ACTIVE,
                        tradable=True,
                        open_interest="500",
                    )
                ],
                next_page_token=None,
            )

    class Options:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def get_option_snapshot(self, request: object) -> dict[str, object]:
            captured["snapshot"] = request
            return {
                "AAPL260925C00100000": SimpleNamespace(
                    latest_quote=SimpleNamespace(
                        bid_price=3.90,
                        ask_price=4.00,
                        bid_size=10,
                        ask_size=20,
                        timestamp=now - timedelta(seconds=1),
                    ),
                    greeks=SimpleNamespace(delta=0.625),
                )
            }

    monkeypatch.setattr(options_adapter, "TradingClient", TradingClient)
    monkeypatch.setattr(options_adapter, "OptionHistoricalDataClient", Options)

    result = options_adapter.AlpacaOptionMarketData("key", "secret").select(
        "AAPL", Side.BUY, Decimal("100"), now=now
    )

    assert result.selected is not None
    assert result.selected.contract_symbol == "AAPL260925C00100000"
    assert result.selected.multiplier == 100
    assert captured["snapshot"].feed is OptionsFeed.INDICATIVE


def test_option_adapter_fails_closed_when_greeks_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 15, tzinfo=UTC)

    class TradingClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def get_option_contracts(self, _: object) -> object:
            return SimpleNamespace(
                option_contracts=[
                    SimpleNamespace(
                        symbol="AAPL260925C00100000",
                        underlying_symbol="AAPL",
                        type=ContractType.CALL,
                        expiration_date=date(2026, 9, 25),
                        strike_price=100.0,
                        size="100",
                        status=AssetStatus.ACTIVE,
                        tradable=True,
                        open_interest="500",
                    )
                ],
                next_page_token=None,
            )

    class Options:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def get_option_snapshot(self, _: object) -> dict[str, object]:
            return {
                "AAPL260925C00100000": SimpleNamespace(
                    latest_quote=SimpleNamespace(
                        bid_price=3.90,
                        ask_price=4.00,
                        bid_size=10,
                        ask_size=20,
                        timestamp=now,
                    ),
                    greeks=None,
                )
            }

    monkeypatch.setattr(options_adapter, "TradingClient", TradingClient)
    monkeypatch.setattr(options_adapter, "OptionHistoricalDataClient", Options)

    result = options_adapter.AlpacaOptionMarketData("key", "secret").select(
        "AAPL", Side.BUY, Decimal("100"), now=now
    )

    assert result.selected is None
