from decimal import ROUND_FLOOR, Decimal

from catalyst_router.domain import (
    AgentMode,
    AgentState,
    InstrumentType,
    PortfolioRiskState,
    RiskDecision,
    RiskDecisionStatus,
    Route,
    Side,
    TradeIntent,
)


class RiskGovernor:
    MAX_POSITIONS = 6
    MAX_TRADE_RISK_RATE = Decimal("0.01")
    REDUCED_TRADE_RISK_RATE = Decimal("0.005")
    MAX_TOTAL_RISK_RATE = Decimal("0.04")
    MAX_OVERNIGHT_RISK_RATE = Decimal("0.02")
    MAX_GROUP_RISK_RATE = Decimal("0.02")
    DE_RISK_DAILY_LOSS_RATE = Decimal("0.02")
    MAX_DAILY_LOSS_RATE = Decimal("0.04")
    MAX_COMPETITION_DRAWDOWN_RATE = Decimal("0.12")
    MAX_QUOTE_AGE_SECONDS = Decimal("5")

    def __init__(self, *, options_execution_enabled: bool = False) -> None:
        self._options_execution_enabled = options_execution_enabled

    def evaluate(
        self,
        intent: TradeIntent,
        agent: AgentState,
        portfolio: PortfolioRiskState,
        *,
        market_is_open: bool,
        overnight: bool = False,
        trading_blocked: bool = False,
    ) -> RiskDecision:
        vetoes: list[str] = []
        if intent.route is Route.NO_TRADE:
            vetoes.append("NO_TRADE cannot create exposure")
        if intent.instrument_type is InstrumentType.OPTION and intent.side is not Side.BUY:
            vetoes.append("short option legs are prohibited")
        if intent.instrument_type is InstrumentType.OPTION and not self._options_execution_enabled:
            vetoes.append("live option execution is not enabled")
        if intent.route is Route.LIQUIDITY_REVERSION and (
            intent.instrument_type is not InstrumentType.EQUITY
        ):
            vetoes.append("liquidity reversion must use equities")
        if intent.route is Route.REGIME_TREND and (
            intent.instrument_type is not InstrumentType.EQUITY
        ):
            vetoes.append("regime trend must use equities")
        if trading_blocked:
            vetoes.append("Alpaca account is blocked from trading")
        if agent.mode is not AgentMode.RUNNING:
            vetoes.append(f"agent mode is {agent.mode}")
        if not agent.is_reconciled:
            vetoes.append("startup reconciliation is incomplete")
        if not market_is_open:
            vetoes.append("market is closed")
        if not intent.data_quality_passed:
            vetoes.append("intent data failed quality gates")
        if intent.quote_age_seconds > self.MAX_QUOTE_AGE_SECONDS:
            vetoes.append("quote is stale")
        if intent.stop_distance <= 0:
            vetoes.append("stop distance must be positive")
        if intent.side is Side.BUY and intent.stop_price >= intent.entry_price:
            vetoes.append("buy stop must be below entry")
        if intent.side is Side.SELL and intent.stop_price <= intent.entry_price:
            vetoes.append("sell stop must be above entry")
        if portfolio.position_count >= self.MAX_POSITIONS:
            vetoes.append("maximum position count reached")

        daily_loss = max(Decimal("0"), -portfolio.daily_pnl)
        if daily_loss >= portfolio.equity * self.MAX_DAILY_LOSS_RATE:
            vetoes.append("daily loss circuit breaker reached")
        if portfolio.competition_drawdown >= self.MAX_COMPETITION_DRAWDOWN_RATE:
            vetoes.append("competition drawdown kill threshold reached")

        if vetoes:
            return self._veto(intent, vetoes)

        risk_rate = (
            self.REDUCED_TRADE_RISK_RATE
            if daily_loss >= portfolio.equity * self.DE_RISK_DAILY_LOSS_RATE
            else self.MAX_TRADE_RISK_RATE
        )
        allowed_risk = portfolio.equity * risk_rate
        total_capacity = portfolio.equity * self.MAX_TOTAL_RISK_RATE - portfolio.total_open_risk
        group_capacity = (
            portfolio.equity * self.MAX_GROUP_RISK_RATE
            - portfolio.group_open_risk.get(intent.exposure_group, Decimal("0"))
        )
        capacities = [allowed_risk, total_capacity, group_capacity]
        if overnight:
            capacities.append(
                portfolio.equity * self.MAX_OVERNIGHT_RISK_RATE - portfolio.overnight_open_risk
            )
        risk_amount = max(Decimal("0"), min(capacities))
        affordable = (portfolio.buying_power / intent.entry_cost_per_unit).to_integral_value(
            rounding=ROUND_FLOOR
        )
        by_stop = (risk_amount / intent.stop_risk_per_unit).to_integral_value(rounding=ROUND_FLOOR)
        quantity = int(min(affordable, by_stop))
        if quantity < 1:
            return self._veto(intent, ["risk or buying-power capacity is insufficient"])

        actual_risk = intent.stop_risk_per_unit * quantity
        status = (
            RiskDecisionStatus.REDUCED
            if risk_amount < allowed_risk or risk_rate < self.MAX_TRADE_RISK_RATE
            else RiskDecisionStatus.APPROVED
        )
        return RiskDecision(
            status=status,
            intent_id=intent.intent_id,
            quantity=quantity,
            risk_amount=actual_risk,
            checks=(
                "agent running and reconciled",
                "data and market gates passed",
                "trade, portfolio, group, and buying-power limits passed",
            ),
        )

    @staticmethod
    def _veto(intent: TradeIntent, vetoes: list[str]) -> RiskDecision:
        return RiskDecision(
            status=RiskDecisionStatus.VETOED,
            intent_id=intent.intent_id,
            quantity=0,
            risk_amount=Decimal("0"),
            checks=tuple(vetoes),
        )
