from datetime import UTC, datetime, timedelta
from decimal import Decimal

from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.domain import (
    AccountSnapshot,
    AgentMode,
    BrokerOrderSnapshot,
    DecisionRecord,
    MarketClockSnapshot,
    OrderExecutionStatus,
    OrderPlan,
    PositionSnapshot,
    QuoteSnapshot,
    ReconciliationSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    Route,
    Side,
)
from catalyst_router.execution import ExecutionGateway, IncumbentStrategy, LiveTradingCycle
from catalyst_router.training import FEATURE_NAMES, FEATURE_SCHEMA, FeatureVector


class FakeBroker:
    def __init__(
        self,
        *,
        lose_first_response: bool = False,
        submit_status: str = "accepted",
        equity: Decimal = Decimal("100000"),
        last_equity: Decimal = Decimal("100000"),
    ) -> None:
        self.orders: dict[str, BrokerOrderSnapshot] = {}
        self.submissions = 0
        self.lose_first_response = lose_first_response
        self.submit_status = submit_status
        self.equity = equity
        self.last_equity = last_equity
        self.flattened = False

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        return self.orders.get(client_order_id)

    def submit_order(self, plan: OrderPlan) -> BrokerOrderSnapshot:
        client_order_id = plan.client_order_id
        order = BrokerOrderSnapshot(
            order_id="alpaca-order-1",
            client_order_id=client_order_id,
            symbol=plan.symbol,
            side=Side.BUY,
            quantity=plan.quantity,
            status=self.submit_status,
        )
        self.submissions += 1
        self.orders[client_order_id] = order
        if self.lose_first_response:
            self.lose_first_response = False
            raise TimeoutError("response lost after acceptance")
        return order

    def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        now = datetime.now(UTC)
        return ReconciliationSnapshot(
            account=AccountSnapshot(
                equity=self.equity,
                buying_power=Decimal("100000"),
                cash=Decimal("100000"),
                portfolio_value=self.equity,
                trading_blocked=False,
                options_trading_level=0,
                last_equity=self.last_equity,
            ),
            clock=MarketClockSnapshot(
                is_open=True,
                timestamp=now,
                next_open=now + timedelta(days=1),
                next_close=now + timedelta(hours=1),
            ),
            positions=(),
            open_orders=(),
        )

    def flatten(self) -> None:
        self.flattened = True
        self.orders = {
            client_order_id: order.model_copy(update={"status": "canceled"})
            for client_order_id, order in self.orders.items()
        }


class FakeQuotes:
    def latest_quote(self, symbol: str) -> QuoteSnapshot:
        return quote(symbol=symbol)


class MissingOrderBroker(FakeBroker):
    def submit_order(self, plan: OrderPlan) -> BrokerOrderSnapshot:
        del plan
        self.submissions += 1
        raise TimeoutError("submission outcome is unknown")


class UnprotectedPositionBroker(FakeBroker):
    def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        snapshot = super().reconciliation_snapshot()
        return snapshot.model_copy(
            update={
                "positions": (
                    PositionSnapshot(
                        symbol="AAPL",
                        asset_class="us_equity",
                        quantity=Decimal("10"),
                        market_value=Decimal("1000"),
                        unrealized_pl=Decimal("0"),
                    ),
                ),
                "open_orders": (),
            }
        )


def reconciled_store(*, running: bool = True) -> InMemoryOperationalStore:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    if running:
        store.transition_agent_mode(
            AgentMode.RUNNING,
            reason="operator resume",
            record=DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="resume"),
        )
    return store


def vector(symbol: str = "AAPL", **updates: float) -> FeatureVector:
    values: dict[str, float] = dict.fromkeys(FEATURE_NAMES, 0.0)
    values.update(
        {
            "return_1": 0.001,
            "return_4": -0.012,
            "relative_return_4": -0.013,
            "vwap_distance": 0.001,
            "close_location_20": -0.4,
            "cross_sectional_return_rank_4": -0.5,
        }
    )
    values.update(updates)
    return FeatureVector(
        symbol=symbol,
        observed_at=datetime.now(UTC) - timedelta(minutes=2),
        schema=FEATURE_SCHEMA,
        names=FEATURE_NAMES,
        values=tuple(values[name] for name in FEATURE_NAMES),
    )


def quote(**updates: object) -> QuoteSnapshot:
    values: dict[str, object] = {
        "symbol": "AAPL",
        "bid_price": Decimal("99.98"),
        "ask_price": Decimal("100.00"),
        "timestamp": datetime.now(UTC) - timedelta(seconds=1),
        "feed": "iex",
    }
    values.update(updates)
    return QuoteSnapshot.model_validate(values)


def approved(intent_id: str, quantity: int = 10) -> RiskDecision:
    return RiskDecision(
        status=RiskDecisionStatus.APPROVED,
        intent_id=intent_id,
        quantity=quantity,
        risk_amount=Decimal("20"),
        checks=("approved",),
    )


def test_incumbent_creates_long_equity_reversion_intent_from_fresh_quote() -> None:
    intent = IncumbentStrategy().create_intent(vector(), quote())

    assert intent is not None
    assert intent.route is Route.LIQUIDITY_REVERSION
    assert intent.side is Side.BUY
    assert intent.entry_price == Decimal("100.00")
    assert intent.stop_price == Decimal("98.00")


def test_incumbent_rejects_stale_quote_and_unconfirmed_reversion() -> None:
    stale = quote(timestamp=datetime.now(UTC) - timedelta(seconds=6))

    assert IncumbentStrategy().create_intent(vector(), stale) is None
    assert IncumbentStrategy().create_intent(vector(return_1=-0.001), quote()) is None


def test_gateway_claims_and_submits_the_same_order_only_once() -> None:
    store = reconciled_store()
    broker = FakeBroker()
    intent = IncumbentStrategy().create_intent(vector(), quote())
    assert intent is not None
    gateway = ExecutionGateway(store=store, broker=broker)
    epoch = store.get_agent_state().execution_epoch

    first = gateway.execute(intent, approved(intent.intent_id), expected_epoch=epoch)
    second = gateway.execute(intent, approved(intent.intent_id), expected_epoch=epoch)

    assert first.status is OrderExecutionStatus.ACKNOWLEDGED
    assert second.status is OrderExecutionStatus.ACKNOWLEDGED
    assert first.plan.client_order_id == second.plan.client_order_id
    assert broker.submissions == 1


def test_gateway_recovers_response_lost_after_alpaca_acceptance() -> None:
    store = reconciled_store()
    broker = FakeBroker(lose_first_response=True)
    intent = IncumbentStrategy().create_intent(vector(), quote())
    assert intent is not None
    gateway = ExecutionGateway(store=store, broker=broker)
    epoch = store.get_agent_state().execution_epoch

    execution = gateway.execute(intent, approved(intent.intent_id), expected_epoch=epoch)

    assert execution.status is OrderExecutionStatus.ACKNOWLEDGED
    assert execution.alpaca_order_id == "alpaca-order-1"
    assert broker.submissions == 1


def test_gateway_records_broker_rejection_and_releases_entry_lease() -> None:
    store = reconciled_store()
    broker = FakeBroker(submit_status="rejected")
    intent = IncumbentStrategy().create_intent(vector(), quote())
    assert intent is not None

    execution = ExecutionGateway(store=store, broker=broker).execute(
        intent,
        approved(intent.intent_id),
        expected_epoch=store.get_agent_state().execution_epoch,
    )

    assert execution.status is OrderExecutionStatus.REJECTED
    assert store.get_agent_state().active_order_id is None


def test_gateway_refuses_to_claim_orders_while_paused() -> None:
    store = reconciled_store(running=False)
    broker = FakeBroker()
    intent = IncumbentStrategy().create_intent(vector(), quote())
    assert intent is not None

    try:
        ExecutionGateway(store=store, broker=broker).execute(
            intent,
            approved(intent.intent_id),
            expected_epoch=store.get_agent_state().execution_epoch,
        )
    except RuntimeError as exc:
        assert str(exc) == "agent is not authorized for new exposure"
    else:
        raise AssertionError("paused execution should fail before broker submission")
    assert broker.submissions == 0


def test_gateway_refuses_ambiguous_order_recovery_after_pause() -> None:
    store = reconciled_store()
    broker = MissingOrderBroker()
    intent = IncumbentStrategy().create_intent(vector(), quote())
    assert intent is not None
    gateway = ExecutionGateway(store=store, broker=broker)
    epoch = store.get_agent_state().execution_epoch
    assert (
        gateway.execute(intent, approved(intent.intent_id), expected_epoch=epoch).status
        is OrderExecutionStatus.UNKNOWN
    )
    store.transition_agent_mode(
        AgentMode.PAUSED,
        reason="operator pause",
        record=DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="pause"),
    )

    try:
        gateway.execute(intent, approved(intent.intent_id), expected_epoch=epoch)
    except RuntimeError as exc:
        assert str(exc) == "agent is not authorized for new exposure"
    else:
        raise AssertionError("paused worker must not recover an ambiguous order")
    assert broker.submissions == 1


def test_live_cycle_submits_one_risk_sized_protected_order() -> None:
    store = reconciled_store()
    broker = FakeBroker()
    cycle = LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes())

    records = cycle.run((vector(),), expected_epoch=store.get_agent_state().execution_epoch)

    assert broker.submissions == 1
    assert {record.decision_type for record in records} == {
        "RISK_APPROVAL",
        "ORDER_EXECUTION",
    }
    execution = store.get_order(next(iter(broker.orders)))
    assert execution is not None
    assert execution.plan.quantity == 100
    assert execution.plan.stop_price == Decimal("98.00")
    assert execution.plan.take_profit_price == Decimal("104.00")


def test_live_cycle_persists_a_single_active_entry_lease() -> None:
    store = reconciled_store()
    first_broker = FakeBroker()
    first = IncumbentStrategy().create_intent(vector(), quote())
    second = IncumbentStrategy().create_intent(vector(symbol="MSFT"), quote(symbol="MSFT"))
    assert first is not None and second is not None
    gateway = ExecutionGateway(store=store, broker=first_broker)
    epoch = store.get_agent_state().execution_epoch
    gateway.execute(first, approved(first.intent_id), expected_epoch=epoch)

    try:
        gateway.execute(second, approved(second.intent_id), expected_epoch=epoch)
    except RuntimeError as exc:
        assert str(exc) == "agent is not authorized for new exposure"
    else:
        raise AssertionError("a second active entry should be transactionally rejected")


def test_live_cycle_halts_and_flattens_at_daily_loss_limit() -> None:
    store = reconciled_store()
    broker = FakeBroker(equity=Decimal("96000"), last_equity=Decimal("100000"))

    records = LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes()).run(
        (vector(),), expected_epoch=store.get_agent_state().execution_epoch
    )

    assert records == ()
    assert store.get_agent_state().mode is AgentMode.RISK_HALTED
    assert broker.flattened


def test_live_cycle_flattens_a_position_without_bracket_exits() -> None:
    store = reconciled_store()
    broker = UnprotectedPositionBroker()

    LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes()).run(
        (vector(),), expected_epoch=store.get_agent_state().execution_epoch
    )

    assert store.get_agent_state().mode is AgentMode.RISK_HALTED
    assert broker.flattened


def test_live_cycle_persists_peak_and_kills_at_competition_drawdown() -> None:
    store = reconciled_store()
    store.update_equity_peak(Decimal("110000"))
    broker = FakeBroker(equity=Decimal("96000"), last_equity=Decimal("100000"))

    LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes()).run(
        (vector(),), expected_epoch=store.get_agent_state().execution_epoch
    )

    assert store.get_agent_state().equity_peak == Decimal("110000")
    assert store.get_agent_state().mode is AgentMode.KILLED
    assert broker.flattened


def test_flattened_expired_entry_releases_lease_on_next_cycle() -> None:
    store = reconciled_store()
    broker = FakeBroker()
    cycle = LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes())
    epoch = store.get_agent_state().execution_epoch
    signal = vector()
    cycle.run((signal,), expected_epoch=epoch)
    client_order_id = store.get_agent_state().active_order_id
    assert client_order_id is not None
    execution = store.get_order(client_order_id)
    assert execution is not None
    expired = execution.model_copy(
        update={
            "plan": execution.plan.model_copy(
                update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
            ),
            "version": execution.version + 1,
        }
    )
    store.update_order(expired, expected_status=execution.status)

    cycle.run((signal,), expected_epoch=epoch)
    cycle.run((signal,), expected_epoch=epoch)

    assert broker.flattened
    assert store.get_agent_state().active_order_id is None
