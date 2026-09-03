from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from catalyst_router.challenger import PublicChallengerStatus, ShadowPrediction
from catalyst_router.domain import (
    AgentMode,
    AgentState,
    BrokerOrderSnapshot,
    DecisionRecord,
    InstrumentType,
    OptionType,
    OrderExecution,
    OrderExecutionStatus,
    OrderPlan,
    PortfolioRiskState,
    QuoteSnapshot,
    ReconciliationSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    Route,
    Side,
    TradeIntent,
)
from catalyst_router.ports import OperationalStore, PaperBroker
from catalyst_router.reporting import build_public_portfolio_point, publish_public_portfolio
from catalyst_router.risk import RiskGovernor
from catalyst_router.training import FEATURE_SCHEMA, FeatureVector

_CENT = Decimal("0.01")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EntrySignal:
    route: Route
    side: Side
    confidence: Decimal
    source: str


class EntryStrategy(Protocol):
    def signal(self, vector: FeatureVector) -> EntrySignal | None: ...

    def create_intent(
        self,
        vector: FeatureVector,
        quote: QuoteSnapshot,
        *,
        signal: EntrySignal | None = None,
        now: datetime | None = None,
    ) -> TradeIntent | None: ...


class IncumbentStrategy:
    """Conservative deterministic policy authorized for the first paper execution slice."""

    MAX_QUOTE_AGE = timedelta(seconds=5)
    MAX_FEATURE_AGE = timedelta(minutes=7)
    MAX_SPREAD_BPS = Decimal("15")

    def create_intent(
        self,
        vector: FeatureVector,
        quote: QuoteSnapshot,
        *,
        signal: EntrySignal | None = None,
        now: datetime | None = None,
    ) -> TradeIntent | None:
        observed_now = now or datetime.now(UTC)
        if vector.schema != FEATURE_SCHEMA or quote.symbol != vector.symbol:
            return None
        feature_age = observed_now - vector.observed_at
        quote_age = observed_now - quote.timestamp
        if not timedelta(0) <= feature_age <= self.MAX_FEATURE_AGE:
            return None
        if not timedelta(0) <= quote_age <= self.MAX_QUOTE_AGE:
            return None
        if quote.spread_bps > self.MAX_SPREAD_BPS:
            return None

        selected = signal or self.signal(vector)
        if selected is None:
            return None

        entry_price = quote.ask_price.quantize(_CENT, rounding=ROUND_CEILING)
        stop_price = (entry_price * Decimal("0.98")).quantize(_CENT, rounding=ROUND_FLOOR)
        intent_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{selected.source}:{selected.route}:{vector.symbol}:{vector.observed_at.isoformat()}",
            )
        )
        return TradeIntent(
            intent_id=intent_id,
            route=selected.route,
            symbol=vector.symbol,
            instrument_type=InstrumentType.EQUITY,
            side=selected.side,
            confidence=selected.confidence,
            entry_price=entry_price,
            stop_price=stop_price,
            expected_horizon_minutes=(60 if selected.route is Route.LIQUIDITY_REVERSION else 240),
            exposure_group="us-equity:long",
            quote_age_seconds=Decimal(str(quote_age.total_seconds())),
            data_quality_passed=True,
        )

    def signal(self, vector: FeatureVector) -> EntrySignal | None:
        route = self.route(vector)
        if route is None:
            return None
        return EntrySignal(
            route=route,
            side=Side.BUY,
            confidence=(Decimal("0.85") if route is Route.REGIME_TREND else Decimal("0.80")),
            source="incumbent-v1",
        )

    @staticmethod
    def route(vector: FeatureVector) -> Route | None:
        if vector.schema != FEATURE_SCHEMA:
            return None
        features = dict(zip(vector.names, vector.values, strict=True))
        if vector.symbol in {"SPY", "QQQ"} and (
            features["return_15m"] > 0
            and features["return_1h"] >= 0.002
            and features["return_2h"] >= 0.005
            and features["close_location_2h"] >= 0.2
        ):
            return Route.REGIME_TREND
        elif vector.symbol not in {"SPY", "QQQ"} and (
            features["return_15m"] >= 0.0005
            and features["relative_return_1h"] <= -0.005
            and features["vwap_distance"] >= 0
            and features["close_location_2h"] <= -0.3
            and features["cross_sectional_return_rank_1h"] <= -0.35
        ):
            return Route.LIQUIDITY_REVERSION
        return None


class ModelPredictor(Protocol):
    status: PublicChallengerStatus
    prediction_kind: Literal["probability", "return"]

    def predict(self, vector: FeatureVector) -> ShadowPrediction: ...


class ModelStrategy:
    """Turns sufficiently directional model predictions into paper trade intents."""

    MAX_QUOTE_AGE = timedelta(seconds=5)
    # Alpaca timestamps the freshest quotes microseconds ahead of our clock, so a
    # strict lower bound of zero discards the newest quote on the most liquid names.
    MAX_CLOCK_SKEW = timedelta(seconds=2)
    MAX_FEATURE_AGE = timedelta(minutes=7)
    MAX_SPREAD_BPS = Decimal("15")

    def __init__(self, predictor: ModelPredictor, *, decision_gate: Decimal) -> None:
        if predictor.status.authority != "PAPER_LIVE":
            raise ValueError("model must have PAPER_LIVE authority")
        if predictor.prediction_kind != "probability":
            raise ValueError("live model execution requires probability predictions")
        if not Decimal("0.5") < decision_gate <= Decimal("1"):
            raise ValueError("model decision gate must be greater than 0.5 and at most 1")
        self._predictor = predictor
        self._decision_gate = decision_gate

    def signal(self, vector: FeatureVector) -> EntrySignal | None:
        prediction = self._predictor.predict(vector)
        value = Decimal(str(prediction.value))
        if value >= self._decision_gate:
            side = Side.BUY
            confidence = value
        elif value <= Decimal("1") - self._decision_gate:
            side = Side.SELL
            confidence = Decimal("1") - value
        else:
            return None
        return EntrySignal(
            route=Route.MODEL_DIRECTIONAL,
            side=side,
            confidence=confidence,
            source=f"model-paper-v1:{prediction.run_id}:gate-{self._decision_gate}",
        )

    def create_intent(
        self,
        vector: FeatureVector,
        quote: QuoteSnapshot,
        *,
        signal: EntrySignal | None = None,
        now: datetime | None = None,
    ) -> TradeIntent | None:
        observed_now = now or datetime.now(UTC)
        if vector.schema != self._predictor.status.feature_schema or quote.symbol != vector.symbol:
            return None
        feature_age = observed_now - vector.observed_at
        quote_age = observed_now - quote.timestamp
        if not timedelta(0) <= feature_age <= self.MAX_FEATURE_AGE:
            return None
        if not -self.MAX_CLOCK_SKEW <= quote_age <= self.MAX_QUOTE_AGE:
            return None
        if quote.spread_bps > self.MAX_SPREAD_BPS:
            return None

        selected = signal or self.signal(vector)
        if selected is None:
            return None
        if selected.side is Side.BUY:
            entry_price = quote.ask_price.quantize(_CENT, rounding=ROUND_CEILING)
            stop_price = (entry_price * Decimal("0.98")).quantize(_CENT, rounding=ROUND_FLOOR)
            exposure_group = "us-equity:long"
        else:
            entry_price = quote.bid_price.quantize(_CENT, rounding=ROUND_FLOOR)
            stop_price = (entry_price * Decimal("1.02")).quantize(_CENT, rounding=ROUND_CEILING)
            exposure_group = "us-equity:short"
        intent_id = str(
            uuid5(
                NAMESPACE_URL,
                (
                    f"{selected.source}:{selected.side}:{vector.symbol}:"
                    f"{vector.observed_at.isoformat()}"
                ),
            )
        )
        return TradeIntent(
            intent_id=intent_id,
            route=selected.route,
            symbol=vector.symbol,
            instrument_type=InstrumentType.EQUITY,
            side=selected.side,
            confidence=selected.confidence,
            entry_price=entry_price,
            stop_price=stop_price,
            expected_horizon_minutes=240,
            exposure_group=exposure_group,
            quote_age_seconds=Decimal(str(max(0.0, quote_age.total_seconds()))),
            data_quality_passed=True,
        )


class OptionSelectionSource(Protocol):
    def select(
        self,
        underlying_symbol: str,
        direction: Side,
        underlying_price: Decimal,
        *,
        now: datetime | None = None,
    ) -> object: ...


class DirectionalOptionStrategy(ModelStrategy):
    """Maps model direction to a premium-bounded long call or long put."""

    def __init__(
        self,
        predictor: ModelPredictor,
        options: OptionSelectionSource,
        *,
        decision_gate: Decimal,
    ) -> None:
        super().__init__(predictor, decision_gate=decision_gate)
        self._options = options

    def create_intent(
        self,
        vector: FeatureVector,
        quote: QuoteSnapshot,
        *,
        signal: EntrySignal | None = None,
        now: datetime | None = None,
    ) -> TradeIntent | None:
        from catalyst_router.options import OptionSelection

        observed_now = now or datetime.now(UTC)
        if vector.schema != self._predictor.status.feature_schema or quote.symbol != vector.symbol:
            return None
        feature_age = observed_now - vector.observed_at
        quote_age = observed_now - quote.timestamp
        if not timedelta(0) <= feature_age <= self.MAX_FEATURE_AGE:
            return None
        if not -self.MAX_CLOCK_SKEW <= quote_age <= self.MAX_QUOTE_AGE:
            return None
        if quote.spread_bps > self.MAX_SPREAD_BPS:
            return None
        selected_signal = signal or self.signal(vector)
        if selected_signal is None:
            return None
        underlying_price = (quote.bid_price + quote.ask_price) / Decimal("2")
        selection = cast(
            OptionSelection,
            self._options.select(
                vector.symbol,
                selected_signal.side,
                underlying_price,
                now=observed_now,
            ),
        )
        contract = selection.selected
        if contract is None:
            return None
        entry_price = contract.ask_price.quantize(_CENT, rounding=ROUND_CEILING)
        stop_price = (entry_price * Decimal("0.70")).quantize(_CENT, rounding=ROUND_FLOOR)
        take_profit = (entry_price * Decimal("1.50")).quantize(_CENT, rounding=ROUND_CEILING)
        intent_id = str(
            uuid5(
                NAMESPACE_URL,
                (
                    f"{selected_signal.source}:long-option:{contract.contract_symbol}:"
                    f"{vector.observed_at.isoformat()}"
                ),
            )
        )
        direction = "bullish" if selected_signal.side is Side.BUY else "bearish"
        return TradeIntent(
            intent_id=intent_id,
            route=selected_signal.route,
            symbol=contract.contract_symbol,
            underlying_symbol=vector.symbol,
            instrument_type=InstrumentType.OPTION,
            option_type=(OptionType.CALL if selected_signal.side is Side.BUY else OptionType.PUT),
            option_expiration_date=contract.expiration_date,
            side=Side.BUY,
            confidence=selected_signal.confidence,
            entry_price=entry_price,
            stop_price=stop_price,
            take_profit_price=take_profit,
            expected_horizon_minutes=240,
            exposure_group=f"us-options:{direction}",
            quote_age_seconds=Decimal(
                str(max(0.0, (observed_now - contract.quote_timestamp).total_seconds()))
            ),
            data_quality_passed=True,
            contract_multiplier=100,
        )


class QuoteSource(Protocol):
    def latest_quote(self, symbol: str) -> QuoteSnapshot: ...


class LiveTradingCycle:
    """Evaluates the incumbent and submits at most one protected equity entry per cycle."""

    MAX_ENTRY_NOTIONAL_RATE = Decimal("0.10")
    MAX_CONCURRENT_POSITIONS = RiskGovernor.MAX_POSITIONS
    MANAGED_OPTION_QUOTE_GRACE = timedelta(seconds=60)

    def __init__(
        self,
        *,
        store: OperationalStore,
        broker: PaperBroker,
        quotes: QuoteSource,
        strategy: EntryStrategy | None = None,
        risk_governor: RiskGovernor | None = None,
        option_quotes: QuoteSource | None = None,
    ) -> None:
        self._store = store
        self._broker = broker
        self._quotes = quotes
        self._strategy = strategy or IncumbentStrategy()
        self._risk = risk_governor or RiskGovernor()
        self._option_quotes = option_quotes
        self._option_quote_failures: dict[str, datetime] = {}
        self._gateway = ExecutionGateway(store=store, broker=broker)

    def run(
        self,
        vectors: tuple[FeatureVector, ...],
        *,
        expected_epoch: str,
        universe_id: str | None = None,
    ) -> tuple[DecisionRecord, ...]:
        agent = self._store.get_agent_state()
        if agent.mode is not AgentMode.RUNNING:
            return ()
        if agent.execution_epoch != expected_epoch or not agent.is_reconciled:
            raise RuntimeError("worker lost execution epoch ownership")

        snapshot = self._broker.reconciliation_snapshot()
        agent = self._store.update_equity_peak(snapshot.account.equity)
        until_close = snapshot.clock.next_close - snapshot.clock.timestamp
        agent, tracked = self._release_terminal_orders(agent, snapshot)

        close_flatten_due = bool(
            snapshot.positions or snapshot.open_orders
        ) and until_close <= timedelta(minutes=10)
        if close_flatten_due:
            self._broker.flatten()
            return ()
        expired = tuple(
            execution
            for execution in tracked.values()
            if execution.plan.instrument_type is not InstrumentType.OPTION
            and snapshot.clock.timestamp >= execution.plan.expires_at
        )
        if expired:
            for execution in expired:
                self._broker.close_position(execution.plan.symbol)
            return ()

        option_exits = self._manage_option_exits(snapshot, tracked, expected_epoch)
        if option_exits or self._option_quote_failures:
            return option_exits

        last_equity = snapshot.account.last_equity or snapshot.account.equity
        equity_peak = agent.equity_peak or max(last_equity, snapshot.account.equity)
        group_open_risk = self._group_open_risk(tracked)
        portfolio = PortfolioRiskState(
            equity=snapshot.account.equity,
            buying_power=min(
                snapshot.account.buying_power,
                snapshot.account.equity * self.MAX_ENTRY_NOTIONAL_RATE,
            ),
            position_count=max(len(snapshot.positions), len(tracked)),
            total_open_risk=sum(group_open_risk.values(), Decimal("0")),
            overnight_open_risk=Decimal("0"),
            group_open_risk=group_open_risk,
            daily_pnl=snapshot.account.equity - last_equity,
            competition_drawdown=max(
                Decimal("0"),
                (equity_peak - snapshot.account.equity) / equity_peak,
            ),
        )
        publish_public_portfolio(
            self._store,
            build_public_portfolio_point(snapshot, agent, tracked.values()),
            expected_epoch=expected_epoch,
        )
        daily_loss = max(Decimal("0"), -portfolio.daily_pnl)
        if portfolio.competition_drawdown >= RiskGovernor.MAX_COMPETITION_DRAWDOWN_RATE:
            self._halt_and_flatten(AgentMode.KILLED, "competition drawdown kill threshold reached")
            return ()
        if daily_loss >= portfolio.equity * RiskGovernor.MAX_DAILY_LOSS_RATE:
            self._halt_and_flatten(AgentMode.RISK_HALTED, "daily loss circuit breaker reached")
            return ()
        if not self._every_position_is_protected(snapshot, tracked):
            self._halt_and_flatten(
                AgentMode.RISK_HALTED,
                "broker position is missing protective bracket exits",
            )
            return ()
        if (
            not snapshot.clock.is_open
            or snapshot.account.trading_blocked
            or until_close <= timedelta(minutes=15)
            or len(agent.active_order_ids) >= self.MAX_CONCURRENT_POSITIONS
        ):
            return ()
        committed = {execution.plan.symbol for execution in tracked.values()}
        committed_underlyings = {
            execution.plan.underlying_symbol or execution.plan.symbol
            for execution in tracked.values()
        }
        committed.update(position.symbol for position in snapshot.positions)
        committed.update(order.symbol for order in snapshot.open_orders)
        candidates = [
            (signal, vector)
            for vector in vectors
            if vector.symbol not in committed
            and vector.symbol not in committed_underlyings
            and (signal := self._strategy.signal(vector)) is not None
        ]
        for signal, vector in sorted(
            candidates, key=lambda item: (-item[0].confidence, item[1].symbol)
        ):
            quote = self._quotes.latest_quote(vector.symbol)
            intent = self._strategy.create_intent(vector, quote, signal=signal)
            if intent is None:
                continue
            if universe_id is not None:
                intent = intent.model_copy(update={"universe_id": universe_id})
            risk = self._risk.evaluate(
                intent,
                agent,
                portfolio,
                market_is_open=snapshot.clock.is_open,
                trading_blocked=snapshot.account.trading_blocked,
                options_trading_level=snapshot.account.options_trading_level,
                options_buying_power=(
                    min(
                        snapshot.account.options_buying_power,
                        snapshot.account.equity * self.MAX_ENTRY_NOTIONAL_RATE,
                    )
                    if snapshot.account.options_buying_power is not None
                    else None
                ),
            )
            if risk.status is RiskDecisionStatus.VETOED:
                return self._persist_risk_veto(intent, risk, expected_epoch)
            approvals = self._persist_risk_approval(intent, risk, portfolio, expected_epoch)
            execution = self._gateway.execute(
                intent,
                risk,
                expected_epoch=expected_epoch,
                max_active_orders=self.MAX_CONCURRENT_POSITIONS,
            )
            return approvals + self._persist_execution(intent, risk, execution, expected_epoch)
        return ()

    def manage(self, *, expected_epoch: str) -> tuple[DecisionRecord, ...]:
        return self.run((), expected_epoch=expected_epoch)

    @staticmethod
    def _group_open_risk(tracked: dict[str, OrderExecution]) -> dict[str, Decimal]:
        group_open_risk: dict[str, Decimal] = {}
        for execution in tracked.values():
            plan = execution.plan
            group_open_risk[plan.exposure_group] = (
                group_open_risk.get(plan.exposure_group, Decimal("0")) + plan.risk_amount
            )
        return group_open_risk

    def _every_position_is_protected(
        self, snapshot: ReconciliationSnapshot, tracked: dict[str, OrderExecution]
    ) -> bool:
        protected: set[str] = set()
        positions = {position.symbol: position for position in snapshot.positions}
        for client_order_id, execution in tracked.items():
            if execution.plan.instrument_type is InstrumentType.OPTION:
                position = positions.get(execution.plan.symbol)
                expiration = execution.plan.option_expiration_date
                if (
                    position is not None
                    and position.quantity > 0
                    and expiration is not None
                    and expiration > snapshot.clock.timestamp.date()
                ):
                    protected.add(execution.plan.symbol)
                continue
            broker_order = self._broker.get_order_by_client_id(client_order_id)
            if (
                broker_order is not None
                and broker_order.has_active_take_profit
                and broker_order.has_active_stop_loss
            ):
                protected.add(execution.plan.symbol)
        return all(position.symbol in protected for position in snapshot.positions)

    def _manage_option_exits(
        self,
        snapshot: ReconciliationSnapshot,
        tracked: dict[str, OrderExecution],
        expected_epoch: str,
    ) -> tuple[DecisionRecord, ...]:
        positions = {position.symbol: position for position in snapshot.positions}
        managed_symbols = {
            execution.plan.symbol
            for execution in tracked.values()
            if execution.plan.instrument_type is InstrumentType.OPTION
            and execution.plan.symbol in positions
        }
        self._option_quote_failures = {
            symbol: failed_at
            for symbol, failed_at in self._option_quote_failures.items()
            if symbol in managed_symbols
        }
        for client_order_id, execution in tracked.items():
            plan = execution.plan
            position = positions.get(plan.symbol)
            if plan.instrument_type is not InstrumentType.OPTION or position is None:
                continue
            if self._option_quotes is None:
                raise RuntimeError("option position supervision is not configured")
            reason = None
            quote: QuoteSnapshot | None = None
            if snapshot.clock.timestamp >= plan.expires_at:
                reason = "model horizon elapsed"
            else:
                try:
                    quote = self._option_quotes.latest_quote(plan.symbol)
                except Exception:
                    if self._record_option_quote_failure(
                        execution,
                        snapshot.clock.timestamp,
                        f"option quote unavailable for managed position {plan.symbol}",
                    ):
                        return ()
                    continue
                quote_age = snapshot.clock.timestamp - quote.timestamp
                if quote.feed != "indicative" or not (
                    -ModelStrategy.MAX_CLOCK_SKEW <= quote_age <= timedelta(seconds=5)
                ):
                    if self._record_option_quote_failure(
                        execution,
                        snapshot.clock.timestamp,
                        f"option quote failed quality gates for managed position {plan.symbol}",
                    ):
                        return ()
                    continue
                failed_at = (
                    execution.option_quote_failure_started_at
                    or self._option_quote_failures.pop(plan.symbol, None)
                )
                if failed_at is not None:
                    self._option_quote_failures.pop(plan.symbol, None)
                    recovered = execution.model_copy(
                        update={
                            "option_quote_failure_started_at": None,
                            "version": execution.version + 1,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                    self._store.update_order(recovered, expected_status=execution.status)
                    logger.info(
                        "managed option quote recovered for %s after %.1f seconds",
                        plan.symbol,
                        (snapshot.clock.timestamp - failed_at).total_seconds(),
                    )
                if quote.bid_price <= plan.stop_price:
                    reason = "premium stop reached"
                elif quote.bid_price >= plan.take_profit_price:
                    reason = "premium target reached"
            if reason is None:
                continue
            exit_client_order_id = (
                "crx-" + uuid5(NAMESPACE_URL, f"option-exit-v1:{plan.client_order_id}").hex
            )
            broker_exit = self._broker.get_order_by_client_id(exit_client_order_id)
            if broker_exit is not None and self._is_terminal_rejection(broker_exit.status):
                self._halt_and_flatten(
                    AgentMode.RISK_HALTED,
                    f"option exit {exit_client_order_id} was rejected or canceled",
                )
                return ()
            if broker_exit is None:
                try:
                    broker_exit = self._broker.submit_option_exit(
                        plan.symbol,
                        int(position.quantity),
                        exit_client_order_id,
                    )
                except Exception:
                    self._halt_and_flatten(
                        AgentMode.RISK_HALTED,
                        f"option exit {exit_client_order_id} submission failed",
                    )
                    return ()
                if self._is_terminal_rejection(broker_exit.status):
                    self._halt_and_flatten(
                        AgentMode.RISK_HALTED,
                        f"option exit {exit_client_order_id} was rejected",
                    )
                    return ()
            record = DecisionRecord.create(
                decision_id=str(uuid5(NAMESPACE_URL, f"option-exit:{client_order_id}:{reason}")),
                decision_type="OPTION_EXIT",
                route=Route.MODEL_DIRECTIONAL,
                symbol=plan.underlying_symbol or plan.symbol,
                summary=f"closed {plan.symbol}: {reason}",
                payload={
                    "contract_symbol": plan.symbol,
                    "reason": reason,
                    "bid_price": str(quote.bid_price) if quote is not None else None,
                    "exit_client_order_id": exit_client_order_id,
                    "broker_status": broker_exit.status,
                    "universe_id": plan.universe_id,
                },
                public=True,
                public_summary=f"Closed option position: {reason}",
            )
            return (
                (record,)
                if self._store.append_decision_once(record, expected_epoch=expected_epoch)
                else ()
            )
        return ()

    def _record_option_quote_failure(
        self, execution: OrderExecution, observed_at: datetime, reason: str
    ) -> bool:
        symbol = execution.plan.symbol
        failed_at = execution.option_quote_failure_started_at
        if failed_at is None:
            failed_at = observed_at
            degraded = execution.model_copy(
                update={
                    "option_quote_failure_started_at": failed_at,
                    "version": execution.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._store.update_order(degraded, expected_status=execution.status)
        self._option_quote_failures[symbol] = failed_at
        failure_age = observed_at - failed_at
        if failure_age < self.MANAGED_OPTION_QUOTE_GRACE:
            logger.warning(
                "%s; pausing new entries during %.1f/%.1f second grace period",
                reason,
                failure_age.total_seconds(),
                self.MANAGED_OPTION_QUOTE_GRACE.total_seconds(),
            )
            return False
        self._halt_and_flatten(AgentMode.RISK_HALTED, reason)
        return True

    @staticmethod
    def _is_terminal_rejection(status: str) -> bool:
        return status.rsplit(".", 1)[-1].lower() in {
            "canceled",
            "expired",
            "rejected",
            "replaced",
        }

    def _release_terminal_orders(
        self, agent: AgentState, snapshot: ReconciliationSnapshot
    ) -> tuple[AgentState, dict[str, OrderExecution]]:
        tracked: dict[str, OrderExecution] = {}
        released = False
        for client_order_id in agent.active_order_ids:
            execution = self._store.get_order(client_order_id)
            if execution is None:
                raise RuntimeError("active order has no durable execution record")
            if self._release_terminal_order(client_order_id, snapshot, execution):
                released = True
                continue
            tracked[client_order_id] = execution
        return (self._store.get_agent_state() if released else agent), tracked

    def _release_terminal_order(
        self,
        client_order_id: str,
        snapshot: ReconciliationSnapshot,
        execution: OrderExecution,
    ) -> bool:
        broker_order = self._broker.get_order_by_client_id(client_order_id)
        status = (
            broker_order.status.rsplit(".", 1)[-1].lower()
            if broker_order is not None
            else "missing"
        )
        terminal = status in {"canceled", "expired", "filled", "rejected", "replaced"}
        expired_and_missing = (
            status == "missing" and snapshot.clock.timestamp >= execution.plan.expires_at
        )
        symbol = execution.plan.symbol
        symbol_is_flat = not any(
            position.symbol == symbol for position in snapshot.positions
        ) and not any(order.symbol == symbol for order in snapshot.open_orders)
        if (terminal or expired_and_missing) and symbol_is_flat:
            self._store.clear_active_order(client_order_id)
            return True
        return False

    def _halt_and_flatten(self, mode: AgentMode, reason: str) -> None:
        self._store.transition_agent_mode(
            mode,
            reason=reason,
            record=DecisionRecord.create(
                decision_type="CIRCUIT_BREAKER",
                summary=reason,
                payload={"mode": mode, "reason": reason},
            ),
        )
        self._broker.flatten()

    def _persist_risk_veto(
        self, intent: TradeIntent, risk: RiskDecision, expected_epoch: str
    ) -> tuple[DecisionRecord, ...]:
        record = DecisionRecord.create(
            decision_id=str(uuid5(NAMESPACE_URL, f"risk-veto:{intent.intent_id}")),
            decision_type="RISK_VETO",
            route=intent.route,
            symbol=intent.underlying_symbol or intent.symbol,
            summary="; ".join(risk.checks),
            payload={
                "intent_id": intent.intent_id,
                "contract_symbol": intent.symbol,
                "universe_id": intent.universe_id,
                "checks": risk.checks,
            },
            public=True,
            public_summary=f"Risk governor vetoed {intent.symbol}",
        )
        return (
            (record,)
            if self._store.append_decision_once(record, expected_epoch=expected_epoch)
            else ()
        )

    def _persist_risk_approval(
        self,
        intent: TradeIntent,
        risk: RiskDecision,
        portfolio: PortfolioRiskState,
        expected_epoch: str,
    ) -> tuple[DecisionRecord, ...]:
        record = DecisionRecord.create(
            decision_id=str(uuid5(NAMESPACE_URL, f"risk-approval:{intent.intent_id}")),
            decision_type="RISK_APPROVAL",
            route=intent.route,
            symbol=intent.underlying_symbol or intent.symbol,
            summary=f"approved {risk.quantity} units with {risk.risk_amount} maximum risk",
            payload={
                "intent_id": intent.intent_id,
                "status": risk.status,
                "quantity": risk.quantity,
                "risk_amount": str(risk.risk_amount),
                "checks": risk.checks,
                "contract_symbol": intent.symbol,
                "universe_id": intent.universe_id,
                "account_snapshot": {
                    "equity": str(portfolio.equity),
                    "buying_power": str(portfolio.buying_power),
                    "position_count": portfolio.position_count,
                    "total_open_risk": str(portfolio.total_open_risk),
                    "daily_pnl": str(portfolio.daily_pnl),
                    "competition_drawdown": str(portfolio.competition_drawdown),
                },
            },
        )
        return (
            (record,)
            if self._store.append_decision_once(record, expected_epoch=expected_epoch)
            else ()
        )

    def _persist_execution(
        self,
        intent: TradeIntent,
        risk: RiskDecision,
        execution: OrderExecution,
        expected_epoch: str,
    ) -> tuple[DecisionRecord, ...]:
        record = DecisionRecord.create(
            decision_id=str(
                uuid5(NAMESPACE_URL, f"order-execution:{execution.plan.client_order_id}")
            ),
            decision_type="ORDER_EXECUTION",
            route=intent.route,
            symbol=intent.underlying_symbol or intent.symbol,
            summary=(
                f"{execution.status} paper order {execution.plan.client_order_id} "
                f"for {execution.plan.quantity} {intent.symbol}"
            ),
            payload={
                "client_order_id": execution.plan.client_order_id,
                "status": execution.status,
                "quantity": execution.plan.quantity,
                "risk_amount": str(risk.risk_amount),
                "limit_price": str(execution.plan.limit_price),
                "stop_price": str(execution.plan.stop_price),
                "take_profit_price": str(execution.plan.take_profit_price),
                "contract_symbol": intent.symbol,
                "universe_id": intent.universe_id,
            },
            public=True,
            public_summary=f"Paper order {execution.status.lower()} for {intent.symbol}",
        )
        return (
            (record,)
            if self._store.append_decision_once(record, expected_epoch=expected_epoch)
            else ()
        )


class ExecutionGateway:
    def __init__(self, *, store: OperationalStore, broker: PaperBroker) -> None:
        self._store = store
        self._broker = broker

    def execute(
        self,
        intent: TradeIntent,
        risk: RiskDecision,
        *,
        expected_epoch: str,
        max_active_orders: int,
    ) -> OrderExecution:
        if risk.intent_id != intent.intent_id or risk.status is RiskDecisionStatus.VETOED:
            raise ValueError("an approved risk decision matching the intent is required")
        plan = self._plan(intent, risk)
        prepared = OrderExecution(plan=plan, request_hash=self._request_hash(plan))
        claimed = self._store.claim_order(
            prepared, expected_epoch=expected_epoch, max_active_orders=max_active_orders
        )
        execution = prepared if claimed else self._store.get_order(plan.client_order_id)
        if execution is None:
            raise RuntimeError("claimed order execution could not be loaded")
        if execution.request_hash != prepared.request_hash:
            raise RuntimeError("client order id was reused with a different request")
        if execution.status in {
            OrderExecutionStatus.ACKNOWLEDGED,
            OrderExecutionStatus.REJECTED,
        }:
            return execution

        broker_order = self._broker.get_order_by_client_id(plan.client_order_id)
        if broker_order is not None:
            return self._acknowledge(execution, broker_order)
        try:
            broker_order = self._broker.submit_order(plan)
        except Exception:
            broker_order = self._broker.get_order_by_client_id(plan.client_order_id)
            if broker_order is not None:
                return self._acknowledge(execution, broker_order)
            return self._transition(execution, OrderExecutionStatus.UNKNOWN)
        return self._acknowledge(execution, broker_order)

    @staticmethod
    def _plan(intent: TradeIntent, risk: RiskDecision) -> OrderPlan:
        client_order_id = "cr-" + uuid5(NAMESPACE_URL, f"paper-order-v1:{intent.intent_id}").hex
        take_profit = intent.take_profit_price or (
            (intent.entry_price * Decimal("1.04")).quantize(_CENT, rounding=ROUND_CEILING)
            if intent.side is Side.BUY
            else (intent.entry_price * Decimal("0.96")).quantize(_CENT, rounding=ROUND_FLOOR)
        )
        created_at = datetime.now(UTC)
        return OrderPlan(
            client_order_id=client_order_id,
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=risk.quantity,
            limit_price=intent.entry_price,
            stop_price=intent.stop_price,
            take_profit_price=take_profit,
            risk_amount=risk.risk_amount,
            exposure_group=intent.exposure_group,
            instrument_type=intent.instrument_type,
            underlying_symbol=intent.underlying_symbol,
            option_type=intent.option_type,
            option_expiration_date=intent.option_expiration_date,
            universe_id=intent.universe_id,
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=intent.expected_horizon_minutes),
        )

    @staticmethod
    def _request_hash(plan: OrderPlan) -> str:
        if plan.instrument_type is InstrumentType.OPTION:
            request = {
                "client_order_id": plan.client_order_id,
                "limit_price": str(plan.limit_price),
                "position_intent": "buy_to_open",
                "quantity": plan.quantity,
                "side": plan.side,
                "symbol": plan.symbol,
                "time_in_force": "day",
                "type": "limit",
            }
        else:
            request = {
                "client_order_id": plan.client_order_id,
                "limit_price": str(plan.limit_price),
                "quantity": plan.quantity,
                "side": plan.side,
                "stop_price": str(plan.stop_price),
                "symbol": plan.symbol,
                "take_profit_price": str(plan.take_profit_price),
                "time_in_force": "day",
                "type": "limit",
            }
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _acknowledge(
        self, execution: OrderExecution, broker_order: BrokerOrderSnapshot
    ) -> OrderExecution:
        plan = execution.plan
        if (
            broker_order.client_order_id != plan.client_order_id
            or broker_order.symbol != plan.symbol
            or broker_order.side is not plan.side
            or broker_order.quantity != plan.quantity
        ):
            raise RuntimeError("Alpaca order does not match the durable order plan")
        status = broker_order.status.rsplit(".", 1)[-1].lower()
        target = (
            OrderExecutionStatus.REJECTED
            if status in {"canceled", "expired", "rejected", "replaced"}
            else OrderExecutionStatus.ACKNOWLEDGED
        )
        updated = self._transition(
            execution,
            target,
            alpaca_order_id=broker_order.order_id,
            broker_status=broker_order.status,
        )
        if target is OrderExecutionStatus.REJECTED:
            self._store.clear_active_order(plan.client_order_id)
        return updated

    def _transition(
        self,
        execution: OrderExecution,
        status: OrderExecutionStatus,
        *,
        alpaca_order_id: str | None = None,
        broker_status: str | None = None,
    ) -> OrderExecution:
        updated = execution.model_copy(
            update={
                "status": status,
                "version": execution.version + 1,
                "alpaca_order_id": alpaca_order_id or execution.alpaca_order_id,
                "broker_status": broker_status or execution.broker_status,
                "updated_at": datetime.now(UTC),
            }
        )
        return self._store.update_order(updated, expected_status=execution.status)
