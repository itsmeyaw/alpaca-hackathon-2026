from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from catalyst_router.domain import (
    AgentMode,
    AgentState,
    InstrumentType,
    OptionType,
    PortfolioRiskState,
    RiskDecisionStatus,
    Route,
    Side,
    TradeIntent,
)
from catalyst_router.risk import RiskGovernor


def intent(**updates: object) -> TradeIntent:
    values: dict[str, object] = {
        "intent_id": "intent-1",
        "route": Route.LIQUIDITY_REVERSION,
        "symbol": "AAPL",
        "instrument_type": InstrumentType.EQUITY,
        "side": Side.BUY,
        "confidence": Decimal("0.8"),
        "entry_price": Decimal("100"),
        "stop_price": Decimal("98"),
        "expected_horizon_minutes": 30,
        "exposure_group": "technology:long",
        "quote_age_seconds": Decimal("1"),
        "data_quality_passed": True,
    }
    values.update(updates)
    if values["instrument_type"] is InstrumentType.OPTION:
        values.setdefault("underlying_symbol", "AAPL")
        values.setdefault("option_type", OptionType.CALL)
        values.setdefault("option_expiration_date", date(2026, 9, 25))
        values.setdefault("take_profit_price", Decimal("150"))
    return TradeIntent.model_validate(values)


def agent(**updates: object) -> AgentState:
    values: dict[str, object] = {
        "mode": AgentMode.RUNNING,
        "version": 2,
        "execution_epoch": "epoch-1",
        "reconciled_epoch": "epoch-1",
        "reason": "operator resumed",
        "updated_at": datetime(2026, 8, 27, tzinfo=UTC),
    }
    values.update(updates)
    return AgentState.model_validate(values)


def portfolio(**updates: object) -> PortfolioRiskState:
    values: dict[str, object] = {
        "equity": Decimal("100000"),
        "buying_power": Decimal("100000"),
        "position_count": 0,
        "total_open_risk": Decimal("0"),
        "overnight_open_risk": Decimal("0"),
        "group_open_risk": {},
        "daily_pnl": Decimal("0"),
        "competition_drawdown": Decimal("0"),
    }
    values.update(updates)
    return PortfolioRiskState.model_validate(values)


def test_sizes_from_stop_distance() -> None:
    decision = RiskGovernor().evaluate(intent(), agent(), portfolio(), market_is_open=True)

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.quantity == 500
    assert decision.risk_amount == Decimal("1000")


def test_vetoes_unreconciled_or_non_running_agent() -> None:
    governor = RiskGovernor()

    paused = governor.evaluate(
        intent(), agent(mode=AgentMode.PAUSED), portfolio(), market_is_open=True
    )
    unreconciled = governor.evaluate(
        intent(), agent(reconciled_epoch=None), portfolio(), market_is_open=True
    )

    assert paused.status is RiskDecisionStatus.VETOED
    assert unreconciled.status is RiskDecisionStatus.VETOED


def test_reduces_risk_after_two_percent_daily_loss() -> None:
    decision = RiskGovernor().evaluate(
        intent(),
        agent(),
        portfolio(daily_pnl=Decimal("-2000")),
        market_is_open=True,
    )

    assert decision.status is RiskDecisionStatus.REDUCED
    assert decision.quantity == 250
    assert decision.risk_amount == Decimal("500")


def test_vetoes_daily_and_competition_breakers() -> None:
    governor = RiskGovernor()

    daily = governor.evaluate(
        intent(), agent(), portfolio(daily_pnl=Decimal("-4000")), market_is_open=True
    )
    competition = governor.evaluate(
        intent(),
        agent(),
        portfolio(competition_drawdown=Decimal("0.12")),
        market_is_open=True,
    )

    assert daily.status is RiskDecisionStatus.VETOED
    assert competition.status is RiskDecisionStatus.VETOED


def test_vetoes_no_trade_short_options_and_blocked_accounts() -> None:
    governor = RiskGovernor()

    no_trade = governor.evaluate(
        intent(route=Route.NO_TRADE), agent(), portfolio(), market_is_open=True
    )
    short_option = governor.evaluate(
        intent(
            route=Route.CATALYST_CONTINUATION,
            instrument_type=InstrumentType.OPTION,
            side=Side.SELL,
            contract_multiplier=100,
        ),
        agent(),
        portfolio(),
        market_is_open=True,
    )
    blocked = governor.evaluate(
        intent(), agent(), portfolio(), market_is_open=True, trading_blocked=True
    )

    assert no_trade.status is RiskDecisionStatus.VETOED
    assert short_option.status is RiskDecisionStatus.VETOED
    assert blocked.status is RiskDecisionStatus.VETOED


def test_vetoes_invalid_stop_direction_and_stale_quotes() -> None:
    governor = RiskGovernor()

    invalid_buy = governor.evaluate(
        intent(stop_price=Decimal("101")), agent(), portfolio(), market_is_open=True
    )
    stale = governor.evaluate(
        intent(quote_age_seconds=Decimal("6")), agent(), portfolio(), market_is_open=True
    )

    assert invalid_buy.status is RiskDecisionStatus.VETOED
    assert stale.status is RiskDecisionStatus.VETOED


def test_vetoes_exhausted_portfolio_group_and_overnight_capacity() -> None:
    governor = RiskGovernor()
    full = portfolio(
        total_open_risk=Decimal("5000"),
        overnight_open_risk=Decimal("2000"),
        group_open_risk={"technology:long": Decimal("5000")},
    )

    regular = governor.evaluate(intent(), agent(), full, market_is_open=True)
    overnight = governor.evaluate(intent(), agent(), full, market_is_open=True, overnight=True)

    assert regular.status is RiskDecisionStatus.VETOED
    assert overnight.status is RiskDecisionStatus.VETOED


def test_allows_group_and_total_risk_up_to_five_percent() -> None:
    decision = RiskGovernor().evaluate(
        intent(),
        agent(),
        portfolio(
            total_open_risk=Decimal("4000"),
            group_open_risk={"technology:long": Decimal("4000")},
        ),
        market_is_open=True,
    )

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.risk_amount == Decimal("1000")


def test_option_contract_multiplier_is_enforced_and_used_for_sizing() -> None:
    with pytest.raises(ValueError, match="contract multiplier"):
        intent(
            route=Route.CATALYST_CONTINUATION,
            instrument_type=InstrumentType.OPTION,
            contract_multiplier=1,
        )

    option = intent(
        route=Route.CATALYST_CONTINUATION,
        instrument_type=InstrumentType.OPTION,
        entry_price=Decimal("2"),
        stop_price=Decimal("1"),
        contract_multiplier=100,
    )
    decision = RiskGovernor(options_execution_enabled=True).evaluate(
        option,
        agent(),
        portfolio(),
        market_is_open=True,
        options_trading_level=2,
        options_buying_power=Decimal("100000"),
    )

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.quantity == 5
    assert decision.risk_amount == Decimal("1000")


def test_options_require_level_two_and_use_options_buying_power() -> None:
    option = intent(
        route=Route.MODEL_DIRECTIONAL,
        instrument_type=InstrumentType.OPTION,
        entry_price=Decimal("4"),
        stop_price=Decimal("2.80"),
        contract_multiplier=100,
    )
    governor = RiskGovernor(options_execution_enabled=True)

    level_one = governor.evaluate(
        option,
        agent(),
        portfolio(),
        market_is_open=True,
        options_trading_level=1,
        options_buying_power=Decimal("100000"),
    )
    insufficient = governor.evaluate(
        option,
        agent(),
        portfolio(),
        market_is_open=True,
        options_trading_level=2,
        options_buying_power=Decimal("399"),
    )

    assert level_one.status is RiskDecisionStatus.VETOED
    assert "options trading level 2" in level_one.checks[0]
    assert insufficient.status is RiskDecisionStatus.VETOED
