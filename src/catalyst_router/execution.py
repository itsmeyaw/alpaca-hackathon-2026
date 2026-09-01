from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal, Protocol
from uuid import NAMESPACE_URL, uuid5

from catalyst_router.challenger import PublicChallengerStatus, ShadowPrediction
from catalyst_router.domain import (
    AgentMode,
    AgentState,
    BrokerOrderSnapshot,
    DecisionRecord,
    InstrumentType,
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
from catalyst_router.risk import RiskGovernor
from catalyst_router.training import FEATURE_SCHEMA, FeatureVector

_CENT = Decimal("0.01")


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
        if not timedelta(0) <= quote_age <= self.MAX_QUOTE_AGE:
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
            quote_age_seconds=Decimal(str(quote_age.total_seconds())),
            data_quality_passed=True,
        )


class QuoteSource(Protocol):
    def latest_quote(self, symbol: str) -> QuoteSnapshot: ...


class LiveTradingCycle:
    """Evaluates the incumbent and submits at most one protected equity entry."""

    MAX_ENTRY_NOTIONAL_RATE = Decimal("0.10")

    def __init__(
        self,
        *,
        store: OperationalStore,
        broker: PaperBroker,
        quotes: QuoteSource,
        strategy: EntryStrategy | None = None,
        risk_governor: RiskGovernor | None = None,
    ) -> None:
        self._store = store
        self._broker = broker
        self._quotes = quotes
        self._strategy = strategy or IncumbentStrategy()
        self._risk = risk_governor or RiskGovernor()
        self._gateway = ExecutionGateway(store=store, broker=broker)

    def run(
        self, vectors: tuple[FeatureVector, ...], *, expected_epoch: str
    ) -> tuple[DecisionRecord, ...]:
        agent = self._store.get_agent_state()
        if agent.mode is not AgentMode.RUNNING:
            return ()
        if agent.execution_epoch != expected_epoch or not agent.is_reconciled:
            raise RuntimeError("worker lost execution epoch ownership")

        snapshot = self._broker.reconciliation_snapshot()
        agent = self._store.update_equity_peak(snapshot.account.equity)
        active_execution = (
            self._store.get_order(agent.active_order_id)
            if agent.active_order_id is not None
            else None
        )
        until_close = snapshot.clock.next_close - snapshot.clock.timestamp
        if active_execution is not None:
            self._release_terminal_order(
                agent.active_order_id or active_execution.plan.client_order_id,
                snapshot,
                active_execution,
            )
            agent = self._store.get_agent_state()
            active_execution = (
                self._store.get_order(agent.active_order_id)
                if agent.active_order_id is not None
                else None
            )
        horizon_expired = (
            active_execution is not None
            and snapshot.clock.timestamp >= active_execution.plan.expires_at
        )
        close_flatten_due = bool(
            snapshot.positions or snapshot.open_orders
        ) and until_close <= timedelta(minutes=10)
        if horizon_expired or close_flatten_due:
            self._broker.flatten()
            return ()
        last_equity = snapshot.account.last_equity or snapshot.account.equity
        equity_peak = agent.equity_peak or max(last_equity, snapshot.account.equity)
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
        daily_loss = max(Decimal("0"), -portfolio.daily_pnl)
        if portfolio.competition_drawdown >= RiskGovernor.MAX_COMPETITION_DRAWDOWN_RATE:
            self._halt_and_flatten(AgentMode.KILLED, "competition drawdown kill threshold reached")
            return ()
        if daily_loss >= portfolio.equity * RiskGovernor.MAX_DAILY_LOSS_RATE:
            self._halt_and_flatten(AgentMode.RISK_HALTED, "daily loss circuit breaker reached")
            return ()
        if snapshot.positions and len(snapshot.open_orders) < 2:
            self._halt_and_flatten(
                AgentMode.RISK_HALTED,
                "broker position is missing protective bracket exits",
            )
            return ()
        if (
            agent.active_order_id is not None
            or not snapshot.clock.is_open
            or snapshot.account.trading_blocked
            or snapshot.positions
            or snapshot.open_orders
            or until_close <= timedelta(minutes=15)
        ):
            return ()
        for vector in sorted(vectors, key=lambda item: item.symbol):
            if self._strategy.route(vector) is None:
                continue
            quote = self._quotes.latest_quote(vector.symbol)
            intent = self._strategy.create_intent(vector, quote)
            if intent is None:
                continue
            risk = self._risk.evaluate(
                intent,
                agent,
                portfolio,
                market_is_open=snapshot.clock.is_open,
                trading_blocked=snapshot.account.trading_blocked,
            )
            if risk.status is RiskDecisionStatus.VETOED:
                return self._persist_risk_veto(intent, risk, expected_epoch)
            approvals = self._persist_risk_approval(intent, risk, portfolio, expected_epoch)
            execution = self._gateway.execute(intent, risk, expected_epoch=expected_epoch)
            return approvals + self._persist_execution(intent, risk, execution, expected_epoch)
        return ()

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
            symbol=intent.symbol,
            summary="; ".join(risk.checks),
            payload={"intent_id": intent.intent_id, "checks": risk.checks},
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
            symbol=intent.symbol,
            summary=f"approved {risk.quantity} shares with {risk.risk_amount} stop risk",
            payload={
                "intent_id": intent.intent_id,
                "status": risk.status,
                "quantity": risk.quantity,
                "risk_amount": str(risk.risk_amount),
                "checks": risk.checks,
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
            symbol=intent.symbol,
            summary=(
                f"{execution.status} paper bracket {execution.plan.client_order_id} "
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
            },
            public=True,
            public_summary=f"Paper bracket order {execution.status.lower()} for {intent.symbol}",
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
    ) -> OrderExecution:
        if risk.intent_id != intent.intent_id or risk.status is RiskDecisionStatus.VETOED:
            raise ValueError("an approved risk decision matching the intent is required")
        plan = self._plan(intent, risk)
        prepared = OrderExecution(plan=plan, request_hash=self._request_hash(plan))
        claimed = self._store.claim_order(prepared, expected_epoch=expected_epoch)
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
        take_profit = (
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
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=intent.expected_horizon_minutes),
        )

    @staticmethod
    def _request_hash(plan: OrderPlan) -> str:
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
