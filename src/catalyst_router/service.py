from __future__ import annotations

from datetime import UTC, datetime, timedelta

from catalyst_router.domain import (
    AgentMode,
    DecisionRecord,
    OrderExecutionStatus,
    ReconciliationSnapshot,
)
from catalyst_router.ports import OperationalStore, PaperBroker


class ReconciliationService:
    def __init__(self, *, broker: PaperBroker, store: OperationalStore) -> None:
        self._broker = broker
        self._store = store

    def reconcile(self) -> ReconciliationSnapshot:
        state = self._store.begin_execution()
        snapshot = self._broker.reconciliation_snapshot()
        self.validate_snapshot(snapshot)
        self._reconcile_active_order(snapshot)
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
        self._store.commit_reconciliation(
            state.execution_epoch, record, equity=snapshot.account.equity
        )
        return snapshot

    def _reconcile_active_order(self, snapshot: ReconciliationSnapshot) -> None:
        state = self._store.get_agent_state()
        client_order_id = state.active_order_id
        if client_order_id is None:
            return
        execution = self._store.get_order(client_order_id)
        if execution is None:
            raise RuntimeError("active order has no durable execution record")
        symbol = execution.plan.symbol
        symbol_is_flat = not any(
            position.symbol == symbol for position in snapshot.positions
        ) and not any(order.symbol == symbol for order in snapshot.open_orders)
        broker_order = self._broker.get_order_by_client_id(client_order_id)
        if broker_order is None:
            if symbol_is_flat and snapshot.clock.timestamp >= execution.plan.expires_at:
                self._store.clear_active_order(client_order_id)
                return
            if state.mode is AgentMode.RUNNING and execution.status in {
                OrderExecutionStatus.PREPARED,
                OrderExecutionStatus.UNKNOWN,
            }:
                self._store.transition_agent_mode(
                    AgentMode.RISK_HALTED,
                    reason="ambiguous order was not found during reconciliation",
                    record=DecisionRecord.create(
                        decision_type="ORDER_RECONCILIATION_HALTED",
                        summary=f"could not resolve {client_order_id}",
                    ),
                )
            return
        plan = execution.plan
        if (
            broker_order.symbol != plan.symbol
            or broker_order.side is not plan.side
            or broker_order.quantity != plan.quantity
        ):
            raise RuntimeError("broker order does not match its durable order plan")
        status = broker_order.status.rsplit(".", 1)[-1].lower()
        rejected = status in {"canceled", "expired", "rejected", "replaced"}
        target = OrderExecutionStatus.REJECTED if rejected else OrderExecutionStatus.ACKNOWLEDGED
        if execution.status is not target:
            previous_status = execution.status
            execution = execution.model_copy(
                update={
                    "status": target,
                    "version": execution.version + 1,
                    "alpaca_order_id": broker_order.order_id,
                    "broker_status": broker_order.status,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._store.update_order(execution, expected_status=previous_status)
        if symbol_is_flat and (rejected or status == "filled"):
            self._store.clear_active_order(client_order_id)

    @staticmethod
    def validate_snapshot(snapshot: ReconciliationSnapshot) -> None:
        now = datetime.now(UTC)
        if snapshot.account.trading_blocked:
            raise RuntimeError("Alpaca account is blocked from trading")
        if snapshot.account.equity <= 0 or snapshot.account.portfolio_value <= 0:
            raise RuntimeError("Alpaca account values are invalid")
        if not now - timedelta(seconds=30) <= snapshot.captured_at <= now + timedelta(seconds=5):
            raise RuntimeError("Alpaca reconciliation snapshot is stale")
        if len(snapshot.open_orders) >= 500:
            raise RuntimeError("open-order response may be truncated")
