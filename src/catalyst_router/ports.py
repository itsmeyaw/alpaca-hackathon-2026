from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from catalyst_router.domain import (
    AgentMode,
    AgentState,
    BrokerOrderSnapshot,
    DecisionRecord,
    OrderExecution,
    OrderExecutionStatus,
    OrderPlan,
    PublicDecisionRecord,
    ReconciliationSnapshot,
)


class PaperBroker(Protocol):
    def reconciliation_snapshot(self) -> ReconciliationSnapshot: ...

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None: ...

    def submit_order(self, plan: OrderPlan) -> BrokerOrderSnapshot: ...

    def flatten(self) -> None: ...


class OperationalStore(Protocol):
    def initialize(self) -> AgentState: ...

    def get_agent_state(self) -> AgentState: ...

    def begin_execution(self) -> AgentState: ...

    def transition_agent_mode(
        self, mode: AgentMode, *, reason: str, record: DecisionRecord
    ) -> AgentState: ...

    def commit_reconciliation(
        self, epoch: str, record: DecisionRecord, *, equity: Decimal | None = None
    ) -> AgentState: ...

    def append_decision(self, record: DecisionRecord) -> None: ...

    def append_decision_once(
        self, record: DecisionRecord, *, expected_epoch: str | None = None
    ) -> bool: ...

    def claim_order(self, execution: OrderExecution, *, expected_epoch: str) -> bool: ...

    def get_order(self, client_order_id: str) -> OrderExecution | None: ...

    def clear_active_order(self, client_order_id: str) -> AgentState: ...

    def update_equity_peak(self, equity: Decimal) -> AgentState: ...

    def update_order(
        self,
        execution: OrderExecution,
        *,
        expected_status: OrderExecutionStatus,
    ) -> OrderExecution: ...

    def list_public_decisions(self, limit: int = 50) -> list[PublicDecisionRecord]: ...
