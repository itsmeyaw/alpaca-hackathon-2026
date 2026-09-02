from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.domain import (
    AccountSnapshot,
    AgentState,
    BrokerOrderSnapshot,
    DecisionRecord,
    MarketClockSnapshot,
    OrderPlan,
    PublicPortfolioPoint,
    ReconciliationSnapshot,
)
from catalyst_router.service import ReconciliationService


class FakePaperBroker:
    def __init__(self, *, trading_blocked: bool = False) -> None:
        self._trading_blocked = trading_blocked

    def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        now = datetime.now(UTC)
        return ReconciliationSnapshot(
            account=AccountSnapshot(
                equity=Decimal("100000"),
                buying_power=Decimal("200000"),
                cash=Decimal("100000"),
                portfolio_value=Decimal("100000"),
                trading_blocked=self._trading_blocked,
                options_trading_level=3,
            ),
            clock=MarketClockSnapshot(
                is_open=True,
                timestamp=now,
                next_open=now,
                next_close=now + timedelta(hours=6),
            ),
            positions=(),
            open_orders=(),
            captured_at=now,
        )

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        del client_order_id
        return None

    def submit_order(self, plan: OrderPlan) -> BrokerOrderSnapshot:
        del plan
        raise AssertionError("reconciliation must not submit orders")

    def close_position(self, symbol: str) -> None:
        del symbol
        raise AssertionError("reconciliation must not close positions")

    def flatten(self) -> None:
        raise AssertionError("reconciliation must not flatten a healthy account")


class FailingCommitStore(InMemoryOperationalStore):
    def commit_reconciliation(
        self, epoch: str, record: DecisionRecord, *, equity: Decimal | None = None
    ) -> AgentState:
        del epoch, record, equity
        raise RuntimeError("conditional write failed")


class FailingProjectionStore(InMemoryOperationalStore):
    def append_public_portfolio(self, point: PublicPortfolioPoint, *, expected_epoch: str) -> bool:
        del point, expected_epoch
        raise OSError("reporting write failed")


def test_reconciliation_fences_execution_and_records_result() -> None:
    store = InMemoryOperationalStore()
    service = ReconciliationService(broker=FakePaperBroker(), store=store)

    snapshot = service.reconcile()

    assert snapshot.account.options_trading_level == 3
    assert store.get_agent_state().is_reconciled
    assert store.get_agent_state().equity_peak == Decimal("100000")
    assert store.get_agent_state().competition_start_equity == Decimal("100000")
    assert store.list_public_decisions()[0].decision_type == "RECONCILIATION_COMPLETED"
    portfolio = store.list_public_portfolio()[0]
    assert portfolio.equity == Decimal("100000")
    assert portfolio.net_pnl == Decimal("0")
    assert portfolio.position_count == 0


def test_blocked_account_remains_unreconciled_and_unpublished() -> None:
    store = InMemoryOperationalStore()
    service = ReconciliationService(broker=FakePaperBroker(trading_blocked=True), store=store)

    with pytest.raises(RuntimeError, match="blocked"):
        service.reconcile()

    assert not store.get_agent_state().is_reconciled
    assert store.list_public_decisions() == []


def test_failed_commit_does_not_publish_reconciliation() -> None:
    store = FailingCommitStore()
    service = ReconciliationService(broker=FakePaperBroker(), store=store)

    with pytest.raises(RuntimeError, match="conditional write"):
        service.reconcile()

    assert not store.get_agent_state().is_reconciled
    assert store.list_public_decisions() == []


def test_reporting_failure_does_not_invalidate_reconciliation() -> None:
    store = FailingProjectionStore()

    ReconciliationService(broker=FakePaperBroker(), store=store).reconcile()

    assert store.get_agent_state().is_reconciled
