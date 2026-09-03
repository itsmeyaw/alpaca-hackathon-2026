from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.challenger import PublicChallengerStatus, ShadowPrediction
from catalyst_router.domain import (
    AccountSnapshot,
    AgentMode,
    BrokerOrderSnapshot,
    DecisionRecord,
    InstrumentType,
    MarketClockSnapshot,
    OptionType,
    OrderExecutionStatus,
    OrderPlan,
    PositionSnapshot,
    QuoteSnapshot,
    ReconciliationSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    Route,
    Side,
    TradeIntent,
)
from catalyst_router.execution import (
    ExecutionGateway,
    IncumbentStrategy,
    LiveTradingCycle,
    ModelStrategy,
)
from catalyst_router.training import (
    FEATURE_NAMES,
    FEATURE_NAMES_V2,
    FEATURE_SCHEMA,
    FEATURE_SCHEMA_V2,
    FeatureVector,
)


class FakeBroker:
    def __init__(
        self,
        *,
        lose_first_response: bool = False,
        submit_status: str = "accepted",
        equity: Decimal = Decimal("100000"),
        last_equity: Decimal = Decimal("100000"),
        track_positions: bool = False,
        options_trading_level: int = 0,
        options_buying_power: Decimal | None = None,
        minutes_to_close: int = 60,
        option_exit_status: str = "accepted",
    ) -> None:
        self.orders: dict[str, BrokerOrderSnapshot] = {}
        self.submissions = 0
        self.lose_first_response = lose_first_response
        self.submit_status = submit_status
        self.equity = equity
        self.last_equity = last_equity
        self.flattened = False
        self.track_positions = track_positions
        self.closed: list[str] = []
        self.plans: dict[str, OrderPlan] = {}
        self.options_trading_level = options_trading_level
        self.options_buying_power = options_buying_power
        self.minutes_to_close = minutes_to_close
        self.option_exit_status = option_exit_status

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        return self.orders.get(client_order_id)

    def submit_order(self, plan: OrderPlan) -> BrokerOrderSnapshot:
        client_order_id = plan.client_order_id
        order = BrokerOrderSnapshot(
            order_id="alpaca-order-1",
            client_order_id=client_order_id,
            symbol=plan.symbol,
            side=plan.side,
            quantity=plan.quantity,
            status=self.submit_status,
            has_active_take_profit=self.track_positions,
            has_active_stop_loss=self.track_positions,
        )
        self.submissions += 1
        self.orders[client_order_id] = order
        self.plans[client_order_id] = plan
        if self.lose_first_response:
            self.lose_first_response = False
            raise TimeoutError("response lost after acceptance")
        return order

    def submit_option_exit(
        self, symbol: str, quantity: int, client_order_id: str
    ) -> BrokerOrderSnapshot:
        order = BrokerOrderSnapshot(
            order_id="alpaca-exit-1",
            client_order_id=client_order_id,
            symbol=symbol,
            side=Side.SELL,
            quantity=quantity,
            status=self.option_exit_status,
        )
        self.closed.append(symbol)
        self.orders[client_order_id] = order
        return order

    def _held_positions(self) -> tuple[PositionSnapshot, ...]:
        if not self.track_positions:
            return ()
        return tuple(
            PositionSnapshot(
                symbol=order.symbol,
                asset_class=(
                    "us_option"
                    if self.plans[client_order_id].instrument_type is InstrumentType.OPTION
                    else "us_equity"
                ),
                quantity=Decimal(order.quantity),
                market_value=Decimal(order.quantity) * Decimal("100"),
                unrealized_pl=Decimal("0"),
            )
            for client_order_id, order in self.orders.items()
            if order.symbol not in self.closed and order.status != "canceled"
        )

    def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        now = datetime.now(UTC)
        return ReconciliationSnapshot(
            account=AccountSnapshot(
                equity=self.equity,
                buying_power=Decimal("100000"),
                cash=Decimal("100000"),
                portfolio_value=self.equity,
                trading_blocked=False,
                options_trading_level=self.options_trading_level,
                options_buying_power=self.options_buying_power,
                last_equity=self.last_equity,
            ),
            clock=MarketClockSnapshot(
                is_open=True,
                timestamp=now,
                next_open=now + timedelta(days=1),
                next_close=now + timedelta(minutes=self.minutes_to_close),
            ),
            positions=self._held_positions(),
            open_orders=(),
        )

    def close_position(self, symbol: str) -> None:
        self.closed.append(symbol)
        self.orders = {
            client_order_id: (
                order.model_copy(
                    update={
                        "status": "canceled",
                        "has_active_take_profit": False,
                        "has_active_stop_loss": False,
                    }
                )
                if order.symbol == symbol
                else order
            )
            for client_order_id, order in self.orders.items()
        }

    def flatten(self) -> None:
        self.flattened = True
        self.closed.extend(order.symbol for order in self.orders.values())
        self.orders = {
            client_order_id: order.model_copy(
                update={
                    "status": "canceled",
                    "has_active_take_profit": False,
                    "has_active_stop_loss": False,
                }
            )
            for client_order_id, order in self.orders.items()
        }


class FakeQuotes:
    def latest_quote(self, symbol: str) -> QuoteSnapshot:
        return quote(symbol=symbol)


class FakePredictor:
    def __init__(
        self,
        value: float,
        *,
        authority: Literal["SHADOW_ONLY", "PAPER_LIVE"] = "PAPER_LIVE",
        prediction_kind: Literal["probability", "return"] = "probability",
        feature_schema: str = FEATURE_SCHEMA,
    ) -> None:
        self.value = value
        self.status = PublicChallengerStatus(
            deployed=True,
            loaded=True,
            authority=authority,
            run_id="run-live",
            feature_schema=feature_schema,
            decision_gate=0.55,
        )
        self.prediction_kind: Literal["probability", "return"] = prediction_kind

    def predict(self, vector: FeatureVector) -> ShadowPrediction:
        return ShadowPrediction(
            symbol=vector.symbol,
            observed_at=vector.observed_at,
            run_id="run-live",
            value=self.value,
            signal="ABSTAIN",
        )


class SymbolPredictor:
    def __init__(self, values: dict[str, float]) -> None:
        self.values = values
        self.status = PublicChallengerStatus(
            deployed=True,
            loaded=True,
            authority="PAPER_LIVE",
            run_id="run-live",
            feature_schema=FEATURE_SCHEMA,
            decision_gate=0.55,
        )
        self.prediction_kind: Literal["probability", "return"] = "probability"

    def predict(self, vector: FeatureVector) -> ShadowPrediction:
        value = self.values[vector.symbol]
        return ShadowPrediction(
            symbol=vector.symbol,
            observed_at=vector.observed_at,
            run_id="run-live",
            value=value,
            signal="ABSTAIN",
        )


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


class ProtectedPositionBroker(UnprotectedPositionBroker):
    def __init__(self) -> None:
        super().__init__()
        self.orders["active-order"] = BrokerOrderSnapshot(
            order_id="alpaca-order-1",
            client_order_id="active-order",
            symbol="AAPL",
            side=Side.BUY,
            quantity=10,
            status="filled",
            has_active_take_profit=True,
            has_active_stop_loss=True,
        )

    def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        snapshot = super().reconciliation_snapshot()
        return snapshot.model_copy(update={"open_orders": ()})


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
            "return_15m": 0.001,
            "return_1h": -0.012,
            "relative_return_1h": -0.013,
            "vwap_distance": 0.001,
            "close_location_2h": -0.4,
            "cross_sectional_return_rank_1h": -0.5,
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


def option_intent() -> TradeIntent:
    return TradeIntent(
        intent_id="option-intent",
        route=Route.MODEL_DIRECTIONAL,
        symbol="AAPL260925C00100000",
        underlying_symbol="AAPL",
        instrument_type=InstrumentType.OPTION,
        option_type=OptionType.CALL,
        option_expiration_date=datetime(2026, 9, 25, tzinfo=UTC).date(),
        side=Side.BUY,
        confidence=Decimal("0.8"),
        entry_price=Decimal("4.00"),
        stop_price=Decimal("2.80"),
        take_profit_price=Decimal("6.00"),
        expected_horizon_minutes=240,
        exposure_group="us-options:bullish",
        quote_age_seconds=Decimal("1"),
        data_quality_passed=True,
        contract_multiplier=100,
    )


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
    assert IncumbentStrategy().create_intent(vector(return_15m=-0.001), quote()) is None


def test_model_strategy_authorizes_long_and_short_predictions_at_live_gate() -> None:
    long_intent = ModelStrategy(FakePredictor(0.56), decision_gate=Decimal("0.55")).create_intent(
        vector(), quote()
    )
    short_intent = ModelStrategy(FakePredictor(0.44), decision_gate=Decimal("0.55")).create_intent(
        vector(), quote()
    )

    assert long_intent is not None
    assert long_intent.route is Route.MODEL_DIRECTIONAL
    assert long_intent.side is Side.BUY
    assert long_intent.confidence == Decimal("0.56")
    assert long_intent.entry_price == Decimal("100.00")
    assert long_intent.stop_price == Decimal("98.00")
    assert short_intent is not None
    assert short_intent.route is Route.MODEL_DIRECTIONAL
    assert short_intent.side is Side.SELL
    assert short_intent.confidence == Decimal("0.56")
    assert short_intent.entry_price == Decimal("99.98")
    assert short_intent.stop_price == Decimal("101.98")


def test_authorized_fifteen_minute_model_contract_reaches_paper_intent() -> None:
    legacy_vector = FeatureVector(
        symbol="AAPL",
        observed_at=datetime.now(UTC) - timedelta(minutes=2),
        schema=FEATURE_SCHEMA_V2,
        names=FEATURE_NAMES_V2,
        values=(0.0,) * len(FEATURE_NAMES_V2),
    )
    strategy = ModelStrategy(
        FakePredictor(0.56, feature_schema=FEATURE_SCHEMA_V2),
        decision_gate=Decimal("0.55"),
    )

    intent = strategy.create_intent(legacy_vector, quote())

    assert intent is not None
    assert intent.route is Route.MODEL_DIRECTIONAL


def test_model_strategy_abstains_inside_live_gate() -> None:
    strategy = ModelStrategy(FakePredictor(0.54), decision_gate=Decimal("0.55"))

    assert strategy.create_intent(vector(), quote()) is None


def test_model_strategy_requires_live_probability_authority() -> None:
    try:
        ModelStrategy(
            FakePredictor(0.60, authority="SHADOW_ONLY"),
            decision_gate=Decimal("0.55"),
        )
    except ValueError as exc:
        assert str(exc) == "model must have PAPER_LIVE authority"
    else:
        raise AssertionError("shadow authority must not create a live model strategy")

    try:
        ModelStrategy(
            FakePredictor(0.60, prediction_kind="return"),
            decision_gate=Decimal("0.55"),
        )
    except ValueError as exc:
        assert str(exc) == "live model execution requires probability predictions"
    else:
        raise AssertionError("return predictions must not use probability execution gates")


def test_model_strategy_includes_exact_directional_gate_boundaries() -> None:
    long_signal = ModelStrategy(FakePredictor(0.55), decision_gate=Decimal("0.55")).signal(vector())
    short_signal = ModelStrategy(FakePredictor(0.45), decision_gate=Decimal("0.55")).signal(
        vector()
    )

    assert long_signal is not None and long_signal.side is Side.BUY
    assert short_signal is not None and short_signal.side is Side.SELL


def test_gateway_claims_and_submits_the_same_order_only_once() -> None:
    store = reconciled_store()
    broker = FakeBroker()
    intent = IncumbentStrategy().create_intent(vector(), quote())
    assert intent is not None
    gateway = ExecutionGateway(store=store, broker=broker)
    epoch = store.get_agent_state().execution_epoch

    first = gateway.execute(
        intent, approved(intent.intent_id), expected_epoch=epoch, max_active_orders=1
    )
    second = gateway.execute(
        intent, approved(intent.intent_id), expected_epoch=epoch, max_active_orders=1
    )

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

    execution = gateway.execute(
        intent, approved(intent.intent_id), expected_epoch=epoch, max_active_orders=1
    )

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
        max_active_orders=1,
    )

    assert execution.status is OrderExecutionStatus.REJECTED
    assert store.get_agent_state().active_order_ids == ()


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
            max_active_orders=1,
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
        gateway.execute(
            intent, approved(intent.intent_id), expected_epoch=epoch, max_active_orders=1
        ).status
        is OrderExecutionStatus.UNKNOWN
    )
    store.transition_agent_mode(
        AgentMode.PAUSED,
        reason="operator pause",
        record=DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="pause"),
    )

    try:
        gateway.execute(
            intent, approved(intent.intent_id), expected_epoch=epoch, max_active_orders=1
        )
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


def test_live_cycle_submits_protected_short_model_order() -> None:
    store = reconciled_store()
    broker = FakeBroker()
    cycle = LiveTradingCycle(
        store=store,
        broker=broker,
        quotes=FakeQuotes(),
        strategy=ModelStrategy(FakePredictor(0.44), decision_gate=Decimal("0.55")),
    )

    records = cycle.run((vector(),), expected_epoch=store.get_agent_state().execution_epoch)

    assert broker.submissions == 1
    assert {record.decision_type for record in records} == {
        "RISK_APPROVAL",
        "ORDER_EXECUTION",
    }
    execution = store.get_order(next(iter(broker.orders)))
    assert execution is not None
    assert execution.plan.side is Side.SELL
    assert execution.plan.limit_price == Decimal("99.98")
    assert execution.plan.stop_price == Decimal("101.98")
    assert execution.plan.take_profit_price == Decimal("95.98")


def test_live_cycle_selects_the_strongest_model_prediction() -> None:
    store = reconciled_store()
    broker = FakeBroker()
    strategy = ModelStrategy(
        SymbolPredictor({"AAPL": 0.56, "MSFT": 0.70}),
        decision_gate=Decimal("0.55"),
    )
    cycle = LiveTradingCycle(
        store=store,
        broker=broker,
        quotes=FakeQuotes(),
        strategy=strategy,
    )

    cycle.run(
        (vector(), vector(symbol="MSFT")),
        expected_epoch=store.get_agent_state().execution_epoch,
    )

    execution = store.get_order(next(iter(broker.orders)))
    assert execution is not None
    assert execution.plan.symbol == "MSFT"


def test_gateway_leases_up_to_the_concurrent_order_bound() -> None:
    store = reconciled_store()
    broker = FakeBroker()
    first = IncumbentStrategy().create_intent(vector(), quote())
    second = IncumbentStrategy().create_intent(vector(symbol="MSFT"), quote(symbol="MSFT"))
    third = IncumbentStrategy().create_intent(vector(symbol="NVDA"), quote(symbol="NVDA"))
    assert first is not None and second is not None and third is not None
    gateway = ExecutionGateway(store=store, broker=broker)
    epoch = store.get_agent_state().execution_epoch

    gateway.execute(first, approved(first.intent_id), expected_epoch=epoch, max_active_orders=2)
    gateway.execute(second, approved(second.intent_id), expected_epoch=epoch, max_active_orders=2)

    assert len(store.get_agent_state().active_order_ids) == 2
    try:
        gateway.execute(third, approved(third.intent_id), expected_epoch=epoch, max_active_orders=2)
    except RuntimeError as exc:
        assert str(exc) == "agent is not authorized for new exposure"
    else:
        raise AssertionError("an entry beyond the bound should be transactionally rejected")
    assert broker.submissions == 2


def test_gateway_rejects_a_second_entry_when_the_bound_is_one() -> None:
    store = reconciled_store()
    broker = FakeBroker()
    first = IncumbentStrategy().create_intent(vector(), quote())
    second = IncumbentStrategy().create_intent(vector(symbol="MSFT"), quote(symbol="MSFT"))
    assert first is not None and second is not None
    gateway = ExecutionGateway(store=store, broker=broker)
    epoch = store.get_agent_state().execution_epoch
    gateway.execute(first, approved(first.intent_id), expected_epoch=epoch, max_active_orders=1)

    try:
        gateway.execute(
            second, approved(second.intent_id), expected_epoch=epoch, max_active_orders=1
        )
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


def test_live_cycle_accepts_nested_protective_legs_when_open_order_list_is_empty() -> None:
    store = reconciled_store()
    broker = ProtectedPositionBroker()
    intent = IncumbentStrategy().create_intent(vector(), quote())
    assert intent is not None
    gateway = ExecutionGateway(store=store, broker=broker)
    epoch = store.get_agent_state().execution_epoch
    gateway.execute(intent, approved(intent.intent_id), expected_epoch=epoch, max_active_orders=1)
    state = store.get_agent_state()
    assert len(state.active_order_ids) == 1
    leased = state.active_order_ids[0]
    broker.orders[leased] = broker.orders.pop("active-order").model_copy(
        update={"client_order_id": leased}
    )

    LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes()).run(
        (vector(),), expected_epoch=epoch
    )

    assert store.get_agent_state().mode is AgentMode.RUNNING
    assert not broker.flattened


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


def test_expired_entry_closes_only_its_symbol_and_releases_the_lease() -> None:
    store = reconciled_store()
    broker = FakeBroker()
    cycle = LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes())
    epoch = store.get_agent_state().execution_epoch
    signal = vector()
    cycle.run((signal,), expected_epoch=epoch)
    active_order_ids = store.get_agent_state().active_order_ids
    assert len(active_order_ids) == 1
    client_order_id = active_order_ids[0]
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

    assert broker.closed == ["AAPL"]
    assert not broker.flattened
    assert store.get_agent_state().active_order_ids == ()


def test_live_cycle_fills_multiple_slots_across_cycles() -> None:
    store = reconciled_store()
    broker = FakeBroker(track_positions=True)
    strategy = ModelStrategy(
        SymbolPredictor({"AAPL": 0.90, "MSFT": 0.80, "NVDA": 0.70}),
        decision_gate=Decimal("0.55"),
    )
    cycle = LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes(), strategy=strategy)
    epoch = store.get_agent_state().execution_epoch
    signals = (vector(), vector(symbol="MSFT"), vector(symbol="NVDA"))

    for _ in range(3):
        cycle.run(signals, expected_epoch=epoch)

    assert broker.submissions == 3
    active_order_ids = store.get_agent_state().active_order_ids
    assert len(active_order_ids) == 3
    held = {store.get_order(order_id).plan.symbol for order_id in active_order_ids}  # type: ignore[union-attr]
    assert held == {"AAPL", "MSFT", "NVDA"}


def test_live_cycle_enters_the_strongest_signal_first() -> None:
    store = reconciled_store()
    broker = FakeBroker(track_positions=True)
    strategy = ModelStrategy(
        SymbolPredictor({"AAPL": 0.60, "MSFT": 0.95}),
        decision_gate=Decimal("0.55"),
    )
    cycle = LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes(), strategy=strategy)
    epoch = store.get_agent_state().execution_epoch

    cycle.run((vector(), vector(symbol="MSFT")), expected_epoch=epoch)

    entered = store.get_agent_state().active_order_ids
    assert len(entered) == 1
    execution = store.get_order(entered[0])
    assert execution is not None and execution.plan.symbol == "MSFT"


def test_live_cycle_does_not_add_a_second_entry_for_a_held_symbol() -> None:
    store = reconciled_store()
    broker = FakeBroker(track_positions=True)
    strategy = ModelStrategy(SymbolPredictor({"AAPL": 0.90}), decision_gate=Decimal("0.55"))
    cycle = LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes(), strategy=strategy)
    epoch = store.get_agent_state().execution_epoch
    signals = (vector(),)

    cycle.run(signals, expected_epoch=epoch)
    cycle.run(signals, expected_epoch=epoch)
    cycle.run(signals, expected_epoch=epoch)

    assert broker.submissions == 1
    assert len(store.get_agent_state().active_order_ids) == 1


def test_live_cycle_stops_entering_at_the_concurrent_position_bound() -> None:
    store = reconciled_store()
    broker = FakeBroker(track_positions=True)
    symbols = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD")
    strategy = ModelStrategy(
        SymbolPredictor(dict.fromkeys(symbols, 0.90)),
        decision_gate=Decimal("0.55"),
    )
    cycle = LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes(), strategy=strategy)
    epoch = store.get_agent_state().execution_epoch
    signals = tuple(vector(symbol=symbol) for symbol in symbols)

    for _ in range(len(symbols)):
        cycle.run(signals, expected_epoch=epoch)

    assert broker.submissions == LiveTradingCycle.MAX_CONCURRENT_POSITIONS
    assert len(store.get_agent_state().active_order_ids) == (
        LiveTradingCycle.MAX_CONCURRENT_POSITIONS
    )


def test_live_cycle_reports_open_risk_of_held_positions_to_the_risk_governor() -> None:
    store = reconciled_store()
    broker = FakeBroker(track_positions=True)
    strategy = ModelStrategy(
        SymbolPredictor({"AAPL": 0.90, "MSFT": 0.80}),
        decision_gate=Decimal("0.55"),
    )
    cycle = LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes(), strategy=strategy)
    epoch = store.get_agent_state().execution_epoch
    signals = (vector(), vector(symbol="MSFT"))

    cycle.run(signals, expected_epoch=epoch)
    records = cycle.run(signals, expected_epoch=epoch)

    approval = next(record for record in records if record.decision_type == "RISK_APPROVAL")
    snapshot = approval.payload["account_snapshot"]
    assert snapshot["position_count"] == 1
    assert Decimal(snapshot["total_open_risk"]) > Decimal("0")
    public = store.list_public_portfolio()[-1]
    assert public.position_count == 1
    assert public.total_open_risk_rate > Decimal("0")
    assert public.max_group_open_risk_rate > Decimal("0")


def test_live_cycle_halts_when_any_held_position_loses_its_bracket() -> None:
    store = reconciled_store()
    broker = FakeBroker(track_positions=True)
    strategy = ModelStrategy(
        SymbolPredictor({"AAPL": 0.90, "MSFT": 0.80}),
        decision_gate=Decimal("0.55"),
    )
    cycle = LiveTradingCycle(store=store, broker=broker, quotes=FakeQuotes(), strategy=strategy)
    epoch = store.get_agent_state().execution_epoch
    signals = (vector(), vector(symbol="MSFT"))
    cycle.run(signals, expected_epoch=epoch)
    cycle.run(signals, expected_epoch=epoch)
    assert len(store.get_agent_state().active_order_ids) == 2
    unprotected = next(
        client_order_id
        for client_order_id, order in broker.orders.items()
        if order.symbol == "MSFT"
    )
    broker.orders[unprotected] = broker.orders[unprotected].model_copy(
        update={"has_active_stop_loss": False}
    )

    cycle.run(signals, expected_epoch=epoch)

    assert store.get_agent_state().mode is AgentMode.RISK_HALTED
    assert broker.flattened


def test_live_cycle_manages_a_long_option_premium_stop_without_bracket_legs() -> None:
    store = reconciled_store()
    broker = FakeBroker(
        track_positions=True,
        options_trading_level=2,
        options_buying_power=Decimal("100000"),
    )
    intent = option_intent()
    epoch = store.get_agent_state().execution_epoch
    ExecutionGateway(store=store, broker=broker).execute(
        intent,
        approved(intent.intent_id, quantity=1),
        expected_epoch=epoch,
        max_active_orders=6,
    )

    class OptionQuotes:
        def latest_quote(self, symbol: str) -> QuoteSnapshot:
            return quote(
                symbol=symbol,
                bid_price=Decimal("2.79"),
                ask_price=Decimal("2.81"),
                feed="indicative",
            )

    records = LiveTradingCycle(
        store=store,
        broker=broker,
        quotes=FakeQuotes(),
        option_quotes=OptionQuotes(),
    ).manage(expected_epoch=epoch)

    assert broker.closed == ["AAPL260925C00100000"]
    assert len(records) == 1
    assert records[0].decision_type == "OPTION_EXIT"


def test_live_cycle_flattens_long_options_at_the_session_cutoff() -> None:
    store = reconciled_store()
    broker = FakeBroker(track_positions=True, minutes_to_close=10)
    intent = option_intent()
    epoch = store.get_agent_state().execution_epoch
    ExecutionGateway(store=store, broker=broker).execute(
        intent,
        approved(intent.intent_id, quantity=1),
        expected_epoch=epoch,
        max_active_orders=6,
    )

    records = LiveTradingCycle(
        store=store,
        broker=broker,
        quotes=FakeQuotes(),
    ).manage(expected_epoch=epoch)

    assert records == ()
    assert broker.flattened


def test_rejected_managed_option_exit_risk_halts_and_flattens() -> None:
    store = reconciled_store()
    broker = FakeBroker(
        track_positions=True,
        option_exit_status="rejected",
    )
    intent = option_intent()
    epoch = store.get_agent_state().execution_epoch
    ExecutionGateway(store=store, broker=broker).execute(
        intent,
        approved(intent.intent_id, quantity=1),
        expected_epoch=epoch,
        max_active_orders=6,
    )

    class OptionQuotes:
        def latest_quote(self, symbol: str) -> QuoteSnapshot:
            return quote(
                symbol=symbol,
                bid_price=Decimal("2.79"),
                ask_price=Decimal("2.81"),
                feed="indicative",
            )

    LiveTradingCycle(
        store=store,
        broker=broker,
        quotes=FakeQuotes(),
        option_quotes=OptionQuotes(),
    ).manage(expected_epoch=epoch)

    assert store.get_agent_state().mode is AgentMode.RISK_HALTED
    assert broker.flattened


def test_missing_managed_option_quote_risk_halts_and_flattens() -> None:
    store = reconciled_store()
    broker = FakeBroker(track_positions=True)
    intent = option_intent()
    epoch = store.get_agent_state().execution_epoch
    ExecutionGateway(store=store, broker=broker).execute(
        intent,
        approved(intent.intent_id, quantity=1),
        expected_epoch=epoch,
        max_active_orders=6,
    )

    class MissingOptionQuotes:
        def latest_quote(self, symbol: str) -> QuoteSnapshot:
            raise RuntimeError(f"no quote for {symbol}")

    LiveTradingCycle(
        store=store,
        broker=broker,
        quotes=FakeQuotes(),
        option_quotes=MissingOptionQuotes(),
    ).manage(expected_epoch=epoch)

    assert store.get_agent_state().mode is AgentMode.RISK_HALTED
    assert broker.flattened


def test_agent_state_migrates_a_legacy_single_active_order() -> None:
    from catalyst_router.domain import AgentState

    empty = AgentState.model_validate_json('{"active_order_id": null}')
    populated = AgentState.model_validate_json('{"active_order_id": "cr-legacy"}')

    assert empty.active_order_ids == ()
    assert populated.active_order_ids == ("cr-legacy",)


def test_model_strategy_accepts_a_quote_timestamped_ahead_by_clock_skew() -> None:
    strategy = ModelStrategy(FakePredictor(0.90), decision_gate=Decimal("0.55"))
    now = datetime.now(UTC)

    intent = strategy.create_intent(
        vector(), quote(timestamp=now + timedelta(milliseconds=10)), now=now
    )

    assert intent is not None
    assert intent.quote_age_seconds == Decimal("0.0")


def test_model_strategy_rejects_a_quote_beyond_the_clock_skew_tolerance() -> None:
    strategy = ModelStrategy(FakePredictor(0.90), decision_gate=Decimal("0.55"))
    now = datetime.now(UTC)

    assert (
        strategy.create_intent(vector(), quote(timestamp=now + timedelta(seconds=30)), now=now)
        is None
    )


def test_model_strategy_still_rejects_a_stale_quote() -> None:
    strategy = ModelStrategy(FakePredictor(0.90), decision_gate=Decimal("0.55"))
    now = datetime.now(UTC)

    assert (
        strategy.create_intent(vector(), quote(timestamp=now - timedelta(seconds=6)), now=now)
        is None
    )


def test_model_strategy_still_rejects_a_wide_spread() -> None:
    strategy = ModelStrategy(FakePredictor(0.90), decision_gate=Decimal("0.55"))
    now = datetime.now(UTC)

    assert (
        strategy.create_intent(
            vector(),
            quote(bid_price=Decimal("99.00"), ask_price=Decimal("100.00"), timestamp=now),
            now=now,
        )
        is None
    )
