from __future__ import annotations

from datetime import UTC, datetime, timedelta

from catalyst_router.domain import DecisionRecord, ReconciliationSnapshot
from catalyst_router.ports import OperationalStore, PaperBroker


class ReconciliationService:
    def __init__(self, *, broker: PaperBroker, store: OperationalStore) -> None:
        self._broker = broker
        self._store = store

    def reconcile(self) -> ReconciliationSnapshot:
        state = self._store.begin_execution()
        snapshot = self._broker.reconciliation_snapshot()
        self._validate_snapshot(snapshot)
        record = DecisionRecord.create(
            decision_type="RECONCILIATION_COMPLETED",
            summary=(
                f"Reconciled {len(snapshot.positions)} positions and "
                f"{len(snapshot.open_orders)} open orders"
            ),
            payload={
                "market_is_open": snapshot.clock.is_open,
                "position_count": len(snapshot.positions),
                "open_order_count": len(snapshot.open_orders),
                "options_trading_level": snapshot.account.options_trading_level,
            },
            public=True,
            public_summary=(
                f"Reconciled {len(snapshot.positions)} positions and "
                f"{len(snapshot.open_orders)} open orders"
            ),
        )
        self._store.commit_reconciliation(state.execution_epoch, record)
        return snapshot

    @staticmethod
    def _validate_snapshot(snapshot: ReconciliationSnapshot) -> None:
        now = datetime.now(UTC)
        if snapshot.account.trading_blocked:
            raise RuntimeError("Alpaca account is blocked from trading")
        if snapshot.account.equity <= 0 or snapshot.account.portfolio_value <= 0:
            raise RuntimeError("Alpaca account values are invalid")
        if not now - timedelta(seconds=30) <= snapshot.captured_at <= now + timedelta(seconds=5):
            raise RuntimeError("Alpaca reconciliation snapshot is stale")
        if len(snapshot.open_orders) >= 500:
            raise RuntimeError("open-order response may be truncated")
