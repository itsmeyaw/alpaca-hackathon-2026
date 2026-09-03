from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from catalyst_router.domain import Side
from catalyst_router.options import OptionCandidate, OptionContractSelector, OptionType

NOW = datetime(2026, 9, 3, 15, tzinfo=UTC)


def option(
    symbol: str,
    *,
    option_type: OptionType = OptionType.CALL,
    expiration: date = date(2026, 9, 25),
    delta: str = "0.62",
    bid: str = "3.80",
    ask: str = "4.00",
    open_interest: int = 500,
    bid_size: int = 10,
    ask_size: int = 10,
    quote_age_seconds: int = 1,
) -> OptionCandidate:
    return OptionCandidate(
        contract_symbol=symbol,
        underlying_symbol="AAPL",
        option_type=option_type,
        expiration_date=expiration,
        strike_price=Decimal("100"),
        multiplier=100,
        active=True,
        tradable=True,
        delta=Decimal(delta),
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        bid_size=bid_size,
        ask_size=ask_size,
        quote_timestamp=NOW - timedelta(seconds=quote_age_seconds),
        feed="indicative",
        open_interest=open_interest,
    )


def test_directional_signals_select_calls_and_puts() -> None:
    selector = OptionContractSelector()
    call = option("AAPL260925C00100000")
    put = option(
        "AAPL260925P00100000",
        option_type=OptionType.PUT,
        delta="-0.62",
    )

    bullish = selector.select("AAPL", Side.BUY, (put, call), now=NOW)
    bearish = selector.select("AAPL", Side.SELL, (call, put), now=NOW)

    assert bullish.selected == call
    assert bearish.selected == put


def test_option_selection_fails_closed_on_each_liquidity_gate() -> None:
    result = OptionContractSelector().select(
        "AAPL",
        Side.BUY,
        (
            option("STALE", quote_age_seconds=6),
            option("WIDE", bid="3.00", ask="4.00"),
            option("THIN", open_interest=99),
            option("EMPTY", bid_size=0),
            option("LOWDELTA", delta="0.54"),
            option("HIGHDELTA", delta="0.71"),
            option("TOOSOON", expiration=date(2026, 9, 16)),
            option("TOOLATE", expiration=date(2026, 10, 4)),
        ),
        now=NOW,
    )

    assert result.selected is None
    assert set(result.rejections) == {
        "EMPTY",
        "HIGHDELTA",
        "LOWDELTA",
        "STALE",
        "THIN",
        "TOOLATE",
        "TOOSOON",
        "WIDE",
    }


def test_option_selection_uses_deterministic_delta_spread_and_interest_ranking() -> None:
    result = OptionContractSelector().select(
        "AAPL",
        Side.BUY,
        (
            option("FAR", delta="0.60", bid="3.80", ask="4.00", open_interest=1000),
            option("TARGET-WIDE", delta="0.625", bid="3.70", ask="4.00"),
            option("TARGET-TIGHT", delta="0.625", bid="3.90", ask="4.00"),
        ),
        now=NOW,
    )

    assert result.selected is not None
    assert result.selected.contract_symbol == "TARGET-TIGHT"
