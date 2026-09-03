from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError

from catalyst_router.adapters.dynamodb import DynamoOperationalStore
from catalyst_router.domain import (
    AgentMode,
    DecisionRecord,
    OrderExecution,
    OrderExecutionStatus,
    OrderPlan,
    PublicPortfolioPoint,
    Route,
    Side,
)
from catalyst_router.universe import UniverseSnapshot


@pytest.fixture
def dynamodb_store() -> Iterator[DynamoOperationalStore]:
    endpoint = os.getenv("DYNAMODB_ENDPOINT_URL")
    if endpoint is None:
        pytest.skip("DYNAMODB_ENDPOINT_URL is not configured")
    table_name = f"catalyst-router-test-{uuid4().hex}"
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        endpoint_url=endpoint,
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    client.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    store = DynamoOperationalStore(
        table_name=table_name,
        competition_id="integration-test",
        region="us-east-1",
        endpoint_url=endpoint,
    )
    yield store
    client.delete_table(TableName=table_name)


@pytest.mark.integration
def test_dynamodb_store_fences_restart_and_keeps_decisions_immutable(
    dynamodb_store: DynamoOperationalStore,
) -> None:
    initial = dynamodb_store.initialize()
    started = dynamodb_store.begin_execution()

    assert initial.mode is AgentMode.PAUSED
    assert not started.is_reconciled
    reconciliation = DecisionRecord.create(
        decision_type="RECONCILIATION_COMPLETED",
        summary="internal reconciliation detail",
        public=True,
        public_summary="reconciliation completed",
    )
    assert dynamodb_store.commit_reconciliation(
        started.execution_epoch, reconciliation, equity=Decimal("100000")
    ).is_reconciled
    portfolio = PublicPortfolioPoint(
        captured_at=datetime.now(UTC),
        equity=Decimal("99900"),
        cash=Decimal("75000"),
        net_pnl=Decimal("-100"),
        daily_return=Decimal("-0.001"),
        competition_return=Decimal("-0.001"),
        drawdown=Decimal("0.001"),
        position_count=1,
        max_trade_risk_rate=Decimal("0.005"),
        total_open_risk_rate=Decimal("0.005"),
        overnight_open_risk_rate=Decimal("0"),
        max_group_open_risk_rate=Decimal("0.005"),
    )
    assert dynamodb_store.append_public_portfolio(portfolio, expected_epoch=started.execution_epoch)
    assert not dynamodb_store.append_public_portfolio(
        portfolio, expected_epoch=started.execution_epoch
    )
    assert dynamodb_store.list_public_portfolio() == [portfolio]
    universe = UniverseSnapshot(
        universe_id="universe-v1:2026-09-03",
        policy_version="universe-v1",
        session_date=date(2026, 9, 3),
        selected_at=datetime.now(UTC),
        symbols=("AAPL",),
        source_ranks={"AAPL": {"most_active": 1}},
        rejections={},
    )
    assert dynamodb_store.put_daily_universe(universe, expected_epoch=started.execution_epoch)
    assert not dynamodb_store.put_daily_universe(universe, expected_epoch=started.execution_epoch)
    assert dynamodb_store.get_daily_universe(date(2026, 9, 3)) == universe
    running = dynamodb_store.transition_agent_mode(
        AgentMode.RUNNING,
        reason="integration resume",
        record=DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="resume"),
    )
    plan = OrderPlan(
        client_order_id="cr-integration",
        intent_id="intent-integration",
        symbol="AAPL",
        side=Side.BUY,
        quantity=1,
        limit_price=Decimal("100"),
        stop_price=Decimal("98"),
        take_profit_price=Decimal("104"),
        risk_amount=Decimal("2"),
        exposure_group="us-equity:long",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    prepared = OrderExecution(plan=plan, request_hash="a" * 64)
    assert dynamodb_store.claim_order(
        prepared, expected_epoch=running.execution_epoch, max_active_orders=1
    )
    assert not dynamodb_store.claim_order(
        prepared, expected_epoch=running.execution_epoch, max_active_orders=1
    )
    unknown = prepared.model_copy(update={"status": OrderExecutionStatus.UNKNOWN, "version": 1})
    assert (
        dynamodb_store.update_order(unknown, expected_status=OrderExecutionStatus.PREPARED).status
        is OrderExecutionStatus.UNKNOWN
    )
    assert dynamodb_store.get_order(plan.client_order_id) == unknown

    record = DecisionRecord.create(
        decision_type="NO_TRADE",
        route=Route.NO_TRADE,
        summary="private quality detail",
        public=True,
        public_summary="quality veto",
    )
    dynamodb_store.append_decision(record)
    assert dynamodb_store.list_public_decisions() == [
        record.public_projection(),
        reconciliation.public_projection(),
    ]
    assert dynamodb_store.list_public_routes() == [record.public_projection()]

    with pytest.raises(ClientError):
        dynamodb_store.append_decision(record)

    once = DecisionRecord.create(decision_type="CHALLENGER_PREDICTION", summary="shadow")
    epoch = dynamodb_store.get_agent_state().execution_epoch
    assert dynamodb_store.append_decision_once(once, expected_epoch=epoch)
    assert not dynamodb_store.append_decision_once(once, expected_epoch=epoch)
    assert dynamodb_store.claim_event_extraction(
        "alpaca:42", "model-1", "event-v1", expected_epoch=epoch
    )
    assert not dynamodb_store.claim_event_extraction(
        "alpaca:42", "model-1", "event-v1", expected_epoch=epoch
    )
    dynamodb_store.release_event_extraction(
        "alpaca:42", "model-1", "event-v1", expected_epoch=epoch
    )
    assert dynamodb_store.claim_event_extraction(
        "alpaca:42", "model-1", "event-v1", expected_epoch=epoch
    )
    dynamodb_store.begin_execution()
    with pytest.raises(RuntimeError, match="lost execution epoch"):
        dynamodb_store.append_decision_once(
            DecisionRecord.create(decision_type="CHALLENGER_PREDICTION", summary="stale"),
            expected_epoch=epoch,
        )


@pytest.mark.integration
def test_dynamodb_store_leases_concurrent_orders_up_to_the_bound(
    dynamodb_store: DynamoOperationalStore,
) -> None:
    dynamodb_store.initialize()
    started = dynamodb_store.begin_execution()
    dynamodb_store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    running = dynamodb_store.transition_agent_mode(
        AgentMode.RUNNING,
        reason="integration resume",
        record=DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="resume"),
    )
    epoch = running.execution_epoch

    def prepared(symbol: str) -> OrderExecution:
        return OrderExecution(
            plan=OrderPlan(
                client_order_id=f"cr-{symbol.lower()}",
                intent_id=f"intent-{symbol.lower()}",
                symbol=symbol,
                side=Side.BUY,
                quantity=1,
                limit_price=Decimal("100"),
                stop_price=Decimal("98"),
                take_profit_price=Decimal("104"),
                risk_amount=Decimal("2"),
                exposure_group="us-equity:long",
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            request_hash="b" * 64,
        )

    assert dynamodb_store.claim_order(prepared("AAPL"), expected_epoch=epoch, max_active_orders=2)
    assert dynamodb_store.claim_order(prepared("MSFT"), expected_epoch=epoch, max_active_orders=2)
    assert dynamodb_store.get_agent_state().active_order_ids == ("cr-aapl", "cr-msft")

    with pytest.raises(RuntimeError, match="not authorized for new exposure"):
        dynamodb_store.claim_order(prepared("NVDA"), expected_epoch=epoch, max_active_orders=2)

    dynamodb_store.clear_active_order("cr-aapl")
    assert dynamodb_store.get_agent_state().active_order_ids == ("cr-msft",)
    assert dynamodb_store.claim_order(prepared("NVDA"), expected_epoch=epoch, max_active_orders=2)
    assert dynamodb_store.get_agent_state().active_order_ids == ("cr-msft", "cr-nvda")
