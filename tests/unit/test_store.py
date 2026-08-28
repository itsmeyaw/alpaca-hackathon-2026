from datetime import UTC, datetime, timedelta

import pytest

from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.domain import AgentMode, DecisionRecord


def test_store_defaults_to_paused_and_restart_epoch_requires_reconciliation() -> None:
    store = InMemoryOperationalStore()

    initial = store.initialize()
    started = store.begin_execution()

    assert initial.mode is AgentMode.PAUSED
    assert not started.is_reconciled
    record = DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok")
    assert store.commit_reconciliation(started.execution_epoch, record).is_reconciled


def test_decisions_are_immutable_and_publicly_filterable() -> None:
    store = InMemoryOperationalStore()
    public = DecisionRecord.create(
        decision_type="NO_TRADE",
        summary="private stale quote detail",
        public=True,
        public_summary="stale quote",
    )
    private = DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="paused", public=False)

    store.append_decision(public)
    store.append_decision(private)

    assert store.list_public_decisions() == [public.public_projection()]
    with pytest.raises(ValueError, match="decision already exists"):
        store.append_decision(public)


def test_publication_delay_hides_recent_decisions() -> None:
    store = InMemoryOperationalStore(public_delay_seconds=900)
    recent = DecisionRecord.create(
        decision_type="NO_TRADE",
        summary="private recent detail",
        public=True,
        public_summary="recent",
    )
    old = DecisionRecord.create(
        decision_type="NO_TRADE",
        summary="old",
        occurred_at=datetime.now(UTC) - timedelta(minutes=16),
        public=True,
        public_summary="old",
    )

    store.append_decision(recent)
    store.append_decision(old)

    assert store.list_public_decisions() == [old.public_projection()]


def test_public_projection_excludes_internal_payload() -> None:
    record = DecisionRecord.create(
        decision_type="NO_TRADE",
        summary="vetoed",
        payload={"raw_prompt": "private", "account_id": "private"},
        public=True,
        public_summary="sanitized veto",
    )

    projection = record.public_projection_json()

    assert "raw_prompt" not in projection
    assert "account_id" not in projection
    assert "sanitized veto" in projection


def test_idempotent_decision_write_is_fenced_by_execution_epoch() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    record = DecisionRecord.create(decision_type="CHALLENGER_PREDICTION", summary="shadow")

    assert store.append_decision_once(record, expected_epoch=started.execution_epoch)
    store.begin_execution()

    with pytest.raises(RuntimeError, match="lost execution epoch"):
        store.append_decision_once(
            DecisionRecord.create(decision_type="CHALLENGER_PREDICTION", summary="stale"),
            expected_epoch=started.execution_epoch,
        )


def test_mode_transitions_are_reconciled_audited_and_kill_is_terminal() -> None:
    store = InMemoryOperationalStore()

    with pytest.raises(RuntimeError, match="reconciliation"):
        store.transition_agent_mode(
            AgentMode.RUNNING,
            reason="unsafe resume",
            record=DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="resume"),
        )

    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    running = store.transition_agent_mode(
        AgentMode.RUNNING,
        reason="operator resume",
        record=DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="resume"),
    )
    killed = store.transition_agent_mode(
        AgentMode.KILLED,
        reason="operator kill",
        record=DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="kill"),
    )

    assert running.mode is AgentMode.RUNNING
    assert killed.mode is AgentMode.KILLED
    with pytest.raises(RuntimeError, match="terminal"):
        store.transition_agent_mode(
            AgentMode.PAUSED,
            reason="cannot revive",
            record=DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="pause"),
        )
