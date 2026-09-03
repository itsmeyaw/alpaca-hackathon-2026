from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from catalyst_router.challenger import PublicChallengerStatus, ShadowPrediction
from catalyst_router.domain import InstrumentType, QuoteSnapshot, Side
from catalyst_router.execution import DirectionalOptionStrategy
from catalyst_router.options import OptionCandidate, OptionSelection, OptionType
from catalyst_router.training import FEATURE_NAMES_V2, FEATURE_SCHEMA_V2, FeatureVector


class Predictor:
    def __init__(self, value: float) -> None:
        self.value = value
        self.status = PublicChallengerStatus(
            deployed=True,
            loaded=True,
            authority="PAPER_LIVE",
            run_id="run-live",
            feature_schema=FEATURE_SCHEMA_V2,
            decision_gate=0.55,
        )
        self.prediction_kind: Literal["probability", "return"] = "probability"

    def predict(self, vector: FeatureVector) -> ShadowPrediction:
        return ShadowPrediction(
            symbol=vector.symbol,
            observed_at=vector.observed_at,
            run_id="run-live",
            value=self.value,
            signal="ABSTAIN",
        )


class OptionSource:
    def __init__(self) -> None:
        self.directions: list[Side] = []

    def select(
        self,
        underlying_symbol: str,
        direction: Side,
        underlying_price: Decimal,
        *,
        now: datetime | None = None,
    ) -> OptionSelection:
        assert underlying_symbol == "AAPL"
        assert underlying_price == Decimal("100")
        self.directions.append(direction)
        selected_at = now or datetime.now(UTC)
        option_type = OptionType.CALL if direction is Side.BUY else OptionType.PUT
        delta = Decimal("0.62") if direction is Side.BUY else Decimal("-0.62")
        return OptionSelection(
            underlying_symbol="AAPL",
            direction=direction,
            selected=OptionCandidate(
                contract_symbol=f"AAPL260925{option_type.value[0]}00100000",
                underlying_symbol="AAPL",
                option_type=option_type,
                expiration_date=date(2026, 9, 25),
                strike_price=Decimal("100"),
                multiplier=100,
                active=True,
                tradable=True,
                delta=delta,
                bid_price=Decimal("3.80"),
                ask_price=Decimal("4.00"),
                bid_size=10,
                ask_size=10,
                quote_timestamp=selected_at - timedelta(seconds=1),
                feed="indicative",
                open_interest=500,
            ),
        )


def vector() -> FeatureVector:
    return FeatureVector(
        symbol="AAPL",
        observed_at=datetime.now(UTC) - timedelta(minutes=2),
        schema=FEATURE_SCHEMA_V2,
        names=FEATURE_NAMES_V2,
        values=(0.0,) * len(FEATURE_NAMES_V2),
    )


def quote() -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol="AAPL",
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        timestamp=datetime.now(UTC) - timedelta(seconds=1),
        feed="iex",
    )


def test_directional_model_buys_calls_and_puts_with_managed_exit_prices() -> None:
    source = OptionSource()
    bullish = DirectionalOptionStrategy(
        Predictor(0.80), source, decision_gate=Decimal("0.55")
    ).create_intent(vector(), quote())
    bearish = DirectionalOptionStrategy(
        Predictor(0.20), source, decision_gate=Decimal("0.55")
    ).create_intent(vector(), quote())

    assert bullish is not None and bearish is not None
    assert source.directions == [Side.BUY, Side.SELL]
    assert bullish.instrument_type is InstrumentType.OPTION
    assert bearish.instrument_type is InstrumentType.OPTION
    assert bullish.option_type is OptionType.CALL
    assert bearish.option_type is OptionType.PUT
    assert bullish.side is Side.BUY and bearish.side is Side.BUY
    assert bullish.entry_price == Decimal("4.00")
    assert bullish.stop_price == Decimal("2.80")
    assert bullish.take_profit_price == Decimal("6.00")
    assert bullish.stop_risk_per_unit == Decimal("400.00")
