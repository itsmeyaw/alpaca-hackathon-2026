from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from catalyst_router.domain import (
    AgentMode,
    AgentState,
    DecisionRecord,
    OrderExecution,
    OrderExecutionStatus,
    PublicDecisionRecord,
)


class InMemoryOperationalStore:
    def __init__(self, public_delay_seconds: int = 0) -> None:
        self._lock = RLock()
        self._state: AgentState | None = None
        self._decisions: dict[str, DecisionRecord] = {}
        self._orders: dict[str, OrderExecution] = {}
        self._public_delay = timedelta(seconds=public_delay_seconds)

    def initialize(self) -> AgentState:
        with self._lock:
            if self._state is None:
                self._state = AgentState()
            return self._state

    def get_agent_state(self) -> AgentState:
        with self._lock:
            return self.initialize()

    def begin_execution(self) -> AgentState:
        with self._lock:
            state = self.initialize()
            self._state = state.model_copy(
                update={
                    "version": state.version + 1,
                    "execution_epoch": str(uuid4()),
                    "reconciled_epoch": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            return self._state

    def transition_agent_mode(
        self, mode: AgentMode, *, reason: str, record: DecisionRecord
    ) -> AgentState:
        with self._lock:
            state = self.initialize()
            if state.mode is AgentMode.KILLED:
                raise RuntimeError("KILLED agent mode is terminal")
            if mode is AgentMode.RUNNING and not state.is_reconciled:
                raise RuntimeError("startup reconciliation is required before RUNNING")
            if record.decision_id in self._decisions:
                raise ValueError(f"decision already exists: {record.decision_id}")
            self._state = state.model_copy(
                update={
                    "mode": mode,
                    "reason": reason,
                    "version": state.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._decisions[record.decision_id] = record
            return self._state

    def commit_reconciliation(
        self, epoch: str, record: DecisionRecord, *, equity: Decimal | None = None
    ) -> AgentState:
        with self._lock:
            state = self.initialize()
            if state.execution_epoch != epoch:
                raise RuntimeError("execution epoch changed during reconciliation")
            if record.decision_id in self._decisions:
                raise ValueError(f"decision already exists: {record.decision_id}")
            self._state = state.model_copy(
                update={
                    "version": state.version + 1,
                    "reconciled_epoch": epoch,
                    "equity_peak": (
                        max(state.equity_peak or equity, equity)
                        if equity is not None
                        else state.equity_peak
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            self._decisions[record.decision_id] = record
            return self._state

    def append_decision(self, record: DecisionRecord) -> None:
        with self._lock:
            if record.decision_id in self._decisions:
                raise ValueError(f"decision already exists: {record.decision_id}")
            self._decisions[record.decision_id] = record

    def append_decision_once(
        self, record: DecisionRecord, *, expected_epoch: str | None = None
    ) -> bool:
        with self._lock:
            state = self.initialize()
            if expected_epoch is not None and (
                state.execution_epoch != expected_epoch or not state.is_reconciled
            ):
                raise RuntimeError("worker lost execution epoch ownership")
            if record.decision_id in self._decisions:
                return False
            self._decisions[record.decision_id] = record
            return True

    def claim_order(self, execution: OrderExecution, *, expected_epoch: str) -> bool:
        with self._lock:
            state = self.initialize()
            client_order_id = execution.plan.client_order_id
            if (
                state.mode is not AgentMode.RUNNING
                or state.execution_epoch != expected_epoch
                or not state.is_reconciled
            ):
                raise RuntimeError("agent is not authorized for new exposure")
            existing = self._orders.get(client_order_id)
            if existing is not None:
                if (
                    existing.status in {OrderExecutionStatus.PREPARED, OrderExecutionStatus.UNKNOWN}
                    and state.active_order_id != client_order_id
                ):
                    raise RuntimeError("agent is not authorized for order recovery")
                return False
            if state.active_order_id is not None:
                raise RuntimeError("agent is not authorized for new exposure")
            self._state = state.model_copy(
                update={
                    "active_order_id": client_order_id,
                    "version": state.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._orders[client_order_id] = execution
            return True

    def get_order(self, client_order_id: str) -> OrderExecution | None:
        with self._lock:
            return self._orders.get(client_order_id)

    def clear_active_order(self, client_order_id: str) -> AgentState:
        with self._lock:
            state = self.initialize()
            if state.active_order_id != client_order_id:
                raise RuntimeError("active order changed concurrently")
            self._state = state.model_copy(
                update={
                    "active_order_id": None,
                    "version": state.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            return self._state

    def update_equity_peak(self, equity: Decimal) -> AgentState:
        with self._lock:
            state = self.initialize()
            if state.equity_peak is not None and equity <= state.equity_peak:
                return state
            self._state = state.model_copy(
                update={
                    "equity_peak": equity,
                    "version": state.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            return self._state

    def update_order(
        self,
        execution: OrderExecution,
        *,
        expected_status: OrderExecutionStatus,
    ) -> OrderExecution:
        with self._lock:
            client_order_id = execution.plan.client_order_id
            current = self._orders.get(client_order_id)
            if current is None:
                raise RuntimeError("order execution does not exist")
            if current.status is not expected_status or execution.version != current.version + 1:
                raise RuntimeError("order execution changed concurrently")
            self._orders[client_order_id] = execution
            return execution

    def list_public_decisions(self, limit: int = 50) -> list[PublicDecisionRecord]:
        with self._lock:
            now = datetime.now(UTC)
            records = [
                record
                for record in self._decisions.values()
                if record.public and record.occurred_at + self._public_delay <= now
            ]
            selected = sorted(records, key=lambda record: record.occurred_at, reverse=True)[:limit]
            return [record.public_projection() for record in selected]
