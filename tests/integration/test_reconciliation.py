from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.domain import (
    AccountSnapshot,
    AgentState,
    DecisionRecord,
    MarketClockSnapshot,
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


class FailingCommitStore(InMemoryOperationalStore):
    def commit_reconciliation(self, epoch: str, record: DecisionRecord) -> AgentState:
        raise RuntimeError("conditional write failed")


def test_reconciliation_fences_execution_and_records_result() -> None:
    store = InMemoryOperationalStore()
    service = ReconciliationService(broker=FakePaperBroker(), store=store)

    snapshot = service.reconcile()

    assert snapshot.account.options_trading_level == 3
    assert store.get_agent_state().is_reconciled
    assert store.list_public_decisions()[0].decision_type == "RECONCILIATION_COMPLETED"


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
